# Permission Matrix UI — Design Spec

**Date:** 2026-06-10
**Status:** Approved for planning
**Author:** Aqsa Ahmed (with Claude)

## Problem

Access control is hardcoded across the codebase as scattered role-name checks
(`user.is_finance_team_user`, `_user_can_edit_sheet`, queryset overrides in
`ProjectPermissionMixin`, `base.html` nav conditionals, etc.). Every time the
business wants to change "who can do what" — e.g. let finance see the
commercial pipeline, or stop a role from opening procurement — it requires a
code change, test, and deploy. As the app grows this does not scale.

We want a **super-admin-only UI** with toggles per feature per role, so
permissions become data the super admin edits, not code we keep changing.

## Key design tension (resolved)

Current permissions tangle two distinct concerns:

1. **"Can this role do action X at all?"** — a clean on/off (e.g. *can finance
   edit costing, can procurement create POs*). Perfect for toggles.
2. **"On which records?"** — region / workflow-stage / ownership scoping (e.g.
   *finance edits only in their region, only while the sheet is in
   `finance_review`*). This is conditional logic, not a checkbox.

**Decision:** the toggle grid governs concern #1 only. Concern #2 (region,
workflow stage, ownership) **stays in code**, layered on top: the capability
check answers "is this role allowed to do this action type," and existing code
still answers "on which objects." A toggle being ON does not bypass region or
stage rules.

## Decisions locked during brainstorming

- **Granularity:** action-level per module — for each role, capabilities for
  `view`/`access`, `create`, `edit`, `delete`, `export`, `approve` (per module,
  as applicable). Region/stage scoping stays in code.
- **Rollout:** the data model and the full super-admin grid ship complete in
  phase 1, but phase 1 only **enforces** the access + nav capabilities across
  the main modules. Granular cells (edit/create/delete/export/approve) appear
  in the grid but stay code-gated until later phases wire them module by module.
- **Home:** lives in the `accounts` app (where `Role` already lives); UI under
  `/settings/permissions/`.
- **Audit trail:** included (lightweight) — this is security-sensitive.

## Architecture

### 1. Capabilities in code, grants in the DB

A permission is meaningless unless some code path checks it, so **capabilities
are defined in code; only the on/off grants live in the database.**

**Code registry — `accounts/permissions.py`:**

```python
@dataclass(frozen=True)
class Capability:
    codename: str      # 'costing.access', 'po.create'
    module: str        # 'Costing', 'Purchase Orders'
    action: str        # 'access' | 'view' | 'create' | 'edit' | 'delete' | 'export' | 'approve' | 'nav'
    label: str         # human label for the grid
    enforced: bool     # True = code reads this today; False = defined, wiring pending
    order: int = 0

CAPABILITIES: list[Capability] = [ ... ]   # canonical, code-owned list
```

The registry is the single source of truth for what cells exist in the grid.
Adding a capability is a code change (because enforcing it is a code change).

**DB model — `accounts/models.py`:**

```python
class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='permissions')
    codename = models.CharField(max_length=64)   # matches a Capability.codename
    allowed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('role', 'codename')
        indexes = [models.Index(fields=['role', 'codename'])]
```

One row per (role × capability). The super-admin UI flips `allowed`. Orphan rows
(codename no longer in the registry) are ignored by lookups and can be pruned by
a management command.

**Audit — `accounts/models.py`:**

```python
class PermissionChangeLog(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    codename = models.CharField(max_length=64)
    allowed = models.BooleanField()        # new value
    created_at = models.DateTimeField(auto_now_add=True)
```

### 2. Enforcement surface

- **`User.has_capability(codename) -> bool`** (`accounts/models.py`):
  - `super_admin` → always `True` (lockout protection).
  - else look up `RolePermission(role=user.role, codename=...).allowed`.
  - no role, or no matching row → `False` (default deny; seeding guarantees rows
    exist so this only bites genuinely new capabilities).
  - Result cached on the request (`request._capability_cache`) so nav rendering
    does not issue N queries. A role's full grant set is one query, memoised.
- **`@require_capability('costing.access')`** — decorator for function views;
  returns 403 (`PermissionDenied`) when absent.
- **`CapabilityRequiredMixin`** — `capability = '...'`; for class-based views.
- **Template filter `can`** — `accounts/templatetags/perms.py`:
  `{% if user|can:'costing.access' %}` for `base.html` nav links.

