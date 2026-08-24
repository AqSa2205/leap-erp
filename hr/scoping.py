"""Team-scoping for the HR/administration authorization roles.

The four roles added for HR delegation split into two visibility tiers:

* **ERP Admin** — company-wide, exactly like ``admin``/``super_admin``.
* **Project Manager / Document Controller** — their *whole* org-chart subtree
  (direct reports, their reports, and so on).
* **Site Manager** — their *direct* reports only ("his team, no one else").

Scope is resolved off the login user's linked ``Employee`` record via the
existing reporting hierarchy (``main_manager``/``main_reports``); there is no
separate Site/Department model. See ``Employee.get_downstream_employee_ids``.
"""


def scoped_employee_ids(user):
    """Return the set of ``Employee`` ids ``user`` may see in HR features, or
    ``None`` meaning *unrestricted* (all employees).

    ``None`` is deliberately distinct from an empty set: unrestricted vs. "may
    see nobody". Callers filter like::

        ids = scoped_employee_ids(request.user)
        qs = Employee.objects.all() if ids is None else Employee.objects.filter(pk__in=ids)

    A user's own record is NOT included — their personal attendance/leave lives
    in My Profile. This returns the people they *manage*.
    """
    # Company-wide tiers: super admin, admin, and ERP Admin see everyone.
    if user.is_super_admin_user or user.is_admin_user or user.is_erp_admin_user:
        return None

    emp = getattr(user, 'employee_profile', None)
    if emp is None:
        # A scoped role with no linked employee record manages nobody.
        return set()

    if user.is_site_manager_user:
        # Direct reports only.
        return set(emp.main_reports.values_list('pk', flat=True))

    if user.is_project_manager_user or user.is_document_controller_user:
        # Whole downstream subtree.
        return set(emp.get_downstream_employee_ids())

    # Any other role has no team scope here.
    return set()


def scope_employee_queryset(user, queryset):
    """Filter an ``Employee`` queryset to what ``user`` may see. Unrestricted
    users get the queryset back untouched."""
    ids = scoped_employee_ids(user)
    if ids is None:
        return queryset
    return queryset.filter(pk__in=ids)


def scope_asset_queryset(user, queryset):
    """Filter an ``Asset`` queryset to assets currently held by ``user``'s team
    (an open AssetAssignment to one of their reports). Unrestricted users get
    the queryset untouched. Team-scoped roles get read-only visibility into
    their team's equipment; inventory operations stay with the admin tiers."""
    ids = scoped_employee_ids(user)
    if ids is None:
        return queryset
    return queryset.filter(
        assignments__returned_at__isnull=True,
        assignments__employee_id__in=ids,
    ).distinct()


def can_manage_hr_scoped(user):
    """True for any role that reaches the (scoped or global) HR feature set:
    the team-scoped roles plus the company-wide admin tiers. Used as the access
    gate on the shared HR feature views (attendance/leave/assets/exceptions)."""
    # `admin` is deliberately absent: these HR screens sit inside the
    # Administration section, which admin is locked out of. Leaving it here
    # would keep the pages reachable by direct URL after the sidebar stopped
    # offering them — a hidden link is not an access control.
    return bool(
        user.is_super_admin_user
        or user.is_erp_admin_user
        or user.is_team_scoped_hr_user
    )
