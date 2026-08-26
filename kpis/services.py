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
