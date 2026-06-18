"""The KPI registry: management's Sales / Proposal / Procurement KPIs declared
in code, each mapped to either a live ERP computation or manual entry.

This mirrors the dashboard "fat helper" pattern used elsewhere: the dashboard
view stays thin and just walks `KPI_DEFINITIONS`, calling `.compute()` for auto
KPIs and reading a KPIEntry for manual ones.

Each KPI declares a `direction` used to colour the tile against its target:
    'higher' -> at/above target is good (win rate, on-time %)
    'lower'  -> at/below target is good (cycle days, errors, PPV)
A value of None means "no data this period" (or, for auto KPIs that need a goal,
no target was set yet) and renders as a neutral 'n/a' tile.

Compute functions all take (start, end, targets, user=None):
  * `targets` maps kpi_key -> the manual target Decimal for the same period, so
    a KPI like pipeline coverage can divide the live pipeline by the revenue
    goal management entered.
  * `user` (optional) restricts the computation to records that person owns /
    created, powering the per-person scorecard. None = whole-department.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional


# ── Departments ──────────────────────────────────────────────────────────────
SALES = 'sales'
PROPOSAL = 'proposal'
PROCUREMENT = 'procurement'

DEPARTMENTS = [
    (SALES, 'Sales'),
    (PROPOSAL, 'Proposal'),
    (PROCUREMENT, 'Procurement'),
]

# ── Units (drive display formatting in the template) ─────────────────────────
PERCENT = 'percent'
RATIO = 'ratio'
DAYS = 'days'
HOURS = 'hours'
COUNT = 'count'
CURRENCY = 'currency'
SCORE = 'score'


@dataclass(frozen=True)
class KPI:
    key: str
    department: str
    label: str
    unit: str
    direction: str                      # 'higher' | 'lower'
    target: Optional[Decimal] = None    # default target; overridable per period
    source: str = 'manual'              # 'auto' | 'manual'
    compute: Optional[Callable] = None  # (start, end, targets, user=None) -> Decimal|None
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


# ══════════════════════════════════════════════════════════════════════════════
# Compute helpers — small, focused queries over the existing ERP models.
# Each accepts an optional `owner` / `creator` to slice to one person's work.
# ══════════════════════════════════════════════════════════════════════════════

def _won_lost_in_period(start, end, restrict_project_ids=None, owner=None):
    """({won_pids}, {lost_pids}) — projects whose status transitioned into a
    won/lost category within [start, end). The *latest* such transition in the
    window decides the outcome (a project flipped won then lost counts as lost).
    `owner` limits to projects owned by that user.
    """
    from projects.models import ProjectHistory
    qs = (ProjectHistory.objects
          .filter(changed_at__date__gte=start, changed_at__date__lt=end,
                  new_status__category__in=['won', 'lost']))
    if owner is not None:
        qs = qs.filter(project__owner=owner)
    qs = (qs.order_by('project_id', '-changed_at')
          .values_list('project_id', 'new_status__category'))
    outcome = {}
    for pid, cat in qs:
        if restrict_project_ids is not None and pid not in restrict_project_ids:
            continue
        if pid not in outcome:          # first seen = latest (ordered desc)
            outcome[pid] = cat
    won = {pid for pid, c in outcome.items() if c == 'won'}
    lost = {pid for pid, c in outcome.items() if c == 'lost'}
    return won, lost


def _win_rate(start, end, restrict_project_ids=None, owner=None):
    won, lost = _won_lost_in_period(start, end, restrict_project_ids, owner)
    decided = len(won) + len(lost)
    if not decided:
        return None
    return (Decimal(len(won)) / Decimal(decided) * Decimal('100')).quantize(Decimal('0.1'))


def compute_revenue_won(start, end, targets, user=None):
    """Actual sales (SAR) of projects won in the period. Tile shows this against
    the revenue goal management enters as the target."""
    from projects.models import Project
    from django.db.models import Sum
    won, _ = _won_lost_in_period(start, end, owner=user)
    if not won:
        return Decimal('0')
    total = Project.objects.filter(pk__in=won).aggregate(s=Sum('actual_sales'))['s']
    return _d(total) or Decimal('0')


def compute_pipeline_coverage(start, end, targets, user=None):
    """Open pipeline value ÷ the revenue goal -> coverage multiple (e.g. 3.2x).
    Department-only: returns None per-user (no per-person goal) or until a
    revenue goal is set for the period."""
    if user is not None:
        return None
    from projects.models import Project
    from django.db.models import Sum
    goal = targets.get('sales_revenue_achievement')
    if not goal or goal <= 0:
        return None
    open_value = Project.objects.filter(
        status__category__in=['active', 'hot_lead']
    ).aggregate(s=Sum('estimated_value'))['s'] or Decimal('0')
    return (Decimal(open_value) / goal).quantize(Decimal('0.01'))


def compute_sales_win_rate(start, end, targets, user=None):
    return _win_rate(start, end, owner=user)


def compute_forecast_accuracy(start, end, targets, user=None):
    """Average accuracy of estimated_value vs actual_sales for won projects,
    as 100% − mean absolute percentage error (floored at 0)."""
    from projects.models import Project
    won, _ = _won_lost_in_period(start, end, owner=user)
    if not won:
        return None
    accs = []
    for est, act in Project.objects.filter(pk__in=won).values_list('estimated_value', 'actual_sales'):
        est = _d(est)
        act = _d(act)
        if not est or est == 0 or act is None:
            continue
        err = abs(act - est) / est
        accs.append(max(Decimal('0'), Decimal('1') - err))
    if not accs:
        return None
    return (sum(accs) / Decimal(len(accs)) * Decimal('100')).quantize(Decimal('0.1'))


def _submission_ontime(start, end, by='deadline', owner=None, creator=None):
    """Shared on-time submission %.

    by='deadline' -> projects whose submission_deadline falls in the window;
                     on-time if a submitted proposal's revision_date <= deadline.
                     `owner` limits to that project owner (sales attribution).
    by='revision' -> proposals submitted (revision_date) in the window with a
                     deadline set; on-time if revision_date <= deadline.
                     `creator` limits to that proposal's created_by.
    Denominator only counts items that actually have a submitted proposal +
    deadline, so missing data never silently drags the score down.
    """
    from proposals.models import TechnicalProposal
    qs = TechnicalProposal.objects.filter(status='submitted', project__isnull=False)
    if owner is not None:
        qs = qs.filter(project__owner=owner)
    if creator is not None:
        qs = qs.filter(created_by=creator)
    submitted = qs.select_related('project').values_list(
        'project_id', 'revision_date', 'project__submission_deadline')
    # Latest submitted proposal per project wins; build {pid: (rev, deadline)}.
    latest = {}
    for pid, rev, deadline in submitted:
        if rev is None or deadline is None:
            continue
        if pid not in latest or rev > latest[pid][0]:
            latest[pid] = (rev, deadline)

    total = ontime = 0
    for pid, (rev, deadline) in latest.items():
        anchor = deadline if by == 'deadline' else rev
        if not (start <= anchor < end):
            continue
        total += 1
        if rev <= deadline:
            ontime += 1
    if not total:
        return None
    return (Decimal(ontime) / Decimal(total) * Decimal('100')).quantize(Decimal('0.1'))


def compute_ontime_rfq_submission(start, end, targets, user=None):
    return _submission_ontime(start, end, by='deadline', owner=user)


def compute_proposal_submission_ontime(start, end, targets, user=None):
    return _submission_ontime(start, end, by='revision', creator=user)


def compute_proposal_win_rate(start, end, targets, user=None):
    from proposals.models import TechnicalProposal
    qs = TechnicalProposal.objects.filter(project__isnull=False)
    if user is not None:
        qs = qs.filter(created_by=user)
    proposal_pids = set(qs.values_list('project_id', flat=True))
    if not proposal_pids:
        return None
    return _win_rate(start, end, restrict_project_ids=proposal_pids)


def _bom_vs_po_totals(start, end, creator=None):
    """(planned_total, actual_total) SAR over PO line items issued in the window
    that trace back to a costing BOM item. planned = BOM unit cost × qty,
    actual = PO rate × qty. `creator` limits to POs that user created."""
    from procurement.models import PurchaseOrderItem
    qs = (PurchaseOrderItem.objects
          .filter(purchase_order__po_date__gte=start,
                  purchase_order__po_date__lt=end,
                  source_bom_item__isnull=False))
    if creator is not None:
        qs = qs.filter(purchase_order__created_by=creator)
    items = qs.select_related('source_bom_item')
    planned = Decimal('0')
    actual = Decimal('0')
    for it in items:
        qty = _d(it.quantity) or Decimal('0')
        planned += (_d(it.source_bom_item.unit_cost_sar) or Decimal('0')) * qty
        actual += (_d(it.rate_per_unit) or Decimal('0')) * qty
    return planned, actual


def compute_cost_savings(start, end, targets, user=None):
    """Planned BOM cost − actual PO cost (SAR). Positive = money saved."""
    planned, actual = _bom_vs_po_totals(start, end, creator=user)
    if planned == 0 and actual == 0:
        return None
    return (planned - actual).quantize(Decimal('0.01'))


def compute_ppv(start, end, targets, user=None):
    """Purchase Price Variance %: (actual − planned) / planned × 100.
    Negative is favourable (paid less than the BOM standard)."""
    planned, actual = _bom_vs_po_totals(start, end, creator=user)
    if planned == 0:
        return None
    return ((actual - planned) / planned * Decimal('100')).quantize(Decimal('0.1'))


def compute_pr_to_po_cycle(start, end, targets, user=None):
    """Avg working-window days from costing finance-approval (release for
    procurement) to PO issuance, for POs issued in the period."""
    from procurement.models import PurchaseOrder
    from costing.models import CostingSheet
    deltas = []
    pos = PurchaseOrder.objects.filter(
        po_date__gte=start, po_date__lt=end, project__isnull=False)
    if user is not None:
        pos = pos.filter(created_by=user)
    pos = pos.values_list('project_id', 'po_date')
    # Earliest finance-approval per project.
    approved = {}
    for pid, ts in (CostingSheet.objects
                    .filter(finance_approved_at__isnull=False)
                    .values_list('project_id', 'finance_approved_at')):
        if pid not in approved or ts < approved[pid]:
            approved[pid] = ts
    for pid, po_date in pos:
        ts = approved.get(pid)
        if not ts:
            continue
        d = (po_date - ts.date()).days
        if d >= 0:
            deltas.append(d)
    if not deltas:
        return None
    return (Decimal(sum(deltas)) / Decimal(len(deltas))).quantize(Decimal('0.1'))


def compute_ontime_delivery(start, end, targets, user=None):
    """% of PO summary rows delivered on/before plan, among rows with an actual
    delivery date in the period."""
    from procurement.models import POSummaryEntry
    from django.db.models import F
    base = POSummaryEntry.objects.filter(
        delivery_actual__gte=start, delivery_actual__lt=end,
        delivery_plan__isnull=False,
    )
    if user is not None:
        base = base.filter(purchase_order_item__purchase_order__created_by=user)
    total = base.count()
    if not total:
        return None
    ontime = base.filter(delivery_actual__lte=F('delivery_plan')).count()
    return (Decimal(ontime) / Decimal(total) * Decimal('100')).quantize(Decimal('0.1'))


# ══════════════════════════════════════════════════════════════════════════════
# The registry.
# ══════════════════════════════════════════════════════════════════════════════
KPI_DEFINITIONS = [
    # ── Sales ────────────────────────────────────────────────────────────────
    KPI('sales_revenue_achievement', SALES, 'Revenue achievement', CURRENCY, 'higher',
        target=None, source='auto', compute=compute_revenue_won,
        help='Actual sales of projects won this period, against the revenue goal.',
        target_help='Revenue goal (SAR) for this period'),
    KPI('sales_pipeline_coverage', SALES, 'Pipeline coverage', RATIO, 'higher',
        target=Decimal('3'), source='auto', compute=compute_pipeline_coverage,
        help='Open pipeline value ÷ revenue goal. Needs the revenue goal set.'),
    KPI('sales_win_rate', SALES, 'Win rate', PERCENT, 'higher',
        target=Decimal('32'), source='auto', compute=compute_sales_win_rate,
        help='Won ÷ (won + lost) by status transitions in the period.'),
    KPI('sales_forecast_accuracy', SALES, 'Forecast accuracy', PERCENT, 'higher',
        target=Decimal('90'), source='auto', compute=compute_forecast_accuracy,
        help='100% − mean error of estimated vs actual value on won deals (±10% → ≥90%).'),
    KPI('sales_ontime_rfq', SALES, 'On-time RFQ submission', PERCENT, 'higher',
        target=Decimal('100'), source='auto', compute=compute_ontime_rfq_submission,
        help='Share of deals (deadline this period) whose proposal was submitted on time.'),

    # ── Proposal ─────────────────────────────────────────────────────────────
    KPI('proposal_submission_ontime', PROPOSAL, 'Submission on-time', PERCENT, 'higher',
        target=Decimal('100'), source='auto', compute=compute_proposal_submission_ontime,
        help='Proposals submitted this period vs their RFQ deadline.'),
    KPI('proposal_win_rate', PROPOSAL, 'Win rate', PERCENT, 'higher',
        target=Decimal('35'), source='auto', compute=compute_proposal_win_rate,
        help='Win rate among projects that had a technical proposal.'),
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
]

KPI_BY_KEY = {k.key: k for k in KPI_DEFINITIONS}

# Auto KPIs that can be sliced per individual (have a clean owner/creator link).
# Pipeline coverage needs a per-period revenue goal we don't track per person,
# so it stays department-only and is excluded here.
USER_ATTRIBUTABLE_KEYS = {
    'sales_revenue_achievement', 'sales_win_rate', 'sales_forecast_accuracy',
    'sales_ontime_rfq',
    'proposal_submission_ontime', 'proposal_win_rate',
    'proc_cost_savings', 'proc_ppv', 'proc_pr_to_po_cycle', 'proc_ontime_delivery',
}


def kpis_for_department(dept):
    return [k for k in KPI_DEFINITIONS if k.department == dept]


def user_attributable_kpis():
    return [k for k in KPI_DEFINITIONS if k.key in USER_ATTRIBUTABLE_KEYS]


def evaluate(kpi, value, target):
    """Status of a value vs its target: 'on' | 'near' | 'off' | 'na'.

    'near' is the within-10%-of-target amber band. Lower-is-better KPIs invert
    the comparison. A None value or missing target yields 'na'.
    """
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
    # higher-is-better
    if value >= target:
        return 'on'
    if value >= target * Decimal('0.9'):
        return 'near'
    return 'off'


def achievement_pct(kpi, value, target):
    """Achievement as a % of target for the progress bar, or None.
    Clamped to 0–150 so a runaway value can't blow out the layout."""
    value = _d(value)
    target = _d(target)
    if value is None or target is None or target == 0:
        return None
    if kpi.direction == 'lower':
        # Lower is better: full bar when at/below target, shrinking as it exceeds.
        if value <= 0:
            pct = Decimal('100')
        else:
            pct = (target / value) * Decimal('100')
    else:
        pct = (value / target) * Decimal('100')
    pct = max(Decimal('0'), min(Decimal('150'), pct))
    return int(pct)
