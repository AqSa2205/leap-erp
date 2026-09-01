# Procurement → Cash Outflow — PO Number Auto-fill — Design

Date: 2026-09-01
Modules: `procurement`, `finance`
Status: built

## Why

A cash-outflow row is generated from a costing line and carries a `po_number`
finance types in by hand once procurement raises the order. So the schedule is
only as current as somebody's memory of which PO covered which line — and that
number is the one field on the row the system already knows for certain.

## The link already existed at both ends

Nothing was joining them:

```
CashOutflowRow.source_ref          'line:<CostingLineItem.pk>'
PurchaseOrderItem.source_bom_item  -> CostingLineItem
```

So when a PO reaches a committed status, every outflow row seeded from a costing
line that PO covers can be filled in.

**One end was broken.** `bom_procurement_tracker` — where procurement picks
lines for a PO — has always set `source_bom_item`. But `po_from_budget`, the
shortcut that seeds a PO with every supply line, did not, despite creating items
from the same `CostingLineItem`. POs raised that way could never be traced back,
so no amount of joining would have reached their rows. Fixed as part of this.

## Where it fires

`PurchaseOrder.record_status_change()` — the single method every status change
goes through, including the one the edit form makes. Hooking it means no call
site has to remember.

Also after the item formset saves in both the create and update views. The
status hook fires *before* the formset writes, so on a request that both commits
a PO and adds its lines, the first call sees no items at all. Calling again is
free: the whole thing is idempotent.

## Three rules that make it safe

**Never overwrite.** The field is finance's. They may have typed something the
system knows nothing about — an agreement, a note, a number from outside. A
value is only ever *added*; a row already naming this PO is left completely
alone, which is what makes re-running a no-op.

Where a costing line is split across several orders the numbers accumulate,
comma-separated, because showing only the first would understate what is
committed against that line. Numbers are compared as whole entries, so `PO-1` is
not considered already present in `PO-10`. If appending would overflow the
field, the number is left out rather than truncated — a truncated PO number is
one that does not exist, which is worse than showing fewer of them.

**Never write a placeholder.** `po_from_budget` generates numbers like
`DRAFT-S12-1787214550` purely to satisfy the unique constraint until somebody
types the real one. On a financial schedule that looks like an answer.

**Only committed orders.** `issued`, `client_acknowledged`, `completed` —
reusing `budget_status.COMMITTED_STATUSES`, so the outflow schedule, the budget
percentage and the system breakdown all agree about what "ordered" means. A
draft has not been ordered and covers no outflow.

## Backfill

Orders committed before this existed are filled by a data migration. That is the
only route to production: the web service has no shell, so a management command
could be run on a laptop and nowhere else.

It calls the real implementation rather than a copy frozen at the migration —
the rules above *are* the point, and a divergent copy would be a second set of
them to get wrong. The reverse is a deliberate no-op: a number this added is
indistinguishable from one typed by hand, so removing them would delete real
work to undo a backfill.

## What this does not do

- **A.2 rows are not filled.** PO items link to `CostingLineItem`, and A.2 rows
  come from scope-of-work items, which POs have no link to.
- **A cancelled PO keeps its number on the row.** Removing text finance can see
  is worse than leaving a record of what happened; the PO's own status says it
  was cancelled.
- **No matching by description or amount.** Guessing would credit an outflow to
  an order that may not cover it.
