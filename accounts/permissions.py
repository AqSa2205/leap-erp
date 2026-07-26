"""Capability registry and enforcement helpers.

Capabilities are declared HERE in code (a capability only means something if a
code path checks it). Only the per-role on/off *grants* live in the database
(`accounts.models.RolePermission`). The super-admin grid renders this registry
crossed with the roles.
"""
from dataclasses import dataclass
from functools import wraps
from django.core.exceptions import PermissionDenied


@dataclass(frozen=True)
class Capability:
    codename: str   # 'costing.access'
    module: str     # display group, e.g. 'Costing'
    action: str     # 'access' | 'nav' | 'view' | 'create' | 'edit' | 'delete' | 'export' | 'approve'
    label: str      # human label for the grid cell/row
    enforced: bool  # True = a code path reads this today; False = defined, wiring pending
    order: int = 0


def _module(key, label, *, granular=()):
    """Build access + nav (enforced) plus optional granular (not-yet-enforced) caps."""
    caps = [
        Capability(f'{key}.access', label, 'access', f'Open {label}', enforced=True, order=0),
        Capability(f'{key}.nav', label, 'nav', f'Show {label} in sidebar', enforced=True, order=1),
    ]
    for i, (action, lbl) in enumerate(granular, start=2):
        caps.append(Capability(f'{key}.{action}', label, action, lbl, enforced=False, order=i))
    return caps


CAPABILITIES = [
    *_module('dashboard', 'Dashboard'),
    *_module('pipeline', 'Commercial Pipeline'),
    *_module('costing', 'Costing', granular=[
        ('view', 'View pricing'), ('create', 'Create sheets'), ('edit', 'Edit sheets'),
        ('delete', 'Delete sheets/items'), ('export', 'Export PDF'), ('approve', 'Approve / release'),
    ]),
    *_module('procurement', 'Procurement'),
    *_module('po', 'Purchase Orders', granular=[
        ('create', 'Create PO'), ('edit', 'Edit PO'), ('delete', 'Delete PO'),
        ('export', 'Export PO'), ('approve', 'Approve PO'),
    ]),
    *_module('dn', 'Delivery Notes', granular=[
        ('create', 'Create DN'), ('edit', 'Edit DN'), ('delete', 'Delete DN'), ('export', 'Export DN'),
    ]),
    *_module('settings', 'Admin / Settings'),
    # Dev Tracking: distinct enforced caps for admin-management vs a developer's
    # own task view. These are read by nav (`can` filter) and by Task 3/4 views,
    # so they are enforced=True (unlike the not-yet-wired granular caps above).
    *_module('devtracking', 'Dev Tracking'),
    Capability('devtracking.admin', 'Dev Tracking', 'admin',
               'Manage dev tasks (admin)', enforced=True, order=2),
    Capability('devtracking.mywork', 'Dev Tracking', 'mywork',
               'See my assigned dev tasks', enforced=True, order=3),
    # Department KPIs: a read dashboard (access/nav) plus a manage cap that gates
    # entering manual values + targets. All enforced (read by nav + the views).
    *_module('kpis', 'Department KPIs'),
    Capability('kpis.manage', 'Department KPIs', 'manage',
               'Enter KPI values & targets', enforced=True, order=2),
    Capability('kpis.activity', 'Department KPIs', 'activity',
               'View team activity review', enforced=True, order=3),

    *_module('timesheets', 'Timesheets'),
    Capability('timesheets.review', 'Timesheets', 'review',
               'Review & reopen employee timesheets (HR)', enforced=True, order=2),           
]


def capability_codenames():
    return {c.codename for c in CAPABILITIES}


def landing_url_for(user):
    """Best post-login landing page for a user, based on what they can reach.

    The main dashboard is gated by `dashboard.access`, so siloed roles (e.g. the
    AI team, who only have Dev Tracking) would 403 if dumped there. Route them to
    a page they can actually open instead.
    """
    from django.urls import reverse
    if user.has_capability('dashboard.access'):
        return reverse('dashboard:index')
    if user.has_capability('devtracking.admin'):
        return reverse('devtracking:dashboard')
    if user.has_capability('devtracking.mywork'):
        return reverse('devtracking:my_tasks')
    return reverse('dashboard:my_work')  # ungated fallback


def default_modules_by_role():
    """{role_name: [department/module labels granted by default]}.

    Used on the user form to show, at a glance, which departments a role gives
    access to — access stays role-driven; this just makes it visible.
    """
    key_label = {}
    for c in CAPABILITIES:
        key = c.codename.rsplit('.', 1)[0]
        key_label.setdefault(key, c.module)
    return {
        role_name: sorted({key_label.get(m, m) for m in modules})
        for role_name, modules in DEFAULT_MODULE_ACCESS.items()
    }


def capabilities_by_module():
    """Ordered {module_label: [Capability, ...]} for rendering the grid."""
    out = {}
    for c in CAPABILITIES:
        out.setdefault(c.module, []).append(c)
    for caps in out.values():
        caps.sort(key=lambda c: c.order)
    return out


def require_capability(codename):
    """Decorator for function views: 403 unless the user holds `codename`."""
    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            user = getattr(request, 'user', None)
            if user is None or not user.is_authenticated or not user.has_capability(codename):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return _wrapped
    return decorator


class CapabilityRequiredMixin:
    """Class-based-view mixin. Set `capability = '<codename>'`."""
    capability = None

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated or not user.has_capability(self.capability):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


