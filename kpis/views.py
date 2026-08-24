from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.permissions import require_capability
from .activity_service import (
    build_activity_overview, build_user_activity, activity_period_options,
)
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


def _scoped_user_ids(user):
    """Set of user-account ids a team-scoped role may see in the per-person KPI
    views (their reports' linked login accounts), or ``None`` = unrestricted
    (admin tiers). Bridges the HR org-chart scope onto user accounts, since KPI
    scorecards/activity key off the *user* who owns records, not the Employee."""
    from hr.scoping import scoped_employee_ids
    emp_ids = scoped_employee_ids(user)
    if emp_ids is None:
        return None
    from hr.models import Employee
    return set(
        Employee.objects.filter(pk__in=emp_ids, user__isnull=False)
        .values_list('user_id', flat=True))


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


def kpi_new(request):
    """Skeleton for the new GM-facing KPI dashboard - Sales & Proposal
    Performance owned by the lead, Procurement owned by this dev, split into
    separate template partials (kpi_new_sales.html, kpi_new_proposal.html,
    kpi_new_procurement.html, kpi_new_pmo.html) so each person's work stays
    in their own file and doesn't conflict. Reuses the existing KPI
    registry/dashboard data (build_dashboard) so already-computed KPIs
    (revenue, pipeline coverage, cost savings, on-time delivery, etc.) are
    available immediately - metrics with no registry entry yet need a new
    compute function in kpis/registry.py before they'll show real data."""
    period = _resolve_period(request)
    region, regions = _resolve_region(request)
    data = build_dashboard(period, region=region)
    context = {
        'data': data,
        'period': period,
        'period_options': period_options(),
        'regions': regions,
        'selected_region': region,
    }
    return render(request, 'kpis/kpi_new.html', context)


@login_required
@require_capability('kpis.access')
def people(request):
    """Per-person KPI scorecard. Pick a user; see the attributable auto KPIs
    computed from records they own/created."""
    period = _resolve_period(request)
    users = attributable_users()

    # Team-scoped roles (Project/Site Manager, Document Controller) only see
    # their own reports here; admin tiers see everyone.
    team_ids = _scoped_user_ids(request.user)
    if team_ids is not None:
        users = [u for u in users if u.pk in team_ids]

    selected = None
    raw_ids = request.GET.getlist('user')
    people_data = []
    for u in users:
        if str(u.pk) in raw_ids:
            people_data.append({
                'user': u,
                'scorecard': build_person_scorecard(period, u),
            })

    context = {
        'period': period,
        'period_label': label_for(period),
        'period_options': period_options(),
        'users': users,
        'selected_ids': raw_ids,
        'people_data': people_data,
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


def _resolve_activity_period(request):
    """'all' or a valid period string; defaults to 'all' (lifetime review)."""
    period = request.GET.get('period') or 'all'
    if period == 'all':
        return 'all'
    try:
        period_bounds(period)
        return period
    except (ValueError, TypeError):
        return 'all'


@login_required
@require_capability('kpis.activity')
def activity_overview(request):
    period = _resolve_activity_period(request)
    from projects.models import Region
    regions = Region.objects.order_by('name')
    selected_region = (request.GET.get('region') or '').strip()
    region_id = int(selected_region) if selected_region.isdigit() else None
    data = build_activity_overview(period, region_id=region_id)
    return render(request, 'kpis/activity_overview.html', {
        'data': data,
        'period': period,
        'period_options': activity_period_options(),
        'regions': regions,
        'selected_region': selected_region,
    })


@login_required
@require_capability('kpis.activity')
def activity_detail(request, user_id):
    period = _resolve_activity_period(request)
    user = get_object_or_404(get_user_model(), pk=user_id)
    # Team-scoped roles can only open activity for one of their own reports.
    team_ids = _scoped_user_ids(request.user)
    if team_ids is not None and user.pk not in team_ids:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    data = build_user_activity(period, user)
    return render(request, 'kpis/activity_detail.html', {
        'data': data,
        'period': period,
        'period_options': activity_period_options(),
    })
