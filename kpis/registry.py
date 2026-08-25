"""The KPI registry: management's Sales / Proposal / Procurement KPIs declared
in code, each mapped to either a live ERP computation or manual entry.

The dashboard view stays thin and walks `KPI_DEFINITIONS`, calling `.compute(ctx)`
for auto KPIs and reading a KPIEntry for manual ones.

Each auto compute takes a `KPIContext` (period + date bounds + targets + optional
`user` and `region` filters) and returns a `KPIResult` (value + a short coverage
note describing what fed it + optional currency). The coverage note is what makes
a blank/odd tile explain itself ("0 submitted proposals", "1 of 3 won have
actuals") instead of looking broken.

Time basis: the project-outcome KPIs (win rate, revenue, forecast accuracy,
proposal win rate) bucket a deal by the project's financial `year` +
`po_award_quarter` — the fields management maintains — for YEAR and QUARTER
periods. A MONTH period buckets on the status-history transition date instead,
because `po_award_quarter` cannot express a month and widening it to the
containing quarter reported three months under a one-month heading. See
`_outcome_projects()`. Procurement/proposal KPIs with real timestamps are
date-based throughout.

Money basis: anything valuing a deal goes through
`projects.views._resolve_project_sales_value()` — costing sheet, then
`actual_sales`, then `estimated_value` — which is the same ladder the home
dashboard's Won tile uses. Do not sum a value column directly here; that is
what made the two pages disagree.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional


# ── Departments ──────────────────────────────────────────────────────────────
SALES = 'sales'
PROPOSAL = 'proposal'
PROCUREMENT = 'procurement'
HR = 'hr'


DEPARTMENTS = [
    (SALES, 'Sales'),
    (PROPOSAL, 'Proposal'),
    (PROCUREMENT, 'Procurement'),
    (HR, 'HR'),
]

# ── Units (drive display formatting in the template) ─────────────────────────
PERCENT = 'percent'
RATIO = 'ratio'
DAYS = 'days'
HOURS = 'hours'
COUNT = 'count'
CURRENCY = 'currency'
SCORE = 'score'


@dataclass
class KPIContext:
    """Everything a compute function needs. `region` is a Region instance or
    None (all regions); `user` restricts to one person's records or None."""
    period: str
    start: object
    end: object
    targets: dict = field(default_factory=dict)
    user: object = None
    region: object = None


@dataclass
class KPIResult:
    value: Optional[Decimal] = None
    coverage: str = ''           # short human note about what fed the number
    currency: Optional[str] = None


def make_context(period, targets=None, user=None, region=None):
    from .periods import period_bounds
    start, end = period_bounds(period)
    return KPIContext(period, start, end, targets or {}, user, region)


@dataclass(frozen=True)
class KPI:
    key: str
    department: str
    label: str
    unit: str
    direction: str                      # 'higher' | 'lower'
    target: Optional[Decimal] = None    # default target; overridable per period
    source: str = 'manual'              # 'auto' | 'manual'
    compute: Optional[Callable] = None  # (ctx) -> KPIResult
    help: str = ''
    target_help: str = ''               # hint shown next to the target input

    @property
    def is_auto(self):
        return self.source == 'auto' and self.compute is not None

    @property
    def is_user_attributable(self):
        return self.key in USER_ATTRIBUTABLE_KEYS


def _d(x):
    """Coerce to Decimal or None (swallows bad data so one row can't 500 a page)."""
    if x is None:
        return None
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _period_year_quarter(period):
    """(year_str, quarter_str|None) for the outcome-KPI bucketing. A month
    period maps to its containing quarter (June -> Q2)."""
    parts = (period or '').split('-')
    year = parts[0]
    if len(parts) == 1:
        return year, None
    tail = parts[1]
    if tail.upper().startswith('Q'):
        return year, tail.upper()
    month = int(tail)
    return year, f'Q{(month - 1) // 3 + 1}'


