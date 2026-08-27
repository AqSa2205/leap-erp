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


# ── Milestone reliability ────────────────────────────────────────────────────
#
# The unit of work is a COSTING SHEET milestone - the same four columns the
# Costing list shows (BOM started / Handed to sales / Costing started /
# Finalised), read from the same sheet stamps, so any figure here can be
# reconciled against /costing/ row by row. Duration is the same working-day
# figure that list shows too.
#
# Everything is judged against the project's SUBMISSION DEADLINE. Project also
# carries a per-milestone deadline field, but those are optional planning dates
# that are rarely filled, and scoring against them left nearly every milestone
# unjudged. The submission deadline is the date the client actually holds us
# to, it is captured on the pipeline entry itself, and every milestone in the
# chain exists to meet it.

# key, column label, stamp, actor, which team owns it.
MILESTONES = [
    ('bom_started',     'BOM started',     'bom_started_at',     'bom_started_by',     'proposal'),
    ('handed_over',     'Handed to sales', 'handed_over_at',     'handed_over_by',     'proposal'),
    ('costing_started', 'Costing started', 'costing_started_at', 'costing_started_by', 'sales'),
    ('finalized',       'Finalised',       'finalized_at',       'finalized_by',       'sales'),
]


def _milestone_due(submission_deadline, team):
    """The date a milestone owned by `team` has to be met, worked back from the
    client submission deadline.

    Proposal work - starting the BOM and handing it to sales - is due
    BOM_TO_SALES_BUFFER_WORKING_DAYS before submission, the same rule the RFQ
    Activity tiles already use: a BOM that lands later than that leaves sales
    no usable time to cost and submit, so it is late even though the client
    date has not passed. A BOM cannot be handed over before it is started, so
    the same bar applies to both.

    Sales work - starting the costing and finalising it - is due on the
    submission deadline itself, which is the moment it has to be ready by.

    Returns None when no submission deadline is recorded; those milestones are
    counted as work done but never scored.
    """
    from .registry import _bom_due_date
    if not submission_deadline:
        return None
    if team == 'proposal':
        return _bom_due_date(submission_deadline)
    return submission_deadline


def _backwards_moves(region=None):
    """How many times a sheet was pushed back to an earlier stage.

    Rework does not show up in the milestone stamps at all: `reopen` rewinds
    workflow_stage WITHOUT clearing them, so re-advancing simply overwrites the
    old stamp and the sheet ends up looking as though it only ever went
    forward. CostingSheetChangeLog keeps every move, so a backwards step is
    visible there and nowhere else.

    Counted by comparing consecutive stages per sheet against
    WORKFLOW_STAGE_SEQUENCE rather than by guessing from labels - a reopen
    records exactly the same label a genuine first pass does.
    """
    from costing.models import CostingSheet, CostingSheetChangeLog, WORKFLOW_STAGE_SEQUENCE
    order = {code: i for i, code in enumerate(WORKFLOW_STAGE_SEQUENCE)}
    to_code = {label: code for code, label in CostingSheet.WORKFLOW_STAGE_CHOICES}

    entries = CostingSheetChangeLog.objects.filter(field='workflow_stage')
    if region is not None:
        entries = entries.filter(sheet__project__region=region)
    entries = entries.order_by('sheet_id', 'created_at').values_list(
        'sheet_id', 'after')

    moves = 0
    last = {}
    for sheet_id, after in entries:
        idx = order.get(to_code.get((after or '').strip()))
        if idx is None:
            continue
        if sheet_id in last and idx < last[sheet_id]:
            moves += 1
        last[sheet_id] = idx
    return moves


