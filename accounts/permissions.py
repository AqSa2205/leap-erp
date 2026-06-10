"""Capability registry and enforcement helpers.

Capabilities are declared HERE in code (a capability only means something if a
code path checks it). Only the per-role on/off *grants* live in the database
(`accounts.models.RolePermission`). The super-admin grid renders this registry
crossed with the roles.
"""
from dataclasses import dataclass


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
