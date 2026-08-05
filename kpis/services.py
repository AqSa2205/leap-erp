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