# ══════════════════════════════════════════════════════════════════════════════
# Compute helpers.
# ══════════════════════════════════════════════════════════════════════════════

def _period_is_month(period):
    """True for 'YYYY-MM'. Distinguished from 'YYYY-Qn' and 'YYYY'."""
    parts = (period or '').split('-')
    return len(parts) == 2 and not parts[1].upper().startswith('Q')


def _outcome_projects(ctx):
    """Projects whose CURRENT status is won/lost, placed into the period.

    Two bucketing bases, because the fields have different granularities:

    * **Year and quarter** use the project's financial ``year`` +
      ``po_award_quarter`` — the fields management maintains. Untagged deals
      (blank year) fall back to their status-history transition date, so
      legacy deals that were never year-tagged still count instead of
      vanishing.

    * **A month** uses the transition date alone. ``po_award_quarter`` cannot
      express a month, and ``_period_year_quarter()`` widens June to Q2 — so
      bucketing a month on the tags returned April, May and June under a tile
      labelled "June". Three months of revenue under a one-month heading is
      not an approximation, it is a different number.

    The two must not be mixed within one period, which is what the old
    tag-primary/history-fallback pair did on a month: a quarter-tagged deal
    counted in all three months while an untagged one counted only in its own,
    and nothing on the tile revealed which rule had applied to which deal.
    """
    from projects.models import Project, ProjectHistory
    year, quarter = _period_year_quarter(ctx.period)

    base = Project.objects.filter(status__category__in=['won', 'lost'])
    if ctx.region is not None:
        base = base.filter(region=ctx.region)
    if ctx.user is not None:
        base = base.filter(owner=ctx.user)

    if _period_is_month(ctx.period):
        # Date only — every deal placed by the same rule, tagged or not.
        month_ids = set(ProjectHistory.objects.filter(
            changed_at__date__gte=ctx.start, changed_at__date__lt=ctx.end,
            new_status__category__in=['won', 'lost'],
            project__in=base,
        ).values_list('project_id', flat=True))
        return base.filter(id__in=month_ids)

    # Primary: tagged by financial year (+ quarter when the period is a quarter).
    tagged = base.filter(year=year)
    if quarter:
        tagged = tagged.filter(po_award_quarter=quarter)
    ids = set(tagged.values_list('id', flat=True))

    # Fallback: untagged deals (blank year) with a won/lost transition in-window.
    hist = set(ProjectHistory.objects.filter(
        changed_at__date__gte=ctx.start, changed_at__date__lt=ctx.end,
        new_status__category__in=['won', 'lost'],
        project__in=base.filter(year=''),
    ).values_list('project_id', flat=True))

    return base.filter(id__in=(ids | hist))


def compute_sales_win_rate(ctx):
    qs = _outcome_projects(ctx)
    won = qs.filter(status__category='won').count()
    lost = qs.filter(status__category='lost').count()
    decided = won + lost
    if not decided:
        return KPIResult(None, 'no deals tagged won/lost in this period')
    val = (Decimal(won) / Decimal(decided) * Decimal('100')).quantize(Decimal('0.1'))
    return KPIResult(val, f'{won} won of {decided} decided')

def compute_attendance_punctuality(ctx):
    from hr.models import AttendanceRecord
    qs = AttendanceRecord.objects.filter(date__gte=ctx.start, date__lte=ctx.end)
    if ctx.user is not None:
        emp = getattr(ctx.user, 'employee_profile', None)
        qs = qs.filter(employee=emp) if emp else qs.none()
    if ctx.region is not None:
        qs = qs.filter(employee__user__region=ctx.region)
    worked = qs.filter(status__in=['present', 'late'])
    total = worked.count()
    if not total:
        return KPIResult(None, 'no attendance recorded in this period')
    on_time = worked.exclude(status='late').count()
    val = (Decimal(on_time) / Decimal(total) * Decimal('100')).quantize(Decimal('0.1'))
    return KPIResult(val, f'{on_time} on-time of {total} attendance days')



