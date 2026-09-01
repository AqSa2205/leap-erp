# Procurement → Cash Outflow — PO Number Auto-fill — Implementation Plan

Date: 2026-09-01
Design: `docs/superpowers/specs/2026-09-01-procurement-outflow-po-numbers-design.md`

## Global Constraints

- **Never overwrite.** Add a number; never substitute one.
- **Never write a placeholder** (`DRAFT-…`).
- **Committed orders only**, via `budget_status.COMMITTED_STATUSES`.
- **Idempotent.** Running twice must change nothing the second time.
- **Scoped by project.** Never match a row on the costing line alone.
- Run `python manage.py test procurement finance` after each task.

## File Structure

```
finance/
  outflow_links.py                       (new) fill_po_numbers, backfill
  tests_outflow_links.py                 (new)
  migrations/0005_backfill_outflow_po_numbers.py  (new) one-off backfill
procurement/
  models.py    record_status_change hook
  views.py     post-formset call; source_bom_item on po_from_budget
```

---

### Task 1: The fill — DONE

`fill_po_numbers(po)` — match items' `source_bom_item` to
`CashOutflowRow.source_ref`, scoped to the PO's project, append the number.

**Tests:** fills the row it covers; only that row; refuses drafts, cancelled,
placeholders, project-less POs and items with no costing link; never crosses
projects.

### Task 2: Appending — DONE

Accumulate several PO numbers, compare as whole entries, skip on overflow.

**Tests:** second PO added not substituted; `PO-1` not found inside `PO-10`;
overflow leaves the number out rather than truncating; running twice is a no-op.

### Task 3: The hooks — DONE

`record_status_change`, plus after the item formset in both views.

**Tests:** issuing via `record_status_change` fills without being asked.

### Task 4: The missing back-link — DONE

`po_from_budget` now sets `source_bom_item`, as `bom_procurement_tracker`
always did.

### Task 5: Backfill — DONE

Data migration calling the real implementation; no-op reverse.

**Tests:** fills pre-existing committed orders; idempotent; respects the same
refusals.

## Risks

- **Overwriting finance's entry** is the one genuinely destructive failure.
  Guarded, and it is the mutation that fails the most tests (7).
- **Writing a placeholder** onto a financial schedule looks like an answer.
- **Cross-project matching.** A costing line pk is globally unique, but a stray
  row on another project referencing the same line must not be touched — so the
  query is scoped by project rather than trusting the ref.
- **Ordering in the edit form.** The status hook fires before the formset saves;
  without the second call, issuing and adding lines in one request fills
  nothing.

## What the build taught us

**One end of the link was broken and nobody would have noticed.**
`po_from_budget` created items from a `CostingLineItem` without recording
which one, while the other path creating items from the same model always
did. The feature would have worked perfectly for POs raised one way and
silently never fired for the other — no error, just rows that stayed empty.
Worth checking both ends of a join before building on it.

**All seven guards mutation-tested and all seven bite:** overwriting instead of
appending fails 7, allowing placeholders fails 1, filling for drafts fails 1,
ignoring the project fails 1, substring matching fails 1, truncating on overflow
fails 1, and removing the status hook fails 1.