# Module access baseline = TODAY'S behavior, exactly (zero regression on launch).
# Every list page (dashboard, pipeline, costing, procurement, po, dn) currently
# has `test_func: return True` / only `@login_required`, so any authenticated
# user can open them today — data is scoped inside, but the page opens. We seed
# all of those ON for every role so nobody loses access on deploy; the super
# admin then tightens from the grid. Only the Users/Settings page is genuinely
# restricted today (AdminRequiredMixin = super_admin only), so `settings` is
# seeded for super_admin alone. Granular caps (enforced=False) stay OFF.
# Note: finance already has `pipeline` here (the earlier "deliberate change"),
# which is now subsumed by the match-today baseline.
_OPEN_TO_ALL = {'dashboard', 'pipeline', 'costing', 'procurement', 'po', 'dn','timesheets'}
DEFAULT_MODULE_ACCESS = {
    # Department KPIs: restricted to super_admin only (the whole KPI section —
    # dashboard, by-person, and data entry). Super admin can widen later from
    # the permission grid if management wants dept heads to see it.
    'super_admin':     _OPEN_TO_ALL | {'settings', 'devtracking', 'kpis'},
    'admin':           _OPEN_TO_ALL | {'devtracking'},
    'manager':         set(_OPEN_TO_ALL),
    'sales_rep':       set(_OPEN_TO_ALL),
    'procurement_mgr': set(_OPEN_TO_ALL),
    'procurement_off': set(_OPEN_TO_ALL),
    'proposal_head':   set(_OPEN_TO_ALL),
    'proposal_rep':    set(_OPEN_TO_ALL),
    'finance_head':    set(_OPEN_TO_ALL),
    'finance_manager': set(_OPEN_TO_ALL),
    'finance_rep':     set(_OPEN_TO_ALL),
    # ── AI team: siloed to Dev Tracking only (NOT the open baseline) ──
    # The doers see just "My Work" (ungated) + notifications (ungated) + their
    # own task view (devtracking.mywork, granted below) — no Costing/Pipeline/
    # Procurement/PO/DN nav. AI Head additionally manages Dev Tracking, so keeps
    # the devtracking module (admin codename granted below). Later we can widen
    # the doers to their own assets/attendance/leave.
    'developer':          set(),
    'ai_head':            {'devtracking'},
    'ai_intern':          set(),
    'ai_engineer':        set(),
    'ai_junior_engineer': set(),
    # ── HR/administration authorization roles ──
    # PM / Site Manager / Document Controller: a Dashboard landing page plus the
    # Department KPIs module (scoped to their own team inside the KPI views).
    # Their other features (attendance/leave/assets/exceptions/org chart) are
    # role-gated in the HR app, not capability-gated, so they need no module here.
    'project_manager':     {'dashboard', 'kpis'},
    'site_manager':        {'dashboard', 'kpis'},
    'document_controller': {'dashboard', 'kpis'},
    # ERP Admin: a Dashboard landing page; the Administration section it manages
    # is role-gated (is_erp_admin_user), not capability-gated.
    'erp_admin':           {'dashboard'},
}

# Per-codename baseline for ENFORCED granular caps that are not plain
# `access`/`nav` (those are handled by DEFAULT_MODULE_ACCESS above). Maps
# role.name -> set of exact codenames seeded ON. The system is per-codename
# granular (User.has_capability checks the exact codename), so these are real,
# independently-toggleable capabilities.
DEFAULT_CODENAME_GRANTS = {
    'super_admin':  {'devtracking.admin', 'devtracking.mywork', 'kpis.manage', 'kpis.activity','timesheets.review'},
    'admin':        {'devtracking.admin', 'devtracking.mywork','timesheets.review'},
    'developer':    {'devtracking.mywork'},
    'ai_head':            {'devtracking.admin', 'devtracking.mywork'},
    'ai_intern':          {'devtracking.mywork'},
    'ai_engineer':        {'devtracking.mywork'},
    'ai_junior_engineer': {'devtracking.mywork'},
    # Team-scoped HR roles get the per-person KPI scorecards + activity review
    # for their own reports (kpis.activity). They deliberately do NOT get
    # kpis.manage — entering department KPI targets/values is a company-level
    # admin action, not a team-scoped one. The KPI views restrict per-person
    # data to their reports (see kpis/views.py).
    'project_manager':     {'kpis.activity'},
    'site_manager':        {'kpis.activity'},
    'document_controller': {'kpis.activity'},
}


def seed_default_permissions():
    """Create a RolePermission row for every (role x capability), set to the
    baseline above. Idempotent: existing rows are left as the admin set them;
    only missing rows are created. Safe to call from a data migration and tests.
    """
    from accounts.models import Role, RolePermission
    access_actions = {'access', 'nav'}
    for role in Role.objects.all():
        modules_on = DEFAULT_MODULE_ACCESS.get(role.name, set())
        codenames_on = DEFAULT_CODENAME_GRANTS.get(role.name, set())
        for cap in CAPABILITIES:
            module_key = cap.codename.rsplit('.', 1)[0]
            default_allowed = (
                (cap.action in access_actions and module_key in modules_on)
                or cap.codename in codenames_on
            )
            RolePermission.objects.get_or_create(
                role=role, codename=cap.codename,
                defaults={'allowed': default_allowed},
            )
