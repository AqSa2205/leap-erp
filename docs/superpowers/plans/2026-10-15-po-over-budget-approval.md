# Over-budget POs: flag, explain, COO decision

**Date:** 2026-10-15
**Area:** procurement (PO detail, budget tracker, PO list), finance (costing budget screen)

## What it does

- A PO that takes a finance-approved budget over - per budget line or for the
  budget as a whole - is flagged. Money, not quantity: PO line value after
  discount, ex-VAT, in SAR; every PO except cancelled ones; drafts count so the
  overspend is caught early.
- The PO is **not blocked**. It still follows SCM -> PM -> COO and can be printed.
- The overrun runs on its own track: the SCM signer and the PM (from Approval
  Routing) write a remark, then the COO approves or rejects.
- **Approve:** the approved amount is shown beside the budget on the tracker
  ("+ X approved overrun = Y") and per line. The finance-approved budget itself
  never changes.
- **Reject:** the PO goes back to Draft and its approvals are cleared, so the
  revised PO is approved again from SCM. A new request opens only if the revised
  PO is still over.
- If a PO changes later and the overrun grows past what was approved, a new
  request opens; if it shrinks or the PO is cancelled, nothing new is needed.
- In-app notifications (the bell) only.

## Where it lives

- `procurement/budget_overrun.py` - detection, remarks, decisions, events.
- Models: `BudgetOverrun`, `BudgetOverrunLine`, `BudgetOverrunEvent`
  (migration `procurement/0032_budget_overrun`).
- `_tracker_rows` carries `overrun_status`, `approved_overrun` and
  `approved_total` per line, so the tracker page and its Excel export share
  one row shape.

## Notes

- Remarker and decider FKs are `SET_NULL`; the template shows "(removed user)"
  rather than failing when one of them is deleted.
- A literal `|default:"-"` argument is always safe in templates; a variable
  argument (`|default:o.user.username`) raises when the object is None.
- Tests: `procurement/tests_budget_overrun.py`.