def build_deadline_reliability(period, region=None):
    """Milestone throughput, punctuality and cycle time for the period.

    Every milestone reached in the window becomes one row in `rows`, carrying
    its own date, due date, variance and verdict. Every headline figure is then
    counted OFF those rows, so the number on a tile and the list of sheets
    behind it cannot drift apart - the drill-down is the same data, filtered,
    not a second query that has to be kept in step.

    Throughput and punctuality are reported separately on purpose. A milestone
    on a project with no submission deadline is real work and is counted, but
    there is no date to judge it against, so it must never score as a pass;
    `undated` says how many were set aside for that reason.

    `overdue_rows` is deliberately outside the period: it holds milestones
    still not reached on live projects whose due date has already passed.
    Nobody performed them, so there is no one to attribute them to, and an
    overdue item does not stop being overdue because the month ended.
    """
    import statistics
    from decimal import Decimal
    from django.utils import timezone
    from costing.models import CostingSheet, working_days_between
    from .periods import period_bounds

    start, end = period_bounds(period)
    today = timezone.localdate()

    sheets = (CostingSheet.objects
              .select_related('project', 'project__status', 'project__region',
                              'project__owner',
                              'created_by', 'bom_started_by', 'handed_over_by',
                              'costing_started_by', 'finalized_by'))
    if region is not None:
        sheets = sheets.filter(project__region=region)

    rows = []               # one per milestone reached in the period
    overdue_rows = []       # one per live sheet stuck past its due date
    open_durations = []     # working days so far, sheets still running

    for sheet in sheets:
        project = sheet.project
        deadline = getattr(project, 'submission_deadline', None) if project else None
        live = bool(project and project.status_id
                    and project.status.category in ('active', 'hot_lead'))

        for key, label, at_field, by_field, team in MILESTONES:
            stamp = getattr(sheet, at_field)
            derived = False
            if key == 'bom_started' and stamp is None:
                # Raising the sheet IS starting the BOM - cycle_rows() and
                # _current_stage_start() both already read
                # `bom_started_at or created_at`. Reading the stamp alone made
                # every sheet imported before the stamp existed look like a BOM
                # that never began.
                stamp, derived = sheet.created_at, True
            if stamp is None:
                continue

            when = timezone.localtime(stamp).date()
            if not (start <= when < end):
                continue

            due = _milestone_due(deadline, team)
            variance = (due - when).days if due is not None else None
            rows.append({
                'sheet': sheet,
                'project': project,
                'key': key,
                'label': label,
                'team': team,
                'when': when,
                'due': due,
                'variance': variance,
                'verdict': ('undated' if variance is None
                            else 'on_time' if variance >= 0 else 'late'),
                'derived': derived,
                'actor': sheet.created_by if derived else getattr(sheet, by_field, None),
                # Only meaningful once the sheet is finished; the Costing
                # list's own Duration figure.
                'duration': sheet.total_cycle_days if key == 'finalized' else None,
            })

        # Overdue and not done: the milestone the sheet is actually stuck on.
        # Only the FIRST unreached one is charged, so a sheet that stalled
        # before the handover is counted once - as a missing handover - rather
        # than three times for every later stage it also has not reached. Each
        # stuck sheet therefore appears exactly once, and the column totals to
        # the number of stuck sheets.
        if live:
            for key, label, at_field, _by, team in MILESTONES:
                # bom_started is never "unreached": raising the sheet starts
                # the BOM, same as above.
                if key == 'bom_started' or getattr(sheet, at_field) is not None:
                    continue
                due = _milestone_due(deadline, team)
                if due is not None and today > due:
                    overdue_rows.append({
                        'sheet': sheet, 'project': project, 'key': key,
                        'label': label, 'due': due,
                        'days_over': (today - due).days,
                    })
                break

        # A sheet that has run for months and one that took a week to finish
        # are not the same thing, and averaging them describes neither.
        if sheet.finalized_at is None and live:
            days = working_days_between(sheet.created_at, timezone.now())
            if days is not None:
                open_durations.append(days)

    def pct(on_time, judged):
        if not judged:
            return None
        return (Decimal(on_time) / Decimal(judged) * 100).quantize(Decimal('0.1'))

    per_ms = {key: {'key': key, 'label': label, 'team': team, 'done': 0,
                    'on_time': 0, 'undated': 0, 'derived': 0, 'overdue_open': 0,
                    'variances': []}
              for key, label, _at, _by, team in MILESTONES}
    people = {}

    for row in rows:
        bucket = per_ms[row['key']]
        bucket['done'] += 1
        if row['derived']:
            bucket['derived'] += 1

        actor = row['actor']
        person = None
        if actor is not None:
            person = people.setdefault(actor.pk, {
                'user': actor, 'count': 0, 'on_time': 0, 'undated': 0,
                'variances': [], 'per_milestone': {},
            })
            person['count'] += 1
            pm = person['per_milestone'].setdefault(
                row['key'], {'key': row['key'], 'label': row['label'],
                             'count': 0, 'on_time': 0, 'variances': []})
            pm['count'] += 1
            if row['verdict'] != 'undated':
                pm['variances'].append(row['variance'])
                if row['verdict'] == 'on_time':
                    pm['on_time'] += 1

        if row['verdict'] == 'undated':
            bucket['undated'] += 1
            if person is not None:
                person['undated'] += 1
            continue

        bucket['variances'].append(row['variance'])
        if person is not None:
            person['variances'].append(row['variance'])
        if row['verdict'] == 'on_time':
            bucket['on_time'] += 1
            if person is not None:
                person['on_time'] += 1

    for row in overdue_rows:
        per_ms[row['key']]['overdue_open'] += 1

    person_rows = []
    for entry in people.values():
        variances = entry['variances']
        person_rows.append({
            'user': entry['user'],
            'count': entry['count'],
            'judged': len(variances),
            'undated': entry['undated'],
            'on_time': entry['on_time'],
            'pct': pct(entry['on_time'], len(variances)),
            # Median, not mean: one catastrophic slip should not define
            # someone otherwise reliable. `worst` sits beside it so the slip is
            # not hidden either.
            'median': int(statistics.median(variances)) if variances else None,
            'worst': min(variances) if variances else None,
            # Per milestone as well as overall: "reliable" can hide someone
            # who is punctual handing over and consistently late finalising.
            'per_milestone': [
                {**pm,
                 'judged': len(pm['variances']),
                 'pct': pct(pm['on_time'], len(pm['variances'])),
                 'median': (int(statistics.median(pm['variances']))
                            if pm['variances'] else None)}
                for pm in (entry['per_milestone'][k]
                           for k, _l, _a, _b, _t in MILESTONES
                           if k in entry['per_milestone'])],
        })
    # Measured and reliable first; someone with nothing judgeable should not
    # outrank someone who hit their dates, however busy they were.
    person_rows.sort(
        key=lambda r: (r['pct'] is not None, r['pct'] or 0, r['count']),
        reverse=True)

    milestones = []
    for key, _label, _at, _by, _team in MILESTONES:
        b = per_ms[key]
        variances = b.pop('variances')
        milestones.append({
            **b,
            'judged': len(variances),
            'late': len(variances) - b['on_time'],
            'pct': pct(b['on_time'], len(variances)),
            'median': int(statistics.median(variances)) if variances else None,
        })

    durations = [r['duration'] for r in rows
                 if r['key'] == 'finalized' and r['duration'] is not None]
    total = len(rows)
    judged = sum(m['judged'] for m in milestones)
    on_time = sum(m['on_time'] for m in milestones)
    return {
        'milestones': milestones,
        'people': person_rows,
        'rows': rows,
        'overdue_rows': overdue_rows,
        'total': total,
        'judged': judged,
        'on_time': on_time,
        'late': judged - on_time,
        'undated': total - judged,
        'pct': pct(on_time, judged),
        'overdue_open': len(overdue_rows),
        'reopens': _backwards_moves(region),
        'duration': {
            'count': len(durations),
            'median': int(statistics.median(durations)) if durations else None,
            'longest': max(durations) if durations else None,
            'open_count': len(open_durations),
            'open_median': (int(statistics.median(open_durations))
                            if open_durations else None),
        },
        'buffer_days': _buffer_days(),
    }


