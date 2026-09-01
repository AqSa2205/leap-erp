"""How much of a project's approved budget its purchase orders have committed.

Procurement's question is "have we spent the budget yet, and how close are we?".
Answering it means putting two numbers on the same footing, and they are not
naturally on the same footing at all — most of this module is that reconciliation
rather than the arithmetic.

**VAT.** `PurchaseOrder.total_value` is `gross_value + vat_amount`, while a
budgeted line price carries no VAT anywhere in its derivation. Comparing the two
directly would report every project as ~15% further through its budget than it
is, consistently and invisibly. Commitment is therefore measured on
`gross_value`.

**Basis.** A PO seeded from a budget is priced at `budget_unit_price()`,
written so that "a PO's rate × qty reproduces the budgeted line total". Summing
`budget_line_price()` over the same lines is exactly the denominator those
numerators were built against, so a project procured strictly to budget lands on
100.0% rather than merely near it.

**Cancelled orders commit nothing** and are excluded outright. **Drafts are not
commitments either** — the question is what has been *issued* — but they are not
discarded either, because a draft is about to become a commitment and a
procurement lead deciding whether to raise another one needs to see it. They are
reported separately so the headline stays honest and nothing is hidden.

**No budget is not zero budget.** A project with no finance-approved sheet has an
undefined percentage, not 0% and not 100%. Returning a number there would invent
a fact; the caller gets None and says so.

**Over budget is not capped.** Going past 100% is the single most important thing
this can tell anyone, so it is reported as it is rather than clamped to a full
bar that looks like success.
"""
from decimal import Decimal

# What counts as money committed. A cancelled PO commits nothing and is absent
# from both tuples on purpose.
COMMITTED_STATUSES = ('issued', 'client_acknowledged', 'completed')
PENDING_STATUSES = ('draft',)

APPROVED_STAGE = 'finance_approved'
BASE_CURRENCY = 'SAR'


def exchange_rates():
    """Currency code -> units per USD. SAR is 3.75, USD is 1."""
    from costing.models import ExchangeRate
    return {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}


def to_base_currency(amount, currency, rates):
    """Convert into SAR.

    Despite the field being named `rate_to_usd`, the seeded values are units
    per USD (SAR 3.75, USD 1.0), so X -> SAR is amount * rate_SAR / rate_X.

    An unknown currency is returned unconverted rather than dropped or zeroed:
    losing committed spend from the figure is far worse than a slightly wrong
    one, and the caller flags that a conversion was involved.
    """
    currency = (currency or BASE_CURRENCY).upper()
    if currency == BASE_CURRENCY:
        return amount
    base_rate, rate = rates.get(BASE_CURRENCY), rates.get(currency)
    if not base_rate or not rate:
        return amount
    return (amount * base_rate / rate).quantize(Decimal('0.01'))


def approved_budgets_for(projects):
    """Approved budget in SAR per project id, for every project given.

    Built in one pass over prefetched sheets rather than per project: this
    feeds a board listing every project procurement is working on, and a
    per-project walk of sections and line items would issue queries in
    proportion to the whole chart of work.

    A project may hold more than one finance-approved sheet — separate phases
    or scopes — and the approved budget for the project is their sum. Revisions
    do not multiply sheets (CostingSheetRevision is a snapshot), so this does
    not double-count a re-approved sheet.
    """
    from costing.models import CostingSheet

    ids = [p.pk for p in projects if p is not None]
    if not ids:
        return {}

    rates = exchange_rates()
    sheets = (CostingSheet.objects
              .filter(project_id__in=ids, workflow_stage=APPROVED_STAGE)
              .prefetch_related('sections__line_items'))

    totals = {}
    for sheet in sheets:
        total = Decimal('0')
        for section in sheet.sections.all():
            # Optional sections are quotable extras the client has not bought,
            # so they are not budget to spend. po_from_budget skips them for
            # the same reason, and the two must agree or a PO seeded from a
            # budget would not reconcile against it.
            if section.is_optional:
                continue
            for item in section.line_items.all():
                item.set_exchange_rates_cache(rates)
                item.set_sheet_cache(sheet)
                total += item.budget_line_price()
        totals[sheet.project_id] = totals.get(sheet.project_id, Decimal('0')) + total
    return totals


def commitment(purchase_orders, rates=None):
    """Split a project's orders into committed and still-draft spend, in SAR."""
    rates = exchange_rates() if rates is None else rates
    committed = draft = Decimal('0')
    converted = False

    for po in purchase_orders:
        if po.status in COMMITTED_STATUSES:
            bucket = 'committed'
        elif po.status in PENDING_STATUSES:
            bucket = 'draft'
        else:
            continue                      # cancelled — commits nothing

        currency = (po.currency or BASE_CURRENCY).upper()
        # Ex-VAT: the budget it is measured against carries none.
        amount = to_base_currency(po.gross_value, currency, rates)
        if currency != BASE_CURRENCY:
            converted = True
        if bucket == 'committed':
            committed += amount
        else:
            draft += amount

    return {'committed': committed, 'draft': draft, 'converted': converted}


def budget_status(budget, purchase_orders, rates=None):
    """Where a project stands against its approved budget.

    `percent` is None when there is no budget to be a percentage of — the
    caller must say "no approved budget" rather than render a 0% bar, which
    reads as "nothing spent" when the truth is "nothing to compare against".
    """
    spend = commitment(purchase_orders, rates=rates)
    committed, draft = spend['committed'], spend['draft']
    has_budget = budget is not None and budget > 0

    def pct(amount):
        if not has_budget:
            return None
        return (amount / budget * Decimal('100')).quantize(Decimal('0.1'))

    return {
        'budget': budget if has_budget else None,
        'committed': committed,
        'draft': draft,
        'percent': pct(committed),
        'percent_with_draft': pct(committed + draft),
        'remaining': (budget - committed) if has_budget else None,
        'over_budget': bool(has_budget and committed > budget),
        # Committed is within budget but the drafts would take it past —
        # the moment to act is before they are issued, not after.
        'draft_would_exceed': bool(
            has_budget and committed <= budget and committed + draft > budget),
        'converted': spend['converted'],
        'has_budget': has_budget,
    }