Region/stage/ownership scoping is **untouched** — it executes after the
capability gate passes.

### 3. Super-admin UI — `/settings/permissions/`

- Reachable **only** by `super_admin`, gated by a hardcoded
  `is_super_admin_user` check — deliberately **not** capability-gated, so the
  permission page can never be toggled away or used to lock everyone out.
- Layout: accordion per module. Inside each module, a table whose rows are
  capabilities and columns are roles, with a checkbox per cell.
- The `super_admin` column renders checked-and-disabled (always all-on).
- Cells whose capability has `enforced=False` show a subtle "wiring pending"
  marker so a toggle is never mistaken for live.
- Toggling a cell saves via **AJAX per cell** (matching the existing inline-edit
  pattern in costing), writes/updates the `RolePermission` row, and appends a
  `PermissionChangeLog` entry.

### 4. Seeding — zero behavior change on launch

A data migration writes a `RolePermission` row for **every** (role × capability)
in the registry, with `allowed` set to reproduce **today's** behavior exactly,
derived from the current hardcoded logic. Examples:

| Role | pipeline.access | costing.access | procurement.access | po.access |
|---|---|---|---|---|
| sales_rep | on | on | off | off |
| finance_* | off* | on | off | off |
| procurement_* | off | on | on | on |
| admin/manager | on | on | off | off |

(*finance currently falls through to `owner=user` on the pipeline, i.e.
effectively no access — seeded `off`, matching reality.)

Result: the day it ships, nothing changes for anyone. The super admin then
adjusts from a known-good baseline.

### 5. Phase 1 enforced capabilities (access + nav)

Phase 1 defines capabilities for all main modules but only **enforces** the
`access` (module entry) and `nav` (sidebar link) capabilities, replacing the
existing entry gates and `base.html` conditionals:

- Dashboard
- Commercial Pipeline (projects)
- Costing
- Procurement
- Purchase Orders
- Delivery Notes
- Admin/Settings (users, roles, exchange rates, templates)

Granular capabilities (`edit`/`create`/`delete`/`export`/`approve`) are present
in the registry and grid with `enforced=False`, wired module-by-module in later
phases. Each later phase replaces that module's specific code checks (e.g.
`_user_can_edit_sheet`'s role component) with `has_capability`, while keeping
its region/stage logic.

## Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `accounts/permissions.py` | Canonical capability registry | nothing |
| `RolePermission` model | Persist grants | `Role` |
| `PermissionChangeLog` model | Audit grant changes | `Role`, `User` |
| `User.has_capability()` | Single read API for enforcement | `RolePermission`, registry |
| `require_capability` / `CapabilityRequiredMixin` | View gating | `has_capability` |
| `can` template filter | Nav/template gating | `has_capability` |
| Permissions admin view + template | Super-admin grid + AJAX toggle | registry, `RolePermission`, `PermissionChangeLog` |
| Seed data migration | Baseline grants = current behavior | registry, `Role` |

## Error handling & edge cases

- **Super-admin lockout:** impossible — `super_admin` bypasses all capability
  checks in code, and the permissions page is hardcoded to super_admin only.
- **User with no role:** `has_capability` returns `False` for everything (they
  already see almost nothing today).
- **New capability added in code, no grant rows yet:** default deny until a
  migration/management command seeds it; surface a warning in the grid for any
  registry codename lacking grant rows.
- **Orphan grant (codename removed from registry):** ignored by lookups; a
  `prune_permissions` management command cleans them.
- **Caching staleness:** per-request cache only, so a toggle takes effect on the
  next request. No long-lived cache in phase 1.

## Testing

- **Unit:** `has_capability` for each role; super_admin always-true; missing-row
  deny; per-request cache returns consistent results; seed migration produces
  the expected baseline for a sample of roles.
- **Integration:** a finance user hitting a module whose `access` is off gets
  403 and the nav link is hidden; flipping the grant via the admin view changes
  access on the next request; non-super-admin gets 403 on `/settings/permissions/`.
- **Regression:** confirm seeded baseline reproduces current access for
  finance, sales_rep, procurement, proposal on the main modules.

## Out of scope (this spec)

- Wiring granular edit/create/delete/export/approve enforcement (later phases).
- Encoding region/stage/ownership as editable data (rejected — stays in code).
- Per-user overrides (grants are per-role only).
- Per-object/record-level ACLs.