def _buffer_days():
    from .registry import BOM_TO_SALES_BUFFER_WORKING_DAYS
    return BOM_TO_SALES_BUFFER_WORKING_DAYS


# ── Drill-down ───────────────────────────────────────────────────────────────
#
# Every figure on the tab is a filter over the rows build_deadline_reliability()
# already produced, so clicking a number shows exactly the sheets that were
# counted into it - not a second query that could answer differently.

DRILL_OUTCOMES = {
    'done':      'reached the milestone',
    'on_time':   'met the date',
    'late':      'missed the date',
    'undated':   'had no submission deadline to be judged against',
    'overdue':   'past due and still not done',
    'finalised': 'finalised in this period',
}


def reliability_drilldown(data, milestone=None, outcome=None, person=None):
    """The sheets behind one figure, or None when nothing was asked for.

    `milestone` is a MILESTONES key, or None/'all' for every milestone;
    `person` narrows to one actor's own work. Unknown values return None
    rather than silently widening the selection to everything - a mistyped
    link should show nothing, not a number that looks authoritative and
    answers a different question.
    """
    if outcome not in DRILL_OUTCOMES:
        return None
    keys = {k for k, _l, _a, _b, _t in MILESTONES}
    if milestone in ('', None, 'all'):
        milestone = None
    elif milestone not in keys:
        return None

    labels = {k: label for k, label, _a, _b, _t in MILESTONES}

    if outcome == 'overdue':
        rows = [r for r in data['overdue_rows']
                if milestone is None or r['key'] == milestone]
        rows.sort(key=lambda r: -r['days_over'])
    elif outcome == 'finalised':
        rows = [r for r in data['rows'] if r['key'] == 'finalized']
        rows.sort(key=lambda r: -(r['duration'] or 0))
        milestone = 'finalized'
    else:
        rows = [r for r in data['rows']
                if (milestone is None or r['key'] == milestone)
                and (outcome == 'done' or r['verdict'] == outcome)]
        # Worst first when the point is who slipped; newest first otherwise.
        if outcome == 'late':
            rows.sort(key=lambda r: r['variance'])
        else:
            rows.sort(key=lambda r: r['when'], reverse=True)

    person_label = None
    if person not in ('', None):
        # Overdue rows have no actor by definition - nobody performed them -
        # so narrowing to a person correctly empties that bucket.
        rows = [r for r in rows
                if r.get('actor') is not None and str(r['actor'].pk) == str(person)]
        match = next((p for p in data['people'] if str(p['user'].pk) == str(person)),
                     None)
        if match is None:
            return None
        person_label = (match['user'].get_full_name() or match['user'].username)

    return {
        'milestone': milestone,
        'milestone_label': labels.get(milestone, 'All milestones'),
        'outcome': outcome,
        'outcome_label': DRILL_OUTCOMES[outcome],
        'person': person,
        'person_label': person_label,
        'rows': rows,
        'count': len(rows),
        'is_overdue': outcome == 'overdue',
        'shows_duration': outcome == 'finalised',
    }



