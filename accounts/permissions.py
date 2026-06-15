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
]


def capability_codenames():
    return {c.codename for c in CAPABILITIES}


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
_OPEN_TO_ALL = {'dashboard', 'pipeline', 'costing', 'procurement', 'po', 'dn'}
DEFAULT_MODULE_ACCESS = {
    'super_admin':     _OPEN_TO_ALL | {'settings', 'devtracking'},
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
    # Developer: the match-today open baseline (every list page opens for every
    # role, data scoped inside — same as all other roles) plus the Dev Tracking
    # module (their own task view). Admin-vs-mywork is split per-codename below.
    'developer':       _OPEN_TO_ALL | {'devtracking'},
}

# Per-codename baseline for ENFORCED granular caps that are not plain
# `access`/`nav` (those are handled by DEFAULT_MODULE_ACCESS above). Maps
# role.name -> set of exact codenames seeded ON. The system is per-codename
# granular (User.has_capability checks the exact codename), so these are real,
# independently-toggleable capabilities.
DEFAULT_CODENAME_GRANTS = {
    'super_admin':  {'devtracking.admin', 'devtracking.mywork'},
    'admin':        {'devtracking.admin', 'devtracking.mywork'},
    'developer':    {'devtracking.mywork'},
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
