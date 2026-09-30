# Linking an existing purchase order to a budget

## Why

`PurchaseOrderItem.source_bom_item` is the only join between a purchase order
and the budget it spends, and until now **nothing could set it but the two
paths that seed a PO from a budget** — the tracker and "create PO from budget".

A PO typed by hand, or imported from a supplier quotation, therefore had no
link and no way to get one. Four consequences, none of them visible on the PO
itself:

| | |
|---|---|
| the tracker | kept offering those budget lines, so the same scope could be ordered twice with nothing to say so |
| the cash-outflow schedule | `fill_po_numbers` had no line ids to work from, so the PO number never arrived and somebody typed it from memory |
| Budget vs Actual | nothing to compare, so the panel stayed blank |
| KPI planned vs actual | never saw the spend |

Note what was *not* broken: the project-level "approved budget vs committed"
figure sums whole POs by project, so a hand-raised PO always counted there.
What was missing is per-line traceability, which is what the other four need.

## The screen

A **Link to budget** button on the PO detail page — offered when there is no
link, and as **Edit links** when there is, so a mis-link can be corrected.

Pick the project's finance-approved budget, then each PO line gets a dropdown
of that budget's lines showing what is left of each (`8 of 10 left`). Blank
means "not budgeted", which is also how a link is removed.

Deliberately allowed on an **issued** PO, not just a draft: the whole point is
reconciling orders somebody already raised. A client-acknowledged PO is refused
along with everything else that writes to one.

## The rules, and why

**Over-linking is refused, not clamped.** A PO line for 12 against a budget
line with 10 left is told so: *"1 Camera 4MP: 10 of 10 left, but this order
would link 12."* Clamping would quietly link part of a line and report a
partial figure without saying why; allowing it would push the tracker's
remaining quantity negative, which nothing downstream expects.

**The remaining-quantity rule is the tracker's, not a second copy of it.** Both
read `RELEASED_STATUSES`, so a cancelled order stays in the history and stops
counting — the tracker learned that one the hard way ("Partial — 0 of 2
ordered"). If the two disagreed, one screen would offer what the other had
already spent.

**All or nothing.** One bad row refuses the whole screen. A half-applied save
leaves the person working out which half.

**A PO counts as itself, not against itself.** `linked_quantity(line,
exclude_po=po)` leaves this PO's own links out, so re-saving an unchanged
screen — or moving a link within the same PO — is not blocked by the quantity
it already holds. A mutation of exactly this survived the first pass: the test
had the PO holding 4 of 10, which passes either way. It now holds 8 of 10,
where the difference bites.

**One budget per PO**, the same rule #251 applies when appending to a draft,
because the Budget vs Actual panel shows one figure and one link back. Once a
PO draws on a budget the picker is pinned to it; unlink everything to change it.

## The cash-outflow side

After a successful save the view calls `fill_po_numbers`, which is idempotent,
never overwrites, and refuses a draft or a placeholder number on its own. So
linking an already-issued PO fills the outflow rows it covers — the step that
would otherwise never happen, because that function normally runs when a PO
*becomes* committed, which for these POs already happened before they had any
line ids.

It re-fetches the PO first. The view prefetches items to render the page, so
the instance in hand still carries the links as they were before the write —
**a bug the test caught**: the first version filled nothing for an issued PO.

## Verification

- **32 tests.** The four consequences of linking, the six refusals, the
  cash-outflow fill for an issued PO and its absence for a draft, and the
  access matrix (procurement, admin, super admin, a manager turned away by the
  role gate, an admin in another region 404ing, a read-only PM refused).
- `validate()` also unit-tested directly, because the view narrows the offered
  lines before calling it — so its cross-budget branch is unreachable from the
  URL and would otherwise have been pinned by nothing. That guard exists for
  the next caller (a bulk importer, an API).
- **14 guards mutation-tested.** Four survived the first pass and every one was
  a real gap in the tests, not in the code.
- No migrations — the field has existed all along.
