"""Dashboard assembly: turn a period (+ optional region / user) into KPI cards.

Kept out of the view so it can be unit-tested directly. Auto KPI computation is
wrapped so one bad data row degrades to an 'n/a' tile instead of 500-ing the page.
"""
from decimal import Decimal

from .models import KPIEntry
from .periods import period_bounds, label_for
from .registry import (
    KPI_DEFINITIONS, DEPARTMENTS, KPIContext, evaluate, achievement_pct,
    user_attributable_kpis,
    CURRENCY, PERCENT, RATIO, DAYS, HOURS, COUNT, SCORE,
)


def format_value(unit, value, currency='SAR'):
    """Human-readable value string for a tile ('—' when None)."""
    if value is None:
        return '—'
    value = Decimal(value)
    if unit == CURRENCY:
        if currency == 'mixed':
            return f'{value:,.0f} (mixed cur.)'
        return f'{currency} {value:,.0f}'
    if unit == PERCENT:
        return f'{value:.1f}%'
    if unit == RATIO:
        return f'{value:.2f}x'
    if unit == DAYS:
        return f'{value:.1f} d'
    if unit == HOURS:
        return f'{value:.0f} h'
    if unit == SCORE:
        return f'{value:.0f}'
    if unit == COUNT:
        return f'{value:.0f}'
    return f'{value}'


def _targets_map(entries):
    """{kpi_key: target Decimal} for entries that set a target."""
    return {k: e.target for k, e in entries.items() if e.target is not None}


def build_card(kpi, period, entries, bounds, targets, user=None, region=None):
    start, end = bounds
    entry = entries.get(kpi.key)
    target = entry.target if (entry and entry.target is not None) else kpi.target

    coverage = ''
    currency = 'SAR'
    if kpi.is_auto:
        ctx = KPIContext(period, start, end, targets, user, region)
        try:
            res = kpi.compute(ctx)
            value = res.value
            coverage = res.coverage
            if res.currency:
                currency = res.currency
        except Exception:
            value = None
    else:
        value = entry.manual_value if entry else None

    status = evaluate(kpi, value, target)
    return {
        'kpi': kpi,
        'key': kpi.key,
        'label': kpi.label,
        'unit': kpi.unit,
        'source': kpi.source,
        'is_auto': kpi.is_auto,
        'help': kpi.help,
        'direction': kpi.direction,
        'value': value,
        'value_display': format_value(kpi.unit, value, currency),
        'target': target,
        'target_display': format_value(kpi.unit, target, currency),
        'status': status,
        'pct': achievement_pct(kpi, value, target),
        'coverage': coverage,
        'note': entry.note if entry else '',
    }


def _grouped(kpis, period, entries, bounds, targets, user=None, region=None):
    """Build cards for `kpis` grouped by department with on/near/off/na counts."""
    departments = []
    for dept_key, dept_label in DEPARTMENTS:
        cards = [
            build_card(kpi, period, entries, bounds, targets, user=user, region=region)
            for kpi in kpis if kpi.department == dept_key
        ]
        if not cards:
            continue
        summary = {'on': 0, 'near': 0, 'off': 0, 'na': 0}
        for c in cards:
            summary[c['status']] += 1
        departments.append({
            'key': dept_key, 'label': dept_label,
            'cards': cards, 'summary': summary,
        })
    return departments


def data_readiness(region=None):
    """Counts of the data gaps that most blunt the KPIs, so the team knows what
    to fill in. Returns {} when there's nothing to flag."""
    from projects.models import Project
    wl = Project.objects.filter(status__category__in=['won', 'lost'])
    if region is not None:
        wl = wl.filter(region=region)
    won = wl.filter(status__category='won')

    gaps = {
        'won_lost_total': wl.count(),
        'untagged_year': wl.filter(year='').count(),
        'won_total': won.count(),
        'won_missing_actuals': won.filter(actual_sales__lte=0).count(),
    }
    gaps['has_gaps'] = bool(gaps['untagged_year'] or gaps['won_missing_actuals'])
    return gaps