def _resolved_values(projects):
    """Value every project through the SAME ladder the home dashboard uses —
    ``projects.views._resolve_project_sales_value()``: a live costing sheet
    first, then ``actual_sales``, then ``estimated_value``.

    This function exists so the two surfaces cannot drift. Summing
    ``actual_sales`` here while the Won tile resolves through costing gave two
    answers to one question on two pages of the same ERP, and the tile a GM
    reads was the one ignoring every costing sheet on record.

    Yields ``(project, resolved)`` with the sheets prefetched, so a period's
    worth of deals costs a fixed number of queries rather than one per deal.
    """
    from projects.views import _resolve_project_sales_value
    from costing.models import ExchangeRate

    rates = {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}
    qs = projects.select_related('region').prefetch_related(
        'costing_sheets__sections__line_items',
        'costing_sheets__scope_of_work_items',
    )
    for project in qs:
        sheets = list(project.costing_sheets.all())
        for sheet in sheets:
            sheet.set_rates_cache(rates)
        yield project, _resolve_project_sales_value(project, sheets)


def compute_revenue_won(ctx):
    qs = _outcome_projects(ctx).filter(status__category='won')
    n = qs.count()
    if not n:
        return KPIResult(Decimal('0'), 'no won deals in this period',
                         currency=_ctx_currency(ctx))

    display_ccy = _ctx_currency(ctx)
    total = Decimal('0')
    priced = 0
    foreign = set()
    for _project, resolved in _resolved_values(qs):
        amount = resolved.get('amount')
        if not amount:
            continue
        # The ladder reports a costing-sourced amount in the SHEET's own output
        # currency, and actual/estimate in the region's. Within one region those
        # are the same currency in normal use — but say so rather than assume it,
        # because a sheet raised in another currency would otherwise be added
        # straight into the total as if the units matched.
        row_ccy = resolved.get('currency')
        if display_ccy != 'mixed' and row_ccy and row_ccy != display_ccy:
            foreign.add(row_ccy)
        total += Decimal(amount)
        priced += 1

    cov = f'{n} won deal{"s" if n != 1 else ""}'
    if priced < n:
        cov += f' ({n - priced} with no value recorded)'
    if foreign:
        cov += f' — mixed currency: {", ".join(sorted(foreign))}'
    return KPIResult(total, cov, currency=display_ccy)


def compute_forecast_accuracy(ctx):
    """Average accuracy of estimated_value vs actual_sales for won deals that
    HAVE an actual recorded (deals with no actual are excluded, not counted as a
    100% miss — that would read as garbage)."""
    qs = _outcome_projects(ctx).filter(status__category='won')
    won_n = qs.count()
    accs = []
    for est, act in qs.values_list('estimated_value', 'actual_sales'):
        est = _d(est)
        act = _d(act)
        if not est or est == 0 or not act or act <= 0:
            continue
        err = abs(act - est) / est
        accs.append(max(Decimal('0'), Decimal('1') - err))
    if not accs:
        return KPIResult(None, f'0 of {won_n} won deals have actual sales filled')
    val = (sum(accs) / Decimal(len(accs)) * Decimal('100')).quantize(Decimal('0.1'))
    return KPIResult(val, f'{len(accs)} of {won_n} won deals have actuals')


