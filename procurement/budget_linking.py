"""Linking an existing purchase order's lines to a finance-approved budget.

`PurchaseOrderItem.source_bom_item` is the only join between a purchase order
and the budget it spends, and until now nothing could set it but the two paths
that SEED a PO from a budget. A PO typed by hand, or imported from a supplier
quotation, was therefore invisible to the budget for good:

  * the tracker kept offering those budget lines, so the same scope could be
    ordered twice with nothing to say so;
  * `finance.outflow_links.fill_po_numbers` had no line ids to work from, so
    the PO number never reached the cash-outflow schedule and somebody typed
    it in from memory;
  * `PurchaseOrder.budget_breakdown()` had nothing to compare, so the Budget
    vs Actual panel stayed blank;
  * the KPI planned-versus-actual never saw the spend.

This module is the arithmetic behind the linking screen, kept pure so the
screen and the tests agree about it.

**The remaining-quantity rule is copied from nowhere.** It is the same
expression the tracker uses, reading the same RELEASED_STATUSES, because the
two screens must never disagree about how much of a budget line is left. A
cancelled order stays visible in the history and stops counting - the tracker
learned that the hard way ("Partial - 0 of 2 ordered").

**Over-linking is refused, not clamped.** A PO line for 5 against a budget
line with 2 left is nearly always a mis-pick, and the honest failure is to say
"2 of 10 left, your line is for 5". Clamping would quietly link part of a PO
line and report a partial figure without saying why; allowing it would push the
tracker's remaining quantity negative, which nothing downstream expects.
"""
from decimal import Decimal

from .budget_status import RELEASED_STATUSES

ZERO = Decimal('0')


def linked_quantity(line, exclude_po=None):
    """How much of `line` is already committed on live purchase orders.

    `exclude_po` leaves that PO's own links out, so re-saving the same screen
    does not count a PO against itself.
    """
    total = ZERO
    for item in line.procured_po_items.all():
        if exclude_po is not None and item.purchase_order_id == exclude_po.pk:
            continue
        if item.purchase_order.status in RELEASED_STATUSES:
            continue
        total += item.quantity or ZERO
    return total


def remaining(line, exclude_po=None):
    """Budget quantity still unspoken for. Can be negative only if data
    already over-committed the line before this screen existed."""
    return (line.quantity or ZERO) - linked_quantity(line, exclude_po=exclude_po)


def sheet_of(line):
    return line.section.costing_sheet_id


def validate(po, sheet_id, choices):
    """Check a whole screenful of choices at once.

    `choices` maps a PurchaseOrderItem to a CostingLineItem or None (None
    meaning "not budgeted" - an unlink, or a line left alone).

    Returns `(errors, changes)`. `errors` is a list of sentences to show the
    user; when it is non-empty nothing should be written, because a half
    applied screen is worse than a refused one - the person would have to work
    out which half. `changes` lists (item, line_or_None) for the items whose
    link actually moves, so an unchanged screen writes nothing at all.
    """
    errors = []

    # One budget per purchase order. The tracker enforces the same thing when
    # appending (see _appendable_draft_pos), because the Budget vs Actual panel
    # shows one figure and one link back per PO.
    for item, line in choices.items():
        if line is not None and sheet_of(line) != sheet_id:
            errors.append(
                f'Line {item.serial_number} points at a budget line from a '
                f'different budget. A purchase order may only draw on one.')

    # Quantities, per budget line, counting everything this screen would link
    # to it at once - two PO lines can name the same budget line.
    wanted = {}
    for item, line in choices.items():
        if line is not None:
            wanted.setdefault(line.pk, (line, ZERO))
            known_line, total = wanted[line.pk]
            wanted[line.pk] = (known_line, total + (item.quantity or ZERO))

    for line, total in wanted.values():
        left = remaining(line, exclude_po=po)
        if total > left:
            errors.append(
                f'{line.item_number} {line.description}: {_q(left)} of '
                f'{_q(line.quantity)} left, but this order would link '
                f'{_q(total)}.')

    changes = [(item, line) for item, line in choices.items()
               if item.source_bom_item_id != (line.pk if line else None)]
    return errors, changes


def _q(value):
    """Quantities read as people write them: 2 rather than 2.000."""
    value = value or ZERO
    normalised = value.normalize()
    if normalised == normalised.to_integral_value():
        return str(normalised.quantize(Decimal('1')))
    return str(normalised)
