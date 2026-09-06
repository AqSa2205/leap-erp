"""Where each project stands — completion, cash in, cash out.

Pure functions over querysets, no writes, in the shape of
`procurement/budget_status.py`. Everything here is derived. Nothing a person
types is recomputed and nothing computed is stored, which is the single change
that removes the class of bug the workbook had: a total that summed the wrong
range, or a cross-reference to a cell that moved, and no way to notice.
"""
from decimal import Decimal

from procurement.budget_status import commitment, exchange_rates

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


def cash_out(project, rates=None):
    """Committed spend, from the purchase orders.

    Uses procurement's own definition of committed (issued, acknowledged,
    completed) so this figure and the procurement budget screens cannot
    disagree.
    """
    rates = exchange_rates() if rates is None else rates
    return commitment(list(project.purchase_orders.all()), rates)['committed']


def board_row(project, rates=None):
    """One project's line on the overview board.

    Every column is derived. There is no manual total to fall out of step and
    no cell reference to break when a row is inserted.
    """
    finance = getattr(project, 'finance', None)
    completion = project_completion(project)
    received = cash_in(project)
    spent = cash_out(project, rates)

    entries = [
        entry
        for row in project.milestones.all()
        for entry in row.progress_entries.all()
    ]
    last_update = max((e.recorded_at for e in entries), default=None)

    return {
        'project': project,
        'reference': project.proposal_reference,
        'completion': completion,
        'completion_pct': completion * Decimal('100'),
        'weightage_problems': validate_weightages(project),
        'po_value': finance.po_value if finance else None,
        'start_date': finance.estimated_start_date if finance else None,
        'end_date': finance.estimated_end_date if finance else None,
        'cash_in': received,
        'cash_out': spent,
        'net': received - spent,
        # Answerable because each update is its own row. The workbook's
        # equivalent column was TODAY(), which always read as today.
        'last_update': last_update,
    }


def board_rows(projects):
    """The whole board.

    Exchange rates are fetched once and passed down: doing it per project is
    how a board that is fine with ten projects becomes slow with a hundred.
    """
    rates = exchange_rates()
    return [board_row(p, rates) for p in projects]