def compute_pipeline_coverage(ctx):
    """Open pipeline value ÷ revenue goal -> coverage multiple. Department-only
    (None per-user). Single currency: region currency when a region is picked,
    else USD via estimated_value_usd."""
    if ctx.user is not None:
        return KPIResult(None, 'department-level only')
    from projects.models import Project
    from django.db.models import Sum
    goal = ctx.targets.get('sales_revenue_achievement')
    if not goal or goal <= 0:
        return KPIResult(None, 'set a revenue goal to compute coverage')
    qs = Project.objects.filter(status__category__in=['active', 'hot_lead'])
    n = qs.count()

    if ctx.region is None:
        # No region picked: estimated_value_usd is the only pre-normalised
        # column, so this path stays on it. Summing per-region estimates across
        # regions would add SAR to GBP.
        open_value = qs.aggregate(s=Sum('estimated_value_usd'))['s'] or Decimal('0')
        return KPIResult((Decimal(open_value) / goal).quantize(Decimal('0.01')),
                         f'{n} open deal{"s" if n != 1 else ""}, all regions',
                         currency='USD')

    # Region picked: value each deal through the same ladder the Won tile and
    # revenue KPI use, so a fully costed open deal counts at its costing total
    # rather than the estimate it was raised with. Reading estimated_value here
    # made the pipeline understate itself precisely on the deals it knew most
    # about.
    qs = qs.filter(region=ctx.region)
    n = qs.count()
    open_value = Decimal('0')
    costed = 0
    for _project, resolved in _resolved_values(qs):
        amount = resolved.get('amount')
        if not amount:
            continue
        open_value += Decimal(amount)
        if resolved.get('source') == 'costing':
            costed += 1

    cov = f'{n} open deal{"s" if n != 1 else ""}'
    if costed:
        cov += f', {costed} costed'
    return KPIResult((open_value / goal).quantize(Decimal('0.01')), cov,
                     currency=ctx.region.currency)


def _submission_ontime(ctx, by='deadline'):
    """On-time submission %. by='deadline' attributes to project owner (sales);
    by='revision' attributes to proposal creator. Denominator = items with a
    submitted proposal + deadline, so missing data never drags the score down."""
    from proposals.models import TechnicalProposal
    qs = TechnicalProposal.objects.filter(status='submitted', project__isnull=False)
    if ctx.region is not None:
        qs = qs.filter(project__region=ctx.region)
    if by == 'deadline' and ctx.user is not None:
        qs = qs.filter(project__owner=ctx.user)
    if by == 'revision' and ctx.user is not None:
        qs = qs.filter(created_by=ctx.user)
    rows = qs.select_related('project').values_list(
        'project_id', 'revision_date', 'project__submission_deadline')
    latest = {}
    for pid, rev, deadline in rows:
        if rev is None or deadline is None:
            continue
        if pid not in latest or rev > latest[pid][0]:
            latest[pid] = (rev, deadline)

    total = ontime = 0
    for pid, (rev, deadline) in latest.items():
        anchor = deadline if by == 'deadline' else rev
        if not (ctx.start <= anchor < ctx.end):
            continue
        total += 1
        if rev <= deadline:
            ontime += 1
    if not total:
        return KPIResult(None, 'no submitted proposals with a deadline this period')
    val = (Decimal(ontime) / Decimal(total) * Decimal('100')).quantize(Decimal('0.1'))
    return KPIResult(val, f'{ontime} on time of {total} submitted')


def compute_ontime_rfq_submission(ctx):
    return _submission_ontime(ctx, by='deadline')


def compute_proposal_submission_ontime(ctx):
    return _submission_ontime(ctx, by='revision')


def compute_proposal_win_rate(ctx):
    """Win rate among projects that had a technical proposal, bucketed by the
    project's year/quarter."""
    from proposals.models import TechnicalProposal
    pq = TechnicalProposal.objects.filter(project__isnull=False)
    if ctx.user is not None:
        pq = pq.filter(created_by=ctx.user)
    proposal_pids = set(pq.values_list('project_id', flat=True))
    if not proposal_pids:
        return KPIResult(None, 'no technical proposals on record')
    qs = _outcome_projects(ctx).filter(pk__in=proposal_pids)
    won = qs.filter(status__category='won').count()
    lost = qs.filter(status__category='lost').count()
    decided = won + lost
    if not decided:
        return KPIResult(None, 'no proposed deals decided in this period')
    val = (Decimal(won) / Decimal(decided) * Decimal('100')).quantize(Decimal('0.1'))
    return KPIResult(val, f'{won} won of {decided} proposed & decided')


