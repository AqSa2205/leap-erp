from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.permissions import require_capability
from .models import KPIEntry
from .periods import current_period, period_options, period_bounds, label_for
from .registry import KPI_DEFINITIONS, DEPARTMENTS, KPI_BY_KEY
from .services import (
    build_dashboard, format_value, build_person_scorecard, attributable_users,
)


def _resolve_period(request):
    """Validated period from ?period=, falling back to the current quarter."""
    period = request.GET.get('period') or request.POST.get('period')
    if period:
        try:
            period_bounds(period)        # validate; raises on garbage
            return period
        except (ValueError, TypeError):
            pass
    return current_period('quarter')


def _parse_decimal(raw):
    raw = (raw or '').strip().replace(',', '')
    if raw == '':
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _resolve_region(request):
    """(region_instance_or_None, all_regions_queryset). Reads ?region=<code>;
    blank / 'all' / unknown -> None (all regions blended)."""
    from projects.models import Region
    regions = Region.objects.filter(is_active=True).order_by('name')
    code = (request.GET.get('region') or '').strip()
    selected = None
    if code and code.lower() != 'all':
        selected = regions.filter(code=code).first()
    return selected, regions


@login_required
@require_capability('kpis.access')
def dashboard(request):
    period = _resolve_period(request)
    region, regions = _resolve_region(request)
    data = build_dashboard(period, region=region)
    context = {
        'data': data,
        'period': period,
        'period_options': period_options(),
        'regions': regions,
        'selected_region': region,
        'can_manage': request.user.has_capability('kpis.manage'),
    }
    return render(request, 'kpis/dashboard.html', context)


@login_required
@require_capability('kpis.access')
def people(request):
    """Per-person KPI scorecard. Pick a user; see the attributable auto KPIs
    computed from records they own/created."""
    period = _resolve_period(request)
    users = attributable_users()

    selected = None
    raw_id = request.GET.get('user')
    if raw_id:
        for u in users:
            if str(u.pk) == str(raw_id):
                selected = u
                break

    scorecard = build_person_scorecard(period, selected) if selected else None
    context = {
        'period': period,
        'period_label': label_for(period),
        'period_options': period_options(),
        'users': users,
        'selected': selected,
        'scorecard': scorecard,
    }
    return render(request, 'kpis/people.html', context)


@login_required
@require_capability('kpis.manage')
def manage(request):
    period = _resolve_period(request)

    if request.method == 'POST':
        entries = {e.kpi_key: e for e in KPIEntry.objects.filter(period=period)}
        for kpi in KPI_DEFINITIONS:
            target = _parse_decimal(request.POST.get(f'target_{kpi.key}'))
            value = (_parse_decimal(request.POST.get(f'value_{kpi.key}'))
                     if not kpi.is_auto else None)
            note = (request.POST.get(f'note_{kpi.key}') or '').strip()

            entry = entries.get(kpi.key)
            # Skip creating empty rows; clear rows that are emptied out.
            if entry is None:
                if target is None and value is None and not note:
                    continue
                entry = KPIEntry(period=period, kpi_key=kpi.key)
            entry.target = target
            if not kpi.is_auto:
                entry.manual_value = value
            entry.note = note
            entry.updated_by = request.user
            entry.save()

        messages.success(request, f'KPI data saved for {label_for(period)}.')
        return redirect(f"{reverse('kpis:manage')}?period={period}")

    # GET — build editable rows grouped by department.
    entries = {e.kpi_key: e for e in KPIEntry.objects.filter(period=period)}
    departments = []
    for dept_key, dept_label in DEPARTMENTS:
        rows = []
        for kpi in KPI_DEFINITIONS:
            if kpi.department != dept_key:
                continue
            entry = entries.get(kpi.key)
            target = entry.target if (entry and entry.target is not None) else kpi.target
            rows.append({
                'kpi': kpi,
                'target': target,
                'manual_value': entry.manual_value if entry else None,
                'note': entry.note if entry else '',
                'default_target': kpi.target,
            })
        departments.append({'key': dept_key, 'label': dept_label, 'rows': rows})

    context = {
        'period': period,
        'period_label': label_for(period),
        'period_options': period_options(),
        'departments': departments,
    }
    return render(request, 'kpis/manage.html', context)