def build_dashboard(period, region=None):
    """{period, period_label, region, departments:[...], readiness:{...}}."""
    bounds = period_bounds(period)
    entries = {e.kpi_key: e for e in KPIEntry.objects.filter(period=period)}
    targets = _targets_map(entries)
    departments = _grouped(KPI_DEFINITIONS, period, entries, bounds, targets, region=region)
    return {
        'period': period,
        'period_label': label_for(period),
        'region': region,
        'departments': departments,
        'readiness': data_readiness(region),
    }


def attributable_users():
    """Users who own/created records a per-person KPI can attribute to."""
    from django.contrib.auth import get_user_model
    from projects.models import Project
    from proposals.models import TechnicalProposal
    from procurement.models import PurchaseOrder

    ids = set(Project.objects.exclude(owner__isnull=True)
              .values_list('owner_id', flat=True))
    ids |= set(TechnicalProposal.objects.exclude(created_by__isnull=True)
               .values_list('created_by_id', flat=True))
    ids |= set(PurchaseOrder.objects.exclude(created_by__isnull=True)
               .values_list('created_by_id', flat=True))
    from hr.models import Employee
    ids |= set(Employee.objects.exclude(user__isnull=True)
               .values_list('user_id', flat=True))

    User = get_user_model()
    return list(User.objects.filter(pk__in=ids)
                .order_by('first_name', 'last_name', 'username'))


def build_person_scorecard(period, user):
    """The user-attributable auto KPIs computed for one user, grouped by
    department. Manual KPIs are department-level and omitted here."""
    bounds = period_bounds(period)
    entries = {e.kpi_key: e for e in KPIEntry.objects.filter(period=period)}
    targets = _targets_map(entries)
    departments = _grouped(user_attributable_kpis(), period, entries, bounds,
                           targets, user=user)
    return {
        'period': period,
        'period_label': label_for(period),
        'user': user,
        'departments': departments,
    }


# ── Deals won ────────────────────────────────────────────────────────────────

def build_deals_won(period, region=None):
    """Deals that flipped to Won, who flipped them, and what they were worth.

    Reads ProjectHistory rather than the project's year/quarter tags, because
    the question is *when did this happen and who did it* - and the tags carry
    neither a date nor a person. Each row is one transition into a won status:
    the project, the moment, the person who made the change, and the value
    through the shared ladder so the totals agree with the KPI tiles.

    A project can flip to Won more than once (won, reopened, won again). The
    FIRST transition in the window is the one kept: the deal was won once, and
    counting a correction as a second win would inflate both the count and the
    value.

    Comes with a health figure, not just rows. ProjectHistory is only written
    by ProjectUpdateView.form_valid, so a status changed by any other route -
    bulk action, Django admin, a script - leaves no trace. `untracked` counts
    projects currently sitting in a won status that this table cannot see,
    which is the difference between "we won nothing this month" and "nobody
    recorded it".
    """
    from decimal import Decimal
    from projects.models import Project, ProjectHistory
    from .periods import period_bounds
    from .registry import _resolved_values

    start, end = period_bounds(period)

    history = (ProjectHistory.objects
               .filter(new_status__category='won',
                       changed_at__date__gte=start, changed_at__date__lt=end)
               .select_related('project', 'project__region', 'project__owner',
                               'changed_by', 'new_status', 'old_status')
               .order_by('changed_at'))
    if region is not None:
        history = history.filter(project__region=region)

    first = {}
    for h in history:
        if h.project_id not in first:
            first[h.project_id] = h

    values = {}
    if first:
        projects = Project.objects.filter(pk__in=first.keys())
        for project, resolved in _resolved_values(projects):
            values[project.pk] = resolved

    rows = []
    for pid, h in first.items():
        resolved = values.get(pid) or {}
        rows.append({
            'project': h.project,
            'won_at': h.changed_at,
            'won_by': h.changed_by,
            'from_status': h.old_status,
            'owner': h.project.owner,
            'region': h.project.region,
            'amount': resolved.get('amount'),
            'currency': resolved.get('currency'),
            'source': resolved.get('source'),
        })
    rows.sort(key=lambda r: r['won_at'], reverse=True)

    # Per-person roll-up: who is actually closing, and for how much.
    people = {}
    for r in rows:
        key = r['won_by'].pk if r['won_by'] else None
        entry = people.setdefault(key, {
            'user': r['won_by'], 'count': 0, 'amount': Decimal('0'),
            'currency': r['currency'],
        })
        entry['count'] += 1
        if r['amount']:
            entry['amount'] += Decimal(r['amount'])
    people = sorted(people.values(), key=lambda p: (-p['count'], -p['amount']))

    # How much of the truth this table can actually see.
    won_now = Project.objects.filter(status__category='won')
    if region is not None:
        won_now = won_now.filter(region=region)
    tracked_ever = set(ProjectHistory.objects
                       .filter(new_status__category='won')
                       .values_list('project_id', flat=True))
    untracked = won_now.exclude(pk__in=tracked_ever).count()

    total = sum((Decimal(r['amount']) for r in rows if r['amount']), Decimal('0'))
    return {
        'rows': rows,
        'people': people,
        'count': len(rows),
        'total': total,
        'currency': (region.currency if region is not None
                     else (rows[0]['currency'] if rows else None)),
        'untracked': untracked,
        'won_now': won_now.count(),
    }


