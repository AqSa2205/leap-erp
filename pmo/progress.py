"""Where each project stands — completion, cash in, cash out.

Pure functions over querysets, no writes, in the shape of
`procurement/budget_status.py`. Everything here is derived. Nothing a person
types is recomputed and nothing computed is stored, which is the single change
that removes the class of bug the workbook had: a total that summed the wrong
range, or a cross-reference to a cell that moved, and no way to notice.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import prefetch_related_objects

from procurement.budget_status import approved_budgets_for, commitment, exchange_rates

from .models import ONE, WEIGHTAGE_TOLERANCE, ZERO


def leaves(project):
    """The rows that carry weight — activities with no children under them.

    One query for the whole tree rather than a descent per row.
    """
    rows = list(project.milestones.all())
    parent_ids = {r.parent_id for r in rows if r.parent_id is not None}
    return [r for r in rows if r.pk not in parent_ids]


def validate_weightages(project):
    """Weights that do not add up, as a list of problems to show on screen.

    Two rules, both taken from how the sheets are actually filled in rather
    than from the column heading — and the sheets follow two conventions, both
    legitimate. On the MASCO sheet the parent carries the weight (0.10) and its
    children divide it (0.05 + 0.05). On the ZULF sheets the parent is blank
    and the children carry all of it. So:

      * **the leaves must sum to 1.00** — this holds either way, and the leaves
        are what completion is actually computed from; and
      * a parent that does carry a weight must agree with its children, since
        disagreeing means one of the two numbers is wrong.

    A rule that the top-level rows sum to 1.00 would be wrong for half the
    sheets and would put a permanent warning on them.

    The workbook checked none of this. A project whose weights summed to 0.97
    could never reach 100%, and the only place it showed was a total that
    quietly stopped short.

    Returned rather than raised — a half-entered WBS is a normal thing to be
    looking at, and the screen should show the problem, not refuse to render.
    """
    rows = list(project.milestones.all())
    by_parent = {}
    for row in rows:
        if row.parent_id is not None:
            by_parent.setdefault(row.parent_id, []).append(row)

    problems = []
    for parent in rows:
        children = by_parent.get(parent.pk)
        # A parent left blank is the other convention, not a mistake.
        if not children or parent.weightage == ZERO:
            continue
        total = sum((c.weightage for c in children), ZERO)
        if abs(total - parent.weightage) > WEIGHTAGE_TOLERANCE:
            problems.append({
                'kind': 'children',
                'parent': parent,
                'total': total,
                'expected': parent.weightage,
            })

    carrying = [r for r in rows if r.pk not in by_parent]
    if carrying:
        total = sum((r.weightage for r in carrying), ZERO)
        if abs(total - ONE) > WEIGHTAGE_TOLERANCE:
            problems.append({
                'kind': 'project',
                'parent': None,
                'total': total,
                'expected': ONE,
            })
    return problems


def project_completion(project):
    """How far along the project is, 0–1.

    Weight times progress, summed over the leaves. Parents are skipped rather
    than added — their weight is their children's, so counting both would
    double it.
    """
    return sum((row.weightage * row.completed_fraction for row in leaves(project)), ZERO)


def total_weightage(project):
    return sum((row.weightage for row in leaves(project)), ZERO)


def _top_level_status(project):
    """Each top-level milestone (the numbered 1, 2, 3 rows) paired with its
    children and its own aggregate (total, completed) weightage.

    Built from one already-fetched list (`project.milestones.all()`, which
    the board's queryset prefetches for every project up front) rather than
    from `ProjectMilestone.total_weightage` / `.pending_weightage` — those
    properties call `.children.all()` per row, which is fine reading one
    project's page but re-queries the database once per top-level row for
    every project on the board otherwise. Same parent/children-map trick
    `validate_weightages` already uses above.
    """
    rows = list(project.milestones.all())
    by_parent = {}
    for row in rows:
        if row.parent_id is not None:
            by_parent.setdefault(row.parent_id, []).append(row)

    out = []
    for top in rows:
        if top.parent_id is not None:
            continue
        children = by_parent.get(top.pk) or []
        if children:
            total = sum((c.weightage for c in children), ZERO)
            completed = sum((c.weightage * c.completed_fraction for c in children), ZERO)
        else:
            total = top.weightage
            completed = top.weightage * top.completed_fraction
        out.append((top, children, total, completed))
    return out


def milestone_counts(project):
    """(achieved, pending) count of TOP-LEVEL milestones — the numbered
    1, 2, 3 rows a PM reads off at a glance, not every leaf activity
    underneath them."""
    statuses = _top_level_status(project)
    achieved = sum(
        1 for _top, _children, total, completed in statuses
        if total > ZERO and (total - completed) <= WEIGHTAGE_TOLERANCE
    )
    return achieved, len(statuses) - achieved


def action_by(project):
    """Whose court the project sits in, right now — the project's own
    region (e.g. "LNA" for Leap Networks Arabia).

    Simpler than tagging a responsible party per activity, and always
    populated, since every project already has a region. `ProjectMilestone.
    responsible_party` still exists on the model for finer-grained tagging
    later, but nothing reads it for this column today.
    """
    return project.region.code if project.region_id else ''


def _milestone_finish_date(row):
    """Best available 'done on' date for one milestone row: the imported
    completion_date if the workbook set one, else the date of the progress
    entry that most recently touched it.

    Marking an activity 100% through the web Delivery Board only ever
    updates `completed_fraction` (see `update_progress` below) — it never
    backfills `completion_date`, which is populated solely by the workbook
    import. Falling back to the progress log keeps "when did this finish"
    answerable either way, rather than going blank for anything finished
    through the web UI.
    """
    if row.completion_date:
        return row.completion_date
    entries = list(row.progress_entries.all())
    if not entries:
        return None
    return timezone.localtime(max(e.recorded_at for e in entries)).date()


def milestone_checklist(project):
    """One line per TOP-LEVEL milestone — done or not, how far along, and
    the date it actually finished (blank while still in progress)."""
    out = []
    for top, children, total, completed in _top_level_status(project):
        is_done = total > ZERO and (total - completed) <= WEIGHTAGE_TOLERANCE
        pct = (completed / total * Decimal('100')) if total > ZERO else ZERO
        if not is_done:
            finish_date = None
        elif children:
            dates = [_milestone_finish_date(c) for c in children]
            dates = [d for d in dates if d]
            finish_date = max(dates) if dates else None
        else:
            finish_date = _milestone_finish_date(top)
        out.append({
            'label': top.activity,
            'number': top.number,
            'is_done': is_done,
            'pct': pct,
            'finish_date': finish_date,
        })
    return out


def expected_completion_date(project):
    """The real-world answer to "when will this actually finish" — derived,
    never typed.

    Once a project is 100% complete, this is simply the date the last
    activity was actually marked done. While it's still in progress, it's a
    straight-line projection from the project's own real pace of work: total
    progress made so far, divided by the calendar days it took to make it,
    extended forward to the finish line. `None` when there isn't enough
    history yet to say anything honest — no fabricated date is better than a
    wrong one.
    """
    completion = project_completion(project)
    if completion >= ONE:
        dates = [_milestone_finish_date(row) for row in leaves(project)]
        dates = [d for d in dates if d]
        return max(dates) if dates else None

    entries = [
        entry
        for row in project.milestones.all()
        for entry in row.progress_entries.all()
    ]
    if not entries or completion <= ZERO:
        return None

    started = timezone.localtime(min(e.recorded_at for e in entries)).date()
    today = timezone.localdate()
    days_elapsed = (today - started).days
    if days_elapsed <= 0:
        return None

    daily_rate = completion / Decimal(days_elapsed)
    days_remaining = (ONE - completion) / daily_rate
    try:
        return today + timedelta(days=int(days_remaining))
    except OverflowError:
        # A sliver of progress (weightage's smallest step is 0.0001) over a
        # long-running project projects a "finish line" centuries out —
        # beyond what `date` can represent. Same rule as everywhere else in
        # this function: no fabricated date is better than a wrong one.
        return None


def delay_months(expected, planned):
    """Whole months the expected finish sits past the planned one.

    Zero (never negative) when running early or on time — a delay figure has
    no business reading negative on a board someone glances at.
    """
    if not expected or not planned:
        return None
    months = (expected.year - planned.year) * 12 + (expected.month - planned.month)
    # A month hasn't actually elapsed yet if we haven't reached the day of
    # month `planned` fell on — same rule as counting whole years of age.
    # Without this, one day past a month-end (e.g. planned 31 Jan, expected
    # 1 Feb) overstates a full month of delay.
    if expected.day < planned.day:
        months -= 1
    return max(months, 0)


def cash_in(project):
    """Money actually received, from finance's own records.

    Read from the payment milestones rather than retyped into a budget sheet.
    Only milestones with an actual receipt date count — an invoice submitted is
    not cash in, and the workbook's Cash In column blurred the two.
    """
    finance = getattr(project, 'finance', None)
    if finance is None:
        return ZERO
    return sum(
        (m.amount for m in finance.milestones.all()
         if m.actual_payment_receive_date is not None),
        ZERO,
    )


def cash_out(project, rates=None, as_of=None):
    """Money paid out to vendors, from finance's own cash-outflow schedule.

    Read from finance's CashOutflowRow schedule (Finance -> Cash Outflow), not
    from purchase orders, so the board and the finance page cannot disagree.
    Only staged payments dated on or before `as_of` (today) count - a payment
    scheduled for next month has not left yet - mirroring cash_in, which only
    counts milestones actually received.

    Ex-VAT (`amount`, not `total_amount`): the approved budget carries no VAT,
    and Over/Under Budget compares the two directly.

    `rates` is unused (finance records rows in SAR); kept so existing callers
    that pass it still work.
    """
    as_of = as_of or timezone.localdate()
    total = ZERO
    for row in project.cash_outflow_rows.all():
        paid_pct = sum(
            (getattr(row, f'pct_{i}') or ZERO
             for i in range(1, 7)
             if getattr(row, f'date_{i}') is not None
             and getattr(row, f'date_{i}') <= as_of),
            ZERO,
        )
        total += row.amount * paid_pct / Decimal('100')
    return total.quantize(Decimal('0.01'))


BUDGET_STATUS_LABELS = {
    'over': 'Over Budget',
    'under': 'Under Budget',
    'within': 'Within Budget',
    'none': 'No budget',
}
FINANCIAL_STATUS_LABELS = {
    'positive': '+ Positive',
    'negative': '\u2212 Negative',
    'balanced': '= Balanced',
}


def budget_status_of(budget, spent):
    """Cash Out against the approved budget, the way the PM's Excel worked
    it out (Over / Under / Within Budget).

    No approved budget is 'none', never 'under': a project with nothing to
    compare against has not come in under anything.
    """
    if budget is None or budget <= ZERO:
        return 'none'
    if spent > budget:
        return 'over'
    if spent < budget:
        return 'under'
    return 'within'


def financial_status_of(net):
    """Cash In minus Cash Out as a word: positive, negative or balanced."""
    if net < ZERO:
        return 'negative'
    if net > ZERO:
        return 'positive'
    return 'balanced'


def board_row(project, rates=None, budgets=None):
    """One project's line on the overview board.

    Every column is derived. There is no manual total to fall out of step and
    no cell reference to break when a row is inserted.
    """
    finance = getattr(project, 'finance', None)
    completion = project_completion(project)
    received = cash_in(project)
    spent = cash_out(project, rates)
    achieved, pending = milestone_counts(project)
    planned_end = finance.estimated_end_date if finance else None
    expected_end = expected_completion_date(project)

    entries = [
        entry
        for row in project.milestones.all()
        for entry in row.progress_entries.all()
    ]
    last_update = max((e.recorded_at for e in entries), default=None)

    # Procurement's own approved-budget figure (finance-approved sheets,
    # optional sections excluded, SAR, ex-VAT) so the board and the
    # procurement budget screens cannot disagree. board_rows passes it in
    # for the whole board at once.
    if budgets is None:
        budgets = approved_budgets_for([project])
    budget = budgets.get(project.pk)
    if budget is not None and budget <= ZERO:
        budget = None
    # Budget Status compares the budget with what procurement has COMMITTED
    # (issued POs and later, ex-VAT), the same rule as procurement's own budget
    # screens. Not with cash paid: finance stages payments late, so paid-to-date
    # would call an over-committed project "Under Budget".
    committed = commitment(list(project.purchase_orders.all()), rates)['committed']
    budget_state = budget_status_of(budget, committed)
    money_state = financial_status_of(received - spent)
    try:
        note = project.delivery_comment
    except ObjectDoesNotExist:
        note = None

    return {
        'project': project,
        'reference': project.proposal_reference,
        'completion': completion,
        'completion_pct': completion * Decimal('100'),
        'weightage_problems': validate_weightages(project),
        'po_value': finance.po_value if finance else None,
        'start_date': finance.estimated_start_date if finance else None,
        'end_date': planned_end,
        'cash_in': received,
        'cash_out': spent,
        'net': received - spent,
        'achieved_milestones': achieved,
        'pending_milestones': pending,
        'action_by': action_by(project),
        'expected_completion_date': expected_end,
        'delay_months': delay_months(expected_end, planned_end),
        # Answerable because each update is its own row. The workbook's
        # equivalent column was TODAY(), which always read as today.
        'last_update': last_update,
        'budget': budget,
        'budget_status': budget_state,
        'budget_status_label': BUDGET_STATUS_LABELS[budget_state],
        'financial_status': money_state,
        'financial_status_label': FINANCIAL_STATUS_LABELS[money_state],
        'comment': note.comment if note else '',
        'comment_updated_by': note.updated_by if note else None,
        'comment_updated_at': note.updated_at if note else None,
    }


def board_rows(projects):
    """The whole board.

    Exchange rates are fetched once and passed down: doing it per project is
    how a board that is fine with ten projects becomes slow with a hundred.
    """
    # One query for every project's finance outflow rows, not one per row.
    prefetch_related_objects(projects, 'cash_outflow_rows', 'purchase_orders')
    rates = exchange_rates()
    budgets = approved_budgets_for(projects)
    return [board_row(p, rates, budgets) for p in projects]