def _bom_vs_po_items(ctx):
    """PO line items issued in the window that trace back to a BOM item,
    narrowed by region (PO's project) and creator (user)."""
    from procurement.models import PurchaseOrderItem
    qs = (PurchaseOrderItem.objects
          .filter(purchase_order__po_date__gte=ctx.start,
                  purchase_order__po_date__lt=ctx.end,
                  source_bom_item__isnull=False))
    if ctx.region is not None:
        qs = qs.filter(purchase_order__project__region=ctx.region)
    if ctx.user is not None:
        qs = qs.filter(purchase_order__created_by=ctx.user)
    return qs.select_related('source_bom_item')


def _bom_vs_po_totals(ctx):
    planned = Decimal('0')
    actual = Decimal('0')
    n = 0
    for it in _bom_vs_po_items(ctx):
        qty = _d(it.quantity) or Decimal('0')
        planned += (_d(it.source_bom_item.unit_cost_sar) or Decimal('0')) * qty
        actual += (_d(it.rate_per_unit) or Decimal('0')) * qty
        n += 1
    return planned, actual, n


def compute_cost_savings(ctx):
    planned, actual, n = _bom_vs_po_totals(ctx)
    if n == 0:
        return KPIResult(None, 'no procured BOM items this period', currency='SAR')
    return KPIResult((planned - actual).quantize(Decimal('0.01')),
                     f'{n} procured BOM line item{"s" if n != 1 else ""}',
                     currency='SAR')


def compute_ppv(ctx):
    planned, actual, n = _bom_vs_po_totals(ctx)
    if planned == 0:
        return KPIResult(None, 'no procured BOM items this period')
    val = ((actual - planned) / planned * Decimal('100')).quantize(Decimal('0.1'))
    return KPIResult(val, f'{n} procured BOM line item{"s" if n != 1 else ""}')


def compute_pr_to_po_cycle(ctx):
    from procurement.models import PurchaseOrder
    from costing.models import CostingSheet
    pos = PurchaseOrder.objects.filter(
        po_date__gte=ctx.start, po_date__lt=ctx.end, project__isnull=False)
    if ctx.region is not None:
        pos = pos.filter(project__region=ctx.region)
    if ctx.user is not None:
        pos = pos.filter(created_by=ctx.user)
    pos = pos.values_list('project_id', 'po_date')
    approved = {}
    for pid, ts in (CostingSheet.objects
                    .filter(finance_approved_at__isnull=False)
                    .values_list('project_id', 'finance_approved_at')):
        if pid not in approved or ts < approved[pid]:
            approved[pid] = ts
    deltas = []
    for pid, po_date in pos:
        ts = approved.get(pid)
        if not ts:
            continue
        d = (po_date - ts.date()).days
        if d >= 0:
            deltas.append(d)
    if not deltas:
        return KPIResult(None, 'no POs with a finance-approval date this period')
    val = (Decimal(sum(deltas)) / Decimal(len(deltas))).quantize(Decimal('0.1'))
    return KPIResult(val, f'{len(deltas)} PO{"s" if len(deltas) != 1 else ""} measured')


def compute_ontime_delivery(ctx):
    from procurement.models import POSummaryEntry
    from django.db.models import F
    base = POSummaryEntry.objects.filter(
        delivery_actual__gte=ctx.start, delivery_actual__lt=ctx.end,
        delivery_plan__isnull=False)
    if ctx.region is not None:
        base = base.filter(purchase_order_item__purchase_order__project__region=ctx.region)
    if ctx.user is not None:
        base = base.filter(purchase_order_item__purchase_order__created_by=ctx.user)
    total = base.count()
    if not total:
        return KPIResult(None, 'no deliveries with plan & actual dates this period')
    ontime = base.filter(delivery_actual__lte=F('delivery_plan')).count()
    val = (Decimal(ontime) / Decimal(total) * Decimal('100')).quantize(Decimal('0.1'))
    return KPIResult(val, f'{ontime} on time of {total} delivered')