# ── Deadline reliability ─────────────────────────────────────────────────────

# Each pipeline milestone carries all three things a punctuality measure needs:
# a deadline on the Project, an actual timestamp on the CostingSheet, and the
# person who did it. Nothing aggregated them per person until now - the Team
# Activity page shows how LONG a stage took (cycle time), which is a different
# question from whether the date was met.
DEADLINE_MILESTONES = [
    ('bom_started', 'BOM started',
     'bom_started_deadline', 'bom_started_at', 'bom_started_by'),
    ('handed_over', 'Handed to sales',
     'handed_over_deadline', 'handed_over_at', 'handed_over_by'),
    ('costing_started', 'Costing started',
     'costing_started_deadline', 'costing_started_at', 'costing_started_by'),
    ('finalized', 'Finalised',
     'finalized_deadline', 'finalized_at', 'finalized_by'),
]


def _cycle_sheet(sheets):
    """The furthest-along sheet on a project, matching what the Commercial
    Pipeline list already treats as the one driving cycle time. Tie-break on
    the most recently updated."""
    from costing.models import WORKFLOW_STAGE_SEQUENCE
    if not sheets:
        return None

    def rank(s):
        try:
            return WORKFLOW_STAGE_SEQUENCE.index(s.workflow_stage)
        except ValueError:
            return -1

    return max(sheets, key=lambda s: (rank(s), s.updated_at))