# ── Export ───────────────────────────────────────────────────────────────────
#
# The PDF is shaped here, as plain strings, and rendered in kpis/pdf.py. Two
# reasons to split it: the shaping is the part worth testing and it can be
# tested without ReportLab, and a second renderer (Excel, CSV) can reuse it
# without the layout coming along.


def _variance_str(days):
    """A signed day count as words. `None` means there was nothing to judge
    against, which is not the same as being on the day."""
    if days is None:
        return '—'
    if days > 0:
        return f'{days}d early'
    if days < 0:
        return f'{abs(days)}d late'
    return 'on the day'


def _pct_str(value):
    return '—' if value is None else f'{value}%'


def _name(user):
    if user is None:
        return '—'
    return user.get_full_name() or user.username


def deadline_export_tables(data, drill=None):
    """The Deadlines tab as a list of {'title', 'note', 'columns', 'rows'}.

    Rows are strings, in the order they are shown on screen, so the export and
    the page cannot describe the same period differently.

    Three tables always: by milestone, by person, and person x milestone -
    the last one because "reliable" can hide someone who is punctual handing
    over and consistently late finalising, which the overall rate averages
    away. A fourth is appended when a drill-down is open, so exporting from a
    filtered view carries the sheets that were on screen.
    """
    tables = [{
        'title': 'By milestone',
        'note': ('Proposal work is due {n} working day{s} before the submission '
                 'date; sales work is due on it.').format(
                     n=data['buffer_days'],
                     s='' if data['buffer_days'] == 1 else 's'),
        'columns': ['Milestone', 'Owner', 'Done', 'Had a date', 'On time',
                    'Late', 'Rate', 'Typically', 'Overdue, not done'],
        'rows': [[
            m['label'],
            m['team'].title(),
            str(m['done']),
            str(m['judged']) + (f" (+{m['undated']} without)" if m['undated'] else ''),
            str(m['on_time']),
            str(m['late']) if m['late'] else '—',
            _pct_str(m['pct']),
            _variance_str(m['median']),
            str(m['overdue_open']) if m['overdue_open'] else '—',
        ] for m in data['milestones']],
    }, {
        'title': 'By person',
        'note': ('Typically is the median, not the mean - one slip should not '
                 'define someone otherwise reliable. Worst sits beside it so '
                 'it is not hidden either.'),
        'columns': ['Person', 'Milestones', 'On time', 'Rate', 'Typically',
                    'Worst'],
        'rows': [[
            _name(r['user']),
            str(r['count']),
            f"{r['on_time']} / {r['judged']}" if r['judged'] else '—',
            _pct_str(r['pct']) if r['pct'] is not None else 'no dates',
            _variance_str(r['median']),
            _variance_str(r['worst']) if r['worst'] is not None and r['worst'] < 0
            else ('never late' if r['judged'] else '—'),
        ] for r in data['people']],
    }]

    # Person x milestone: one row per person per milestone they touched.
    matrix = []
    for person in data['people']:
        for pm in person['per_milestone']:
            matrix.append([
                _name(person['user']),
                pm['label'],
                str(pm['count']),
                str(pm['judged']) if pm['judged'] else '—',
                str(pm['on_time']),
                _pct_str(pm['pct']) if pm['pct'] is not None else 'no dates',
                _variance_str(pm['median']),
            ])
    tables.append({
        'title': 'Each person, each milestone',
        'note': ('Only milestones a person actually moved appear; a blank row '
                 'would claim they were responsible for work that was never '
                 'theirs.'),
        'columns': ['Person', 'Milestone', 'Done', 'Had a date', 'On time',
                    'Rate', 'Typically'],
        'rows': matrix,
    })

    if drill:
        if drill['is_overdue']:
            columns = ['Sheet', 'Reference', 'Project', 'Region', 'Stuck on',
                       'Was due', 'Days over', 'Owner']
            rows = [[
                r['sheet'].title or '—',
                (r['project'].proposal_reference if r['project'] else '—') or '—',
                (r['project'].project_name if r['project'] else '—') or '—',
                (r['project'].region.name if r['project'] and r['project'].region
                 else '—'),
                r['label'],
                r['due'].strftime('%d %b %y'),
                f"{r['days_over']}d",
                _name(r['project'].owner if r['project'] else None),
            ] for r in drill['rows']]
        else:
            columns = ['Sheet', 'Reference', 'Project', 'Region', 'Milestone',
                       'Done on', 'Due', 'Variance']
            if drill['shows_duration']:
                columns.append('Duration')
            columns.append('By')
            rows = []
            for r in drill['rows']:
                row = [
                    r['sheet'].title or '—',
                    (r['project'].proposal_reference if r['project'] else '—') or '—',
                    (r['project'].project_name if r['project'] else '—') or '—',
                    (r['project'].region.name if r['project'] and r['project'].region
                     else '—'),
                    r['label'],
                    r['when'].strftime('%d %b %y')
                    + (' (from creation)' if r['derived'] else ''),
                    r['due'].strftime('%d %b %y') if r['due'] else 'no submission date',
                    _variance_str(r['variance']),
                ]
                if drill['shows_duration']:
                    row.append(f"{r['duration']}d" if r['duration'] is not None else '—')
                row.append(_name(r['actor']))
                rows.append(row)

        title = f"{drill['milestone_label']} — {drill['outcome_label']}"
        if drill['person_label']:
            title += f" — {drill['person_label']}"
        tables.append({'title': title, 'note': '', 'columns': columns,
                       'rows': rows})

    return tables


