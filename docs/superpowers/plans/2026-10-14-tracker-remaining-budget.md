# Remaining Budget on the procurement tracker

2026-10-14 · PR #281

## What it adds

A sixth tile on the BOM procurement tracker: **Remaining Budget (SAR)** =
Budget Total minus what the purchase orders have committed against *this*
budget. Negative shows in red with a minus sign, zero or positive in green.
Drafts are reported underneath ("Drafts not counted: …") rather than deducted,
because a draft is not a commitment yet.

## Where the figure comes from

`procurement/budget_status.commitment_for_items(po_items, rates)` — a sibling
of `commitment()`, which measures whole purchase orders. The rules are the
same and deliberately live in one module:

- **ex-VAT** — a budgeted line price carries no VAT anywhere in its
  derivation, so comparing against `total_value` would report every project
  ~15% further through its budget than it is;
- **SAR** — converted through `exchange_rates()`;
- **cancelled orders commit nothing** and are excluded outright;
- **drafts** are returned in their own bucket.

The difference from `commitment()` is the unit measured. A purchase order is
not all budget: `PurchaseOrderItemForm` does not expose `source_bom_item` and
the Excel importer never sets it, so an order raised from a budget can carry
further lines the budget never had — freight, an extra material. Those are not
spend against it. `PurchaseOrder.budget_breakdown` already counts only the
budget-sourced lines of a single order, so measuring whole orders here would
have the tracker and the PO detail page's Budget vs Actual panel reporting
different figures for the same money.

An order's discount is a percentage of its whole base amount, so each line
carries its share, taken and rounded one line at a time the way
`PurchaseOrder.discount_amount` takes it. Given every line of an order, that
reproduces `gross_value` exactly — which is what keeps the two functions one
rule rather than two.

## Cost

No extra queries. The lines come from the rows `_tracker_rows` has already
built and prefetched for the page, so the tile measures exactly the set the
page lists.

## Deliberate, not oversights

- **Sub items procurement adds** never move Budget Total (they are extra
  scope, not approved budget) but money spent on them *is* deducted. Ordering
  one can therefore take Remaining Budget negative without the budget having
  changed, which is the honest reading of an approved budget.
- **Optional sections** are excluded from both sides: they are quotable extras
  the client has not bought, and the link-to-budget screen already refuses
  them (`section__is_optional=False`), so no spend can hide there.
- The tile is **not** scoped to the viewer's visible POs. Remaining budget is a
  fact about the budget, not a per-user view; the page itself is gated.

## Tests

`procurement/tests_remaining_budget.py` — 16 tests: every committed status,
drafts, cancelled, VAT, multi-line and multi-PO dedup, another budget's PO, an
unlinked PO, a sub item, a line on the order that is not on this budget, a
discounted order, and both access gates.