def build_deadline_reliability(period, region=None):
    """Who hits their dates, per person and per milestone.

    Anchored on the DEADLINE falling in the window, not on when the work was
    done. "Of the milestones due this quarter, how many were met" is the
    question a deadline measure should answer, and it keeps finished and
    unfinished work comparable - anchoring on the actual date would quietly
    drop everything nobody got round to.

    Scoring:

    * A milestone with no deadline recorded cannot be judged and is counted
      separately, never as a pass.
    * A completed milestone scores against the person who completed it. On
      time means on or before the deadline; the variance is whole days, +ve
      for early, matching Project._milestone_variance().
    * A milestone past its deadline that NOBODY has done is late, but it is
      not attributable: there is no actor recorded, because no one performed
      it. Charging it to the previous stage's actor would blame the wrong
      person, so these are reported per milestone instead - showing where work
      is stuck even when the system cannot say who is holding it.

    Per person the headline is on-time percentage, with the MEDIAN variance
    beside it rather than the mean: one catastrophic slip should not define
    someone otherwise reliable. The worst case is carried separately so it is
    not hidden either.
    """
    import statistics
    from decimal import Decimal
    from django.utils import timezone
    from projects.models import Project
    from .periods import period_bounds

    start, end = period_bounds(period)
    today = timezone.localdate()

    projects = (Project.objects
                .select_related('region')
                .prefetch_related('costing_sheets'))
    if region is not None:
        projects = projects.filter(region=region)

    people = {}
    per_milestone = {
        key: {'key': key, 'label': label, 'total': 0, 'on_time': 0,
              'overdue_open': 0, 'undated': 0}
        for key, label, _dl, _at, _by in DEADLINE_MILESTONES
    }
    undated = 0
    overdue_open = 0

    for project in projects:
        sheet = _cycle_sheet(list(project.costing_sheets.all()))
        for key, _label, deadline_field, actual_field, actor_field in DEADLINE_MILESTONES:
            deadline = getattr(project, deadline_field, None)
            if not deadline:
                continue
            if not (start <= deadline < end):
                continue

            actual = getattr(sheet, actual_field, None) if sheet else None
            bucket = per_milestone[key]

            if actual is None:
                # Not done. Only counts as a miss once the date has passed -
                # a milestone due later this period is simply not yet due.
                if today > deadline:
                    bucket['overdue_open'] += 1
                    overdue_open += 1
                continue

            actor = getattr(sheet, actor_field, None)
            actual_date = actual.date() if hasattr(actual, 'date') else actual
            variance = (deadline - actual_date).days
            on_time = variance >= 0

            bucket['total'] += 1
            if on_time:
                bucket['on_time'] += 1

            if actor is None:
                # Done, but the doer was never recorded - countable in the
                # milestone totals, not chargeable to anyone.
                continue
            entry = people.setdefault(actor.pk, {
                'user': actor, 'total': 0, 'on_time': 0,
                'variances': [], 'per_milestone': {},
            })
            entry['total'] += 1
            entry['on_time'] += int(on_time)
            entry['variances'].append(variance)
            m = entry['per_milestone'].setdefault(
                key, {'key': key, 'total': 0, 'on_time': 0})
            m['total'] += 1
            m['on_time'] += int(on_time)

    def pct(on_time, total):
        if not total:
            return None
        return (Decimal(on_time) / Decimal(total) * 100).quantize(Decimal('0.1'))

    rows = []
    for entry in people.values():
        variances = entry['variances']
        rows.append({
            'user': entry['user'],
            'total': entry['total'],
            'on_time': entry['on_time'],
            'pct': pct(entry['on_time'], entry['total']),
            'median': int(statistics.median(variances)) if variances else None,
            'worst': min(variances) if variances else None,
            'per_milestone': [
                {**entry['per_milestone'][k],
                 'label': label,
                 'pct': pct(entry['per_milestone'][k]['on_time'],
                            entry['per_milestone'][k]['total'])}
                for k, label, _d, _a, _b in DEADLINE_MILESTONES
                if k in entry['per_milestone']
            ],
        })
    # Most reliable first; a bigger sample breaks a tie, since 1-of-1 is not
    # the same achievement as 20-of-20.
    rows.sort(key=lambda r: (r['pct'] or 0, r['total']), reverse=True)

    milestones = []
    for key, label, _d, _a, _b in DEADLINE_MILESTONES:
        b = per_milestone[key]
        milestones.append({**b, 'pct': pct(b['on_time'], b['total'])})

    total = sum(m['total'] for m in milestones)
    on_time = sum(m['on_time'] for m in milestones)
    return {
        'people': rows,
        'milestones': milestones,
        'total': total,
        'on_time': on_time,
        'pct': pct(on_time, total),
        'overdue_open': overdue_open,
        'undated': undated,
    }
