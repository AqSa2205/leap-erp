# Costing Stage Barriers — Design

**Date:** 2026-06-14
**Goal:** Enforce strict, stage-based edit barriers on costing sheets so the workflow timestamps (handover, start-costing, finalize, …) accurately reflect who did what, when — making them trustworthy for management KPIs.

## Problem

Every costing-sheet mutation funnels through one gate, `_user_can_edit_sheet(user, sheet)`. Today, in the pre-finance stages, that gate allows the **creator (region-scoped), admins (region), managers (region), and the entire proposal team (global)** to edit a sheet **regardless of its `workflow_stage`**. Consequences:

- Sales can do costing work while the sheet is still `bom_in_progress` (before the BOM is handed over).
- Editing is possible during `ready_for_costing` before anyone clicks "Start costing", so the `costing_started_at` clock doesn't bound the work.
- Proposal can keep editing after handover.

The transition state machine (`costing_workflow_transition`) and all the `*_at`/`*_by` timestamp fields already exist and are correctly team-gated. What's missing is tying **edit rights** to the **stage**.

## Decisions (from stakeholder)

1. **Override scope:** **super_admin only**. Admins, managers, sales reps, proposal reps, and the sheet creator all respect the barriers. Only super_admin can edit out-of-stage.
2. **`ready_for_costing` (handed to Sales, before Start costing):** **fully locked** — no edits by anyone (except super_admin). The only available action is the "Start costing" transition, which unlocks editing.
3. **Grandfathering:** apply barriers to **new sheets only**. Sheets that already exist at deploy time keep the current lenient rules. Implemented via a per-sheet boolean `enforce_stage_barriers` (default `True`; a data migration sets it `False` for every pre-existing row). This implements "only new sheets from today onward" precisely regardless of the actual deploy date.

## Barrier matrix (when `enforce_stage_barriers=True`)

`super_admin` → always allowed (handled before the matrix). Region scope = `sheet.project.region == user.region`.

| `workflow_stage` | Who may edit |
|---|---|
| `bom_in_progress` | `is_proposal_team_user` (proposal owns the BOM) |
| `ready_for_costing` | **nobody** (locked checkpoint — Start costing unlocks) |
| `costing_in_progress` | `is_sales_team_user` AND in-region |
| `finalized` | `is_sales_team_user` AND in-region |
| `finance_review` | `is_finance_team_user` AND in-region *(already enforced; unchanged)* |
| `finance_approved` | nobody *(already enforced; unchanged)* |

Notes:
- `is_sales_team_user` already includes sales_rep, manager, admin, super_admin. So during `costing_in_progress`, regional admins/managers/sales-reps can edit; proposal team cannot.
- Proposal team is **global** (no region filter), matching today's behavior for that team.
- The existing field-level pricing gate (`_field_is_pricing` → proposal team can't touch pricing fields) is unchanged and continues to apply on top during `bom_in_progress`.

## When `enforce_stage_barriers=False` (grandfathered)

`_user_can_edit_sheet` keeps the **exact current** pre-finance ruleset (creator region-scoped, admin region, manager region, proposal team global). Finance-stage locks (`finance_review`, `finance_approved`) apply to **all** sheets regardless of the flag — no change there.

## Enforcement — two layers

### Layer 1 — Server (the guarantee)
All ~21 mutation endpoints already call `_user_can_edit_sheet` (or a child-permission mixin that does). Adding the stage logic **inside that one function** covers every endpoint at once: line items, sections, pricing params, SOW items, terms/remark/contact toggles, vendor quotes, currency apply, Excel import, revision delete. Out-of-stage edits return `403`. This is what makes the KPI tamper-resistant.

New helper:
```python
def _strict_stage_edit(user, sheet, stage):
    region_ok = bool(sheet.project and sheet.project.region_id == user.region_id)
    if stage == 'bom_in_progress':
        return bool(getattr(user, 'is_proposal_team_user', False))
    if stage == 'ready_for_costing':
        return False
    if stage in ('costing_in_progress', 'finalized'):
        return bool(getattr(user, 'is_sales_team_user', False) and region_ok)
    return False
```

`_user_can_edit_sheet` becomes:
```python
def _user_can_edit_sheet(user, sheet):
    if not user.is_authenticated:
        return False
    if user.is_super_admin_user:
        return True
    stage = sheet.workflow_stage
    if stage == 'finance_review':
        return bool(getattr(user, 'is_finance_team_user', False)
                    and sheet.project and sheet.project.region_id == user.region_id)
    if stage == 'finance_approved':
        return False
    if sheet.enforce_stage_barriers:
        return _strict_stage_edit(user, sheet, stage)
    # ── grandfathered: original lenient pre-finance ruleset ──
    if (sheet.created_by_id == user.id and sheet.project
            and sheet.project.region_id == user.region_id):
        return True
    if user.is_admin_user and sheet.project and sheet.project.region_id == user.region_id:
        return True
    if user.is_manager_user and sheet.project and sheet.project.region_id == user.region_id:
        return True
    if getattr(user, 'is_proposal_team_user', False):
        return True
    return False
```

### Layer 2 — UI (the UX)
- `CostingDetailView.get_context_data` passes `can_edit = _user_can_edit_sheet(user, sheet)` and a `lock_reason` string describing why it's read-only at the current stage.
- The detail template shows a prominent read-only banner when `not can_edit` and hides the primary edit entry points (Add section, Add line item, Paste, Import, Apply currency, SOW add, vendor-quote upload, "Edit sheet") behind `{% if can_edit %}`.
- Inline cell edits that slip through are still server-blocked (403) — the existing AJAX error path surfaces the message — so the UI gating is convenience, not the security boundary.

## Out of scope
- **Who clicks the transition buttons** is unchanged — the transition `allowed_teams` checks already gate handover/start-costing/finalize/etc. correctly. Locking edits at `ready_for_costing` does **not** block the Start-costing button (transitions don't pass through the edit gate).
- No new roles, no changes to finance-stage behavior, no retroactive gating of existing sheets.

## Testing
Security-sensitive, so each barrier gets a test:
- Unit: `_user_can_edit_sheet` across the matrix — (stage × role × `enforce_stage_barriers`) → expected boolean, plus super_admin override and grandfathered legacy parity.
- Integration: a representative mutation endpoint (e.g. `ajax_add_line_item` / `ajax_update_item_field`) returns 403 out-of-stage and 200 in-stage; sales blocked at `bom_in_progress`, unlocked after handover→start-costing; proposal blocked at `costing_in_progress`; `ready_for_costing` locked for all non-super-admin; grandfathered sheet keeps legacy access.
- New sheets default `enforce_stage_barriers=True`; data migration sets existing rows `False`.