def _ctx_currency(ctx):
    """Display currency for money KPIs: the region's currency when one is picked,
    else 'mixed' (multiple regions blended — pick a region for a clean figure)."""
    if ctx.region is not None:
        return ctx.region.currency
    return 'mixed'


# ══════════════════════════════════════════════════════════════════════════════
# The registry.
# ══════════════════════════════════════════════════════════════════════════════
KPI_DEFINITIONS = [
    # ── Sales ────────────────────────────────────────────────────────────────
    KPI('sales_revenue_achievement', SALES, 'Revenue achievement', CURRENCY, 'higher',
        target=None, source='auto', compute=compute_revenue_won,
        help='Actual sales of deals won this period (by year/quarter), vs the revenue goal.',
        target_help='Revenue goal for this period'),
    KPI('sales_pipeline_coverage', SALES, 'Pipeline coverage', RATIO, 'higher',
        target=Decimal('3'), source='auto', compute=compute_pipeline_coverage,
        help='Open pipeline value ÷ revenue goal. Needs the revenue goal set.'),
    KPI('sales_win_rate', SALES, 'Win rate', PERCENT, 'higher',
        target=Decimal('32'), source='auto', compute=compute_sales_win_rate,
        help='Won ÷ (won + lost), bucketed by the deal year/quarter.'),
    KPI('sales_forecast_accuracy', SALES, 'Forecast accuracy', PERCENT, 'higher',
        target=Decimal('90'), source='auto', compute=compute_forecast_accuracy,
        help='100% − mean error of estimated vs actual on won deals with actuals filled.'),
    KPI('sales_ontime_rfq', SALES, 'On-time RFQ submission', PERCENT, 'higher',
        target=Decimal('100'), source='auto', compute=compute_ontime_rfq_submission,
        help='Share of deals (deadline this period) whose proposal was submitted on time.'),

    # ── Proposal ─────────────────────────────────────────────────────────────
    KPI('proposal_submission_ontime', PROPOSAL, 'Submission on-time', PERCENT, 'higher',
        target=Decimal('100'), source='auto', compute=compute_proposal_submission_ontime,
        help='Proposals submitted this period vs their RFQ deadline.'),
    KPI('proposal_win_rate', PROPOSAL, 'Win rate', PERCENT, 'higher',
        target=Decimal('35'), source='auto', compute=compute_proposal_win_rate,
        help='Win rate among deals that had a technical proposal.'),
    KPI('proposal_rfq_review_time', PROPOSAL, 'RFQ review time', HOURS, 'lower',
        target=Decimal('24'), source='manual',
        help='Hours from RFQ receipt to review complete. Manual entry.'),
    KPI('proposal_kickoff_time', PROPOSAL, 'Kickoff time', HOURS, 'lower',
        target=Decimal('24'), source='manual',
        help='Hours from award/decision to kickoff. Manual entry.'),
    KPI('proposal_margin_deviation', PROPOSAL, 'Margin deviation', PERCENT, 'lower',
        target=Decimal('10'), source='manual',
        help='Absolute % deviation of realised vs quoted margin. Manual entry.'),
    KPI('proposal_post_award_errors', PROPOSAL, 'Post-award errors', COUNT, 'lower',
        target=Decimal('0'), source='manual',
        help='Count of post-award corrections/errors. Manual entry.'),

    # ── Procurement ──────────────────────────────────────────────────────────
    KPI('proc_cost_savings', PROCUREMENT, 'Cost savings achieved', CURRENCY, 'higher',
        target=None, source='auto', compute=compute_cost_savings,
        help='Planned BOM cost − actual PO cost on procured BOM items (SAR).',
        target_help='Savings target (SAR), optional'),
    KPI('proc_ppv', PROCUREMENT, 'Purchase price variance', PERCENT, 'lower',
        target=Decimal('0'), source='auto', compute=compute_ppv,
        help='(Actual − planned) ÷ planned on procured BOM items. Negative is good.'),
    KPI('proc_pr_to_po_cycle', PROCUREMENT, 'PR → PO cycle', DAYS, 'lower',
        target=Decimal('5'), source='auto', compute=compute_pr_to_po_cycle,
        help='Avg days from finance approval (release) to PO issuance.'),
    KPI('proc_rfq_response_time', PROCUREMENT, 'RFQ response time', HOURS, 'lower',
        target=Decimal('48'), source='manual',
        help='Hours for vendors to respond to RFQs. Manual entry.'),
    KPI('proc_ontime_delivery', PROCUREMENT, 'On-time delivery', PERCENT, 'higher',
        target=Decimal('95'), source='auto', compute=compute_ontime_delivery,
        help='PO summary rows delivered on/before plan this period.'),
    KPI('proc_supplier_performance', PROCUREMENT, 'Supplier performance', SCORE, 'higher',
        target=Decimal('90'), source='manual',
        help='Supplier scorecard (0–100). Manual entry.'),
    KPI('proc_budget_compliance', PROCUREMENT, 'Budget compliance', PERCENT, 'higher',
        target=Decimal('100'), source='manual',
        help='Spend within approved budget. Manual entry.'),
    KPI('proc_emergency_purchases', PROCUREMENT, 'Emergency purchases', PERCENT, 'lower',
        target=Decimal('10'), source='manual',
        help='Share of purchases made as emergencies. Manual entry.'),
    KPI('proc_policy_compliance', PROCUREMENT, 'Policy compliance', PERCENT, 'higher',
        target=Decimal('100'), source='manual',
        help='Procurement policy adherence. Manual entry.'),
    KPI('proc_stakeholder_satisfaction', PROCUREMENT, 'Stakeholder satisfaction', SCORE, 'higher',
        target=Decimal('90'), source='manual',
        help='Internal stakeholder satisfaction (0–100). Manual entry.'),
    KPI('hr_attendance_punctuality', HR, 'Attendance punctuality', PERCENT, 'higher',
        target=Decimal('95'), source='auto', compute=compute_attendance_punctuality,
        help='Share of attendance days marked Present (not Late) this period.'),
]

