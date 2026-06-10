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


# Module access baseline = today's behavior. Keys are role.name. The value is
# the set of MODULE keys whose `.access` + `.nav` are ON. Granular caps
# (enforced=False) are seeded OFF for everyone for now and toggled on later.
DEFAULT_MODULE_ACCESS = {
    'super_admin':     {'dashboard', 'pipeline', 'costing', 'procurement', 'po', 'dn', 'settings'},
    'admin':           {'dashboard', 'pipeline', 'costing'},
    'manager':         {'dashboard', 'pipeline', 'costing'},
    'sales_rep':       {'dashboard', 'pipeline', 'costing'},
    'procurement_mgr': {'dashboard', 'costing', 'procurement', 'po', 'dn'},
    'procurement_off': {'dashboard', 'costing', 'procurement', 'po', 'dn'},
    'proposal_head':   {'dashboard', 'pipeline', 'costing'},
    'proposal_rep':    {'dashboard', 'pipeline', 'costing'},
    # Deliberate launch change: finance gains the (region-scoped) pipeline view.
    'finance_head':    {'dashboard', 'pipeline', 'costing'},
    'finance_manager': {'dashboard', 'pipeline', 'costing'},
    'finance_rep':     {'dashboard', 'pipeline', 'costing'},
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
        for cap in CAPABILITIES:
            module_key = cap.codename.rsplit('.', 1)[0]
            default_allowed = cap.action in access_actions and module_key in modules_on
            RolePermission.objects.get_or_create(
                role=role, codename=cap.codename,
                defaults={'allowed': default_allowed},
            )