def deadline_export_summary(data):
    """The four headline tiles as (label, value, note) - the same figures the
    page leads with, so a reader of the PDF starts where the page does."""
    duration = data['duration']
    return [
        ('Milestones hit', str(data['total']),
         'across all four stages'),
        ('On time', _pct_str(data['pct']),
         (f"{data['on_time']} of {data['judged']} with a submission deadline"
          if data['judged'] else 'no submission deadline to judge against')),
        ('Overdue, not done', str(data['overdue_open']),
         'past due on live deals right now'),
        ('Duration',
         f"{duration['median']}d" if duration['median'] is not None else '—',
         (f"typical, creation to finalised ({duration['count']} finalised)"
          if duration['count'] else 'nothing finalised in this period')),
    ]


def _row_note(row):
    """Why a row might not say what the person expects.

    The two ways a row can look wrong to the person named on it are a missing
    submission deadline and a date that was inferred rather than stamped.
    Saying so on the row is the difference between a report someone can check
    and one they can only dispute.
    """
    notes = []
    if row['derived']:
        notes.append('date taken from when the sheet was raised')
    if row['due'] is None:
        notes.append('no submission deadline on the pipeline entry')
    return '; '.join(notes) or ''


def deadline_person_report(data, user_id):
    """One person's own milestone record, or None if they moved nothing.

    Separate from deadline_export_tables() because a report meant to be sent
    to the person it describes must not carry everybody else's figures. This
    holds their work, their rates, and one team-wide percentage for context -
    no other individual is named.

    The detail table is the point of it: a rate alone cannot be checked, so
    every milestone is listed with the date it was recorded, the deadline it
    was judged against and why, so the person can see which record is wrong
    and go and fix it.
    """
    person = next((p for p in data['people'] if str(p['user'].pk) == str(user_id)),
                  None)
    if person is None:
        return None

    rows = [r for r in data['rows']
            if r['actor'] is not None and r['actor'].pk == person['user'].pk]
    # Worst first, then unjudged, then the ones that went fine: what needs
    # checking should not be on the last page.
    rows.sort(key=lambda r: (r['variance'] is None, r['variance'] or 0))

    detail = {
        'title': 'Every milestone, in detail',
        'note': ('Listed worst first. If a date here is wrong, see the note at '
                 'the end of this report.'),
        'columns': ['Sheet', 'Reference', 'Project', 'Milestone', 'Recorded on',
                    'Submission date', 'Due', 'Variance', 'Note'],
        'rows': [[
            r['sheet'].title or '—',
            (r['project'].proposal_reference if r['project'] else '—') or '—',
            (r['project'].project_name if r['project'] else '—') or '—',
            r['label'],
            r['when'].strftime('%d %b %y'),
            (r['project'].submission_deadline.strftime('%d %b %y')
             if r['project'] and r['project'].submission_deadline else '—'),
            r['due'].strftime('%d %b %y') if r['due'] else '—',
            _variance_str(r['variance']),
            _row_note(r),
        ] for r in rows],
    }

    by_stage = {
        'title': 'Your milestones by stage',
        'note': ('A single overall rate can hide being punctual at one stage '
                 'and late at another, so each stage is scored on its own.'),
        'columns': ['Milestone', 'Done', 'Had a date', 'On time', 'Rate',
                    'Typically'],
        'rows': [[
            pm['label'],
            str(pm['count']),
            str(pm['judged']) if pm['judged'] else '—',
            str(pm['on_time']),
            _pct_str(pm['pct']) if pm['pct'] is not None else 'no dates',
            _variance_str(pm['median']),
        ] for pm in person['per_milestone']],
    }

    tiles = [
        ('Milestones moved', str(person['count']),
         'stages you advanced in this period'),
        ('On time', _pct_str(person['pct']) if person['pct'] is not None else '—',
         (f"{person['on_time']} of {person['judged']} that had a deadline"
          if person['judged']
          else 'none of your work had a submission deadline set')),
        ('Typically', _variance_str(person['median']),
         'your median, not your average'),
        ('Worst', _variance_str(person['worst']) if person['worst'] is not None
         and person['worst'] < 0 else 'never late',
         'your single latest milestone'),
    ]

    return {
        'user': person['user'],
        'name': _name(person['user']),
        'tiles': tiles,
        'tables': [by_stage, detail],
        # One team figure for context. Without a denominator a percentage
        # means nothing; no other individual is named.
        'team_pct': _pct_str(data['pct']),
        'team_judged': data['judged'],
        'undated': person['undated'],
    }


# How the deadline for each milestone is arrived at, in the person's own
# terms. Printed on their report so the arithmetic can be checked rather than
# taken on trust.
PERSON_REPORT_RULES = (
    'Every milestone is judged against the project’s submission date. '
    'Starting the BOM and handing it to sales are due {n} working day{s} '
    'before submission, because sales need that time to cost and submit. '
    'Starting the costing and finalising are due on the submission date '
    'itself. Weekends (Friday and Saturday) are not counted. Work on a '
    'project with no submission date recorded is listed but not scored.'
)

PERSON_REPORT_FOOTER = (
    'If something here is wrong: a wrong or missing submission date can be '
    'corrected on the pipeline entry for that reference, and this report will '
    'follow it. A milestone date is recorded at the moment the stage was '
    'moved and cannot be edited afterwards — if one is wrong, raise it '
    'with the reference and the date you expected.'
)
