"""What a project's purchase orders are made of, by system, and how much has landed.

Reproduces the workbook procurement keeps by hand: every ordered line grouped
under its system, each carrying its share of the project, its share of its own
system, and how much of it has been delivered.

The four percentages are not four views of the same denominator, which is the
thing to get right:

    % of All      line value        / project total
    % of system   line value        / that system's total
    % Delivery    delivered value   / project total
    Pending %     undelivered value / project total

So **% Delivery + Pending % = % of All** for every row, and the delivery columns
are readable straight down the page as progress against the whole project rather
than against each line. That identity is what makes the sheet work, and it is
asserted in the tests — if a change breaks it the page still renders, it just
quietly stops meaning anything.

Only committed orders count. A draft has not been ordered, so its delivery
progress is not a fact about anything; a cancelled order is not part of the
project's composition at all. Both are excluded, using the same status rule as
the budget figures so the two pages cannot disagree about what "ordered" means.
"""
from decimal import Decimal

from .budget_status import BASE_CURRENCY, COMMITTED_STATUSES, exchange_rates, to_base_currency

UNASSIGNED = 'Not categorised'


def _pct(part, whole):
    """Percentage, or None when there is nothing to be a share of.

    Never 0 for an absent denominator: on a page where 0% means "none of this
    has been delivered", using it for "there is nothing here" makes two very
    different situations look identical.
    """
    if not whole:
        return None
    return (part / whole * Decimal('100')).quantize(Decimal('0.01'))


def delivered_quantities(po_items):
    """PO item id -> quantity delivered against it.

    One query for the whole page. Delivery notes are records of goods that
    actually moved, so all of them count — there is no draft delivery.
    """
    from django.db.models import Sum

    from .models import DeliveryNoteItem

    rows = (DeliveryNoteItem.objects
            .filter(source_po_item__in=[i.pk for i in po_items])
            .values('source_po_item')
            .annotate(delivered=Sum('quantity')))
    return {r['source_po_item']: (r['delivered'] or Decimal('0')) for r in rows}


def build_rows(purchase_orders, rates=None):
    """One row per ordered line, in SAR, with its delivered share."""
    rates = exchange_rates() if rates is None else rates

    items = []
    for po in purchase_orders:
        if po.status not in COMMITTED_STATUSES:
            continue
        for item in po.items.all():
            items.append((po, item))

    delivered = delivered_quantities([i for _po, i in items])

    rows = []
    for po, item in items:
        currency = (po.currency or BASE_CURRENCY).upper()
        value = to_base_currency(item.total_value, currency, rates)

        ordered_qty = item.quantity or Decimal('0')
        delivered_qty = delivered.get(item.pk, Decimal('0'))
        # More delivered than ordered is a data problem, not extra value. Left
        # uncapped it drives Pending negative and pushes % Delivery past % of
        # All, which quietly breaks the identity the whole sheet rests on — so
        # it is capped and flagged instead of silently absorbed.
        over_delivered = bool(ordered_qty and delivered_qty > ordered_qty)
        effective_qty = min(delivered_qty, ordered_qty) if ordered_qty else Decimal('0')
        delivered_value = (
            (value * effective_qty / ordered_qty).quantize(Decimal('0.01'))
            if ordered_qty else Decimal('0'))

        rows.append({
            'po': po,
            'item': item,
            'reference': f'{po.po_number}-{item.serial_number:02d}',
            'system': (item.system or '').strip() or UNASSIGNED,
            'description': item.description,
            'value': value,
            'ordered_qty': ordered_qty,
            'delivered_qty': delivered_qty,
            'delivered_value': delivered_value,
            'pending_value': value - delivered_value,
            'over_delivered': over_delivered,
            'converted': currency != BASE_CURRENCY,
        })
    return rows


def breakdown(purchase_orders, rates=None):
    """The whole page: systems, their lines, and the summary.

    Systems are ordered by value, largest first — the question this answers is
    "what is this project mostly made of", and alphabetical order buries it.
    """
    rows = build_rows(purchase_orders, rates=rates)
    total = sum((r['value'] for r in rows), Decimal('0'))
    delivered_total = sum((r['delivered_value'] for r in rows), Decimal('0'))

    systems = {}
    for row in rows:
        systems.setdefault(row['system'], []).append(row)

    groups = []
    for name, system_rows in systems.items():
        system_total = sum((r['value'] for r in system_rows), Decimal('0'))
        system_delivered = sum((r['delivered_value'] for r in system_rows), Decimal('0'))
        for row in system_rows:
            row['pct_of_all'] = _pct(row['value'], total)
            row['pct_of_system'] = _pct(row['value'], system_total)
            row['pct_delivered'] = _pct(row['delivered_value'], total)
            row['pct_pending'] = _pct(row['pending_value'], total)
        groups.append({
            'system': name,
            'rows': sorted(system_rows, key=lambda r: r['value'], reverse=True),
            'total': system_total,
            'delivered': system_delivered,
            'pending': system_total - system_delivered,
            'pct_of_all': _pct(system_total, total),
            'pct_delivered_of_system': _pct(system_delivered, system_total),
            'line_count': len(system_rows),
        })

    # Largest first, but anything uncategorised goes last however big it is —
    # it is a gap to close rather than a finding about the project.
    groups.sort(key=lambda g: (g['system'] == UNASSIGNED, -g['total']))

    return {
        'groups': groups,
        'rows': rows,
        'total': total,
        'delivered': delivered_total,
        'pending': total - delivered_total,
        'pct_delivered': _pct(delivered_total, total),
        'converted': any(r['converted'] for r in rows),
        'over_delivered': [r for r in rows if r['over_delivered']],
        'po_count': len({r['po'].pk for r in rows}),
    }
