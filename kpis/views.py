import datetime
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


@login_required
@require_capability('kpis.access')
def kpi_new(request):
    """Skeleton for the new GM-facing KPI dashboard - Sales & Proposal
    Performance owned by the lead, Procurement owned by this dev, split into
    separate template partials (kpi_new_sales.html, kpi_new_proposal.html,
    kpi_new_procurement.html, kpi_new_pmo.html) so each person's work stays
    in their own file and doesn't conflict. Reuses the existing KPI
    registry/dashboard data (build_dashboard) so already-computed KPIs
    (revenue, pipeline coverage, cost savings, on-time delivery, etc.) are
    available immediately - metrics with no registry entry yet need a new
    compute function in kpis/registry.py before they'll show real data.

    Gated exactly like dashboard() above. This is a sandbox, but it renders
    the same build_dashboard() output the real dashboard does - company
    revenue, cost savings, on-time delivery - so it needs the same two
    decorators. The nav entry is behind `kpis.manage`, which only hides the
    link; without these the URL itself answered 200 to anyone, signed in or
    not. Whatever else changes here, keep them."""
    period = _resolve_period(request)
    region, regions = _resolve_region(request)
    data = build_dashboard(period, region=region)

    # Cards keyed by KPI key, so a partial can place one specific metric where
    # the GM's layout wants it instead of looping over the whole department in
    # registry order. Same card dicts build_dashboard already produced - no
    # second computation.
    cards = {
        dept['key']: {c['key']: c for c in dept['cards']}
        for dept in data['departments']
    }

    context = {
        'data': data,
        'cards': cards,
        'ytd': _ytd_revenue_card(period, region),
        'period': period,
        'period_picker': _period_picker(period),
        'regions': regions,
        'selected_region': region,
    }
    return render(request, 'kpis/kpi_new.html', context)


# ── Period picker ────────────────────────────────────────────────────────────
# period_options() returns months, quarters and years in one flat list - 18
# entries of three different granularities in a single dropdown, where picking
# "June 2026" and "Q2 2026" look like the same kind of choice and the reader
# cannot see at a glance which they are on. The picker below splits that into
# granularity first, then value, so only one granularity's worth of options is
# ever on screen.

PERIOD_KINDS = (
    ('month', 'Month'),
    ('quarter', 'Quarter'),
    ('year', 'Year'),
)


def _period_kind(period):
    """'month' | 'quarter' | 'year' for a period string."""
    parts = (period or '').split('-')
    if len(parts) == 1:
        return 'year'
    return 'quarter' if parts[1].upper().startswith('Q') else 'month'


def _switch_period_kind(period, kind, today=None):
    """The same point in time expressed at a different granularity.

    Switching granularity should keep the reader where they are rather than
    jumping to today: from June 2026 to Quarter gives Q2 2026, not whichever
    quarter it happens to be now. Going the other way - to a finer grain than
    the period carries - there is no single right answer, so it lands on the
    current month/quarter when that falls inside the period, and on the first
    one otherwise.
    """
    if today is None:
        from django.utils import timezone
        today = timezone.localdate()

    parts = (period or '').split('-')
    year = parts[0] if parts and parts[0].isdigit() else str(today.year)
    src = _period_kind(period)

    if kind == 'year':
        return year

    if kind == 'quarter':
        if src == 'month':
            month = int(parts[1])
            return f'{year}-Q{(month - 1) // 3 + 1}'
        if src == 'quarter':
            return period
        # From a year: this quarter if we are in that year, else Q1.
        q = (today.month - 1) // 3 + 1 if str(today.year) == year else 1
        return f'{year}-Q{q}'

    # kind == 'month'
    if src == 'month':
        return period
    if src == 'quarter':
        q = int(parts[1][1:])
        first = (q - 1) * 3 + 1
        # This month if it sits in that quarter, else the quarter's first.
        if str(today.year) == year and first <= today.month < first + 3:
            return f'{year}-{today.month:02d}'
        return f'{year}-{first:02d}'
    month = today.month if str(today.year) == year else 1
    return f'{year}-{month:02d}'


def _period_picker(period, today=None):
    """Everything the filter bar needs: the active granularity, the values
    available within it, and where each granularity tab should link to."""
    from .periods import label_for, current_period
    if today is None:
        from django.utils import timezone
        today = timezone.localdate()

    kind = _period_kind(period)

    if kind == 'month':
        # The last 12 months, newest first.
        values = []
        cur = datetime.date(today.year, today.month, 1)
        for i in range(12):
            idx = (cur.year * 12 + (cur.month - 1)) - i
            d = datetime.date(idx // 12, idx % 12 + 1, 1)
            values.append(f'{d.year}-{d.month:02d}')
    elif kind == 'quarter':
        year = period.split('-')[0]
        values = [f'{year}-Q{q}' for q in range(1, 5)]
    else:
        values = [str(today.year - n) for n in range(3)]

    return {
        'kind': kind,
        'kinds': [
            {'key': k, 'label': lbl, 'active': k == kind,
             'period': _switch_period_kind(period, k, today)}
            for k, lbl in PERIOD_KINDS
        ],
        'values': [
            {'period': v, 'label': label_for(v), 'active': v == period,
             'is_current': v == current_period(kind, today)}
            for v in values
        ],
    }


def _ytd_revenue_card(period, region):
    """Revenue for the financial year containing `period`.

    The dashboard computes one period, but Sales wants revenue for the selected
    period AND year-to-date on screen together. Rather than build a second full
    dashboard - which would recompute every department's KPIs to use one number
    - this computes the single revenue KPI against a year period.

    Returns None if the period string is malformed; the template just omits the
    figure rather than the page failing.
    """
    from .services import build_card
    from .models import KPIEntry
    from .periods import period_bounds

    year = (period or '').split('-')[0]
    if not year.isdigit():
        return None
    try:
        bounds = period_bounds(year)
    except ValueError:
        return None

    entries = {e.kpi_key: e for e in KPIEntry.objects.filter(period=year)}
    targets = {k: e.target for k, e in entries.items() if e.target is not None}
    card = build_card(KPI_BY_KEY['sales_revenue_achievement'], year, entries,
                      bounds, targets, region=region)
    # build_card() does not carry the period it was built for, and the template
    # needs to say which year this figure covers.
    card['period_label'] = label_for(year)
    return card


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