KPI_BY_KEY = {k.key: k for k in KPI_DEFINITIONS}

# Auto KPIs that can be sliced per individual (have a clean owner/creator link).
USER_ATTRIBUTABLE_KEYS = {
    'sales_revenue_achievement', 'sales_win_rate', 'sales_forecast_accuracy',
    'sales_ontime_rfq',
    'proposal_submission_ontime', 'proposal_win_rate',
    'proc_cost_savings', 'proc_ppv', 'proc_pr_to_po_cycle', 'proc_ontime_delivery',
    'hr_attendance_punctuality',
}


def kpis_for_department(dept):
    return [k for k in KPI_DEFINITIONS if k.department == dept]


def user_attributable_kpis():
    return [k for k in KPI_DEFINITIONS if k.key in USER_ATTRIBUTABLE_KEYS]


def evaluate(kpi, value, target):
    """Status of a value vs its target: 'on' | 'near' | 'off' | 'na'."""
    if value is None or target is None:
        return 'na'
    value = _d(value)
    target = _d(target)
    if value is None or target is None:
        return 'na'
    if kpi.direction == 'lower':
        if value <= target:
            return 'on'
        if value <= target * Decimal('1.1'):
            return 'near'
        return 'off'
    if value >= target:
        return 'on'
    if value >= target * Decimal('0.9'):
        return 'near'
    return 'off'


def achievement_pct(kpi, value, target):
    """Achievement as a % of target for the progress bar (clamped 0–150), or None."""
    value = _d(value)
    target = _d(target)
    if value is None or target is None or target == 0:
        return None
    if kpi.direction == 'lower':
        if value <= 0:
            pct = Decimal('100')
        else:
            pct = (target / value) * Decimal('100')
    else:
        pct = (value / target) * Decimal('100')
    pct = max(Decimal('0'), min(Decimal('150'), pct))
    return int(pct)
