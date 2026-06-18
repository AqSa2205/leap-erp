"""Dashboard assembly: turn a period string into per-department KPI cards.

Kept out of the view so it can be unit-tested directly and reused by any future
export. Auto KPI computation is wrapped so a single bad data row degrades to an
'n/a' tile instead of 500-ing the whole dashboard.
"""
from decimal import Decimal

from .models import KPIEntry
from .periods import period_bounds, label_for
from .registry import (
    KPI_DEFINITIONS, DEPARTMENTS, evaluate, achievement_pct,
    user_attributable_kpis,
    CURRENCY, PERCENT, RATIO, DAYS, HOURS, COUNT, SCORE,
)


def format_value(unit, value):
    """Human-readable value string for a tile ('—' when None)."""
    if value is None:
        return '—'
    value = Decimal(value)
    if unit == CURRENCY:
        return f'SAR {value:,.0f}'
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
    """{kpi_key: target Decimal} for entries that set a target — feeds cross-KPI
    compute (e.g. pipeline coverage reads the revenue goal)."""
    return {k: e.target for k, e in entries.items() if e.target is not None}


def build_card(kpi, period, entries, bounds, targets, user=None):
    start, end = bounds
    entry = entries.get(kpi.key)
    target = entry.target if (entry and entry.target is not None) else kpi.target

    if kpi.is_auto:
        try:
            value = kpi.compute(start, end, targets, user=user)
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
        'value_display': format_value(kpi.unit, value),
        'target': target,
        'target_display': format_value(kpi.unit, target),
        'status': status,
        'pct': achievement_pct(kpi, value, target),
        'note': entry.note if entry else '',
    }


def build_dashboard(period):
    """Return {period, period_label, departments:[{key,label,cards,summary}]}."""
    bounds = period_bounds(period)
    entries = {e.kpi_key: e for e in KPIEntry.objects.filter(period=period)}
    targets = _targets_map(entries)

    departments = []
    for dept_key, dept_label in DEPARTMENTS:
        cards = [
            build_card(kpi, period, entries, bounds, targets)
            for kpi in KPI_DEFINITIONS if kpi.department == dept_key
        ]
        summary = {'on': 0, 'near': 0, 'off': 0, 'na': 0}
        for c in cards:
            summary[c['status']] += 1
        departments.append({
            'key': dept_key,
            'label': dept_label,
            'cards': cards,
            'summary': summary,
        })

    return {
        'period': period,
        'period_label': label_for(period),
        'departments': departments,
    }


def attributable_users():
    """Users who own/created records a per-person KPI can attribute to — i.e.
    anyone who owns a project, or created a proposal or a PO. Ordered by name."""
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

    User = get_user_model()
    return list(User.objects.filter(pk__in=ids)
                .order_by('first_name', 'last_name', 'username'))


def build_person_scorecard(period, user):
    """Per-person scorecard: the user-attributable auto KPIs computed for one
    user, grouped by department. Manual KPIs are department-level and omitted
    here (there is no per-person source for them).
    """
    bounds = period_bounds(period)
    # Targets come from the period's manual entries (thresholds apply per person).
    entries = {e.kpi_key: e for e in KPIEntry.objects.filter(period=period)}
    targets = _targets_map(entries)

    kpis = user_attributable_kpis()
    departments = []
    for dept_key, dept_label in DEPARTMENTS:
        cards = [
            build_card(kpi, period, entries, bounds, targets, user=user)
            for kpi in kpis if kpi.department == dept_key
        ]
        if not cards:
            continue
        summary = {'on': 0, 'near': 0, 'off': 0, 'na': 0}
        for c in cards:
            summary[c['status']] += 1
        departments.append({
            'key': dept_key,
            'label': dept_label,
            'cards': cards,
            'summary': summary,
        })

    return {
        'period': period,
        'period_label': label_for(period),
        'user': user,
        'departments': departments,
    }
