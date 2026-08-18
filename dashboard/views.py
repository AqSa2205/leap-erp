from decimal import Decimal

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.http import JsonResponse
from accounts.permissions import require_capability

from projects.models import Project, Region, ProjectStatus
from accounts.models import User


def _convert(amount, from_ccy, to_ccy, rates):
    """Convert `amount` between currencies via USD.

    `rates` maps currency_code -> ExchangeRate.rate_to_usd (units of that
    currency per 1 USD, e.g. SAR≈3.75, GBP≈0.79). A missing rate on either
    side leaves the amount unchanged rather than silently scaling it wrong.
    """
    if not amount:
        return Decimal('0')
    amount = Decimal(amount)
    src = (from_ccy or 'SAR').upper()
    dst = (to_ccy or 'SAR').upper()
    if src == dst:
        return amount
    src_rate, dst_rate = rates.get(src), rates.get(dst)
    if not src_rate or not dst_rate:
        return amount
    return (amount / src_rate) * dst_rate


# Tiles whose headline value comes from the sales costing sheets rather than
# from the estimated_value column. Everything else (Active, Lost, Ongoing,
# Total) is still an estimate sum — those stages have no priced sheet to read.
COSTING_VALUED_CATEGORIES = ('won', 'hot_lead')


def _resolve_sales_values(projects):
    """Map project_id -> (amount, currency, from_costing) for the priced tiles.

    The Won and Hot Leads tiles report the value the sales team actually
    priced, not the early estimate. Resolution is delegated to
    projects.views._resolve_project_sales_value — the same helper behind the
    Commercial Pipeline list's "Actual Sales / Costing" column and the project
    detail panel — so the dashboard can never disagree with them. Its order is:
    live costing-sheet contract total (A.1 + A.2, the "MAIN — TOTAL CONTRACT
    PRICE" line on the costing PDF) → recorded actual_sales → estimated_value.

    Returns (values, rates); `rates` is reused for the tile conversions.
    """
    from costing.models import ExchangeRate
    from projects.views import _resolve_project_sales_value

    rates = {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}

    # Prefetch the whole costing tree in one pass: contract_total walks
    # sections → line_items and scope_of_work_items per sheet, which is an N+1
    # storm without this.
    priced_qs = (projects.filter(status__category__in=COSTING_VALUED_CATEGORIES)
                 .select_related('region')
                 .prefetch_related('costing_sheets__sections__line_items',
                                   'costing_sheets__scope_of_work_items'))

    values = {}
    for project in priced_qs:
        sheets = list(project.costing_sheets.all())
        for sheet in sheets:
            sheet.set_rates_cache(rates)
        resolved = _resolve_project_sales_value(project, sheets)
        values[project.pk] = (
            resolved['amount'] or Decimal('0'),
            resolved['currency'],
            resolved['source'] == 'costing',
        )
    return values, rates


def _resolved_value_for(tile_projects, sales_values, currency, rates):
    """Sum the resolved sales values for `tile_projects`, expressed in `currency`.

    Only costing-sourced amounts get converted — those carry the sheet's
    explicit `output_currency`. The actual_sales / estimated_value fallbacks
    are stored in the region's own currency, which is already what the tile
    prints, and Region.currency is not reliable enough to convert from (e.g.
    NEO-Dubai is recorded as GBP while its tile prints AED), so converting
    them would corrupt the figure. Summing them as-is matches what the tiles
    did before this change.
    """
    total = Decimal('0')
    for pk in tile_projects.values_list('pk', flat=True):
        entry = sales_values.get(pk)
        if not entry:
            continue
        amount, src_ccy, from_costing = entry
        total += _convert(amount, src_ccy, currency, rates) if from_costing else Decimal(amount)
    return total.quantize(Decimal('0.01'))


def get_region_stats(projects, region_codes, sales_values=None, currency='SAR', rates=None):
    """Helper to get stats for a specific region or regions.

    Pass `sales_values`/`rates` from _resolve_sales_values() to have the Won and
    Hot Leads tiles report the actual costing-sheet value; omit them and they
    fall back to the estimated_value sum used by every other tile.
    """
    region_projects = projects.filter(region__code__in=region_codes)

    active = region_projects.filter(status__category='active')
    hot_leads = region_projects.filter(status__category='hot_lead')
    won = region_projects.filter(status__category='won')
    lost = region_projects.filter(status__category='lost')
    ongoing = region_projects.filter(status__category='ongoing')

    return {
        'total': {
            'count': region_projects.count(),
            'value': region_projects.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'active': {
            'count': active.count(),
            'value': active.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'hot_leads': {
            'count': hot_leads.count(),
            # Actual value from the sales costing sheets — see 'won' below.
            'value': (_resolved_value_for(hot_leads, sales_values, currency, rates)
                      if sales_values is not None
                      else hot_leads.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0)
        },
        'won': {
            'count': won.count(),
            # Actual value from the sales costing sheets, not estimated_value —
            # a won deal's real number is the contract total that was priced.
            'value': (_resolved_value_for(won, sales_values, currency, rates)
                      if sales_values is not None
                      else won.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0)
        },
        'lost': {
            'count': lost.count(),
            'value': lost.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'ongoing': {
            'count': ongoing.count(),
            'value': ongoing.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'projects': region_projects.select_related('status', 'region', 'owner').order_by('-updated_at')[:10],
        'top_projects': region_projects.select_related('status', 'region').order_by('-estimated_value')[:5],
    }


@login_required
def index(request):
    """Main dashboard view with regional tabs.

    Root URL ('/') for the whole app, so it must not 403 for users who lack
    dashboard access (e.g. AI team) — send them to their own landing instead.
    Dashboard data is still only rendered for users who can access it.
    """
    user = request.user
    if not user.has_capability('dashboard.access'):
        from accounts.permissions import landing_url_for
        from django.shortcuts import redirect
        return redirect(landing_url_for(user))

    # Base queryset based on user role
    if user.is_super_admin_user:
        projects = Project.objects.all()
    elif user.is_admin_user or user.is_manager_user:
        projects = Project.objects.filter(region=user.region)
    elif getattr(user, 'is_finance_team_user', False):
        # Finance sees their own region (capability gate already passed).
        projects = Project.objects.filter(region=user.region)
    else:
        projects = Project.objects.filter(owner=user)

    # Won / Hot Lead values come from the actual sales costing sheets. Resolved
    # once for every priced project the user can see, then reused by each region
    # tile and its chart so the costing tree is walked a single time per request.
    sales_values, rates = _resolve_sales_values(projects)

    # Get stats for each region group
    # LNUK = UK + Global (GBP)
    lnuk_stats = get_region_stats(projects, ['UK', 'GLB'], sales_values, 'GBP', rates)
    lnuk_stats['currency'] = 'GBP'
    lnuk_stats['currency_symbol'] = '£'
    lnuk_stats['name'] = 'LNUK'
    lnuk_stats['full_name'] = 'Leap Networks UK & Global'

    # LNA = Leap Networks Arabia (SAR)
    lna_stats = get_region_stats(projects, ['LNA'], sales_values, 'SAR', rates)
    lna_stats['currency'] = 'SAR'
    lna_stats['currency_symbol'] = 'SAR'
    lna_stats['name'] = 'LNA'
    lna_stats['full_name'] = 'Leap Networks Arabia'

    # PA = Pace Arabia (SAR)
    pa_stats = get_region_stats(projects, ['PA'], sales_values, 'SAR', rates)
    pa_stats['currency'] = 'SAR'
    pa_stats['currency_symbol'] = 'SAR'
    pa_stats['name'] = 'PA'
    pa_stats['full_name'] = 'Pace Arabia'

    # NEO-Dubai (AED)
    neo_dubai_stats = get_region_stats(projects, ['NEO-Dubai'], sales_values, 'AED', rates)
    neo_dubai_stats['currency'] = 'AED'
    neo_dubai_stats['currency_symbol'] = 'AED'
    neo_dubai_stats['name'] = 'NEO-Dubai'
    neo_dubai_stats['full_name'] = 'NEO Dubai'

    # NEO-KSA (SAR)
    neo_ksa_stats = get_region_stats(projects, ['NEO-KSA'], sales_values, 'SAR', rates)
    neo_ksa_stats['currency'] = 'SAR'
    neo_ksa_stats['currency_symbol'] = 'SAR'
    neo_ksa_stats['name'] = 'NEO-KSA'
    neo_ksa_stats['full_name'] = 'NEO KSA'

    # Overall stats
    overall_stats = {
        'total': {
            'count': projects.count(),
            'value': projects.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'active': {
            'count': projects.filter(status__category='active').count(),
            'value': projects.filter(status__category='active').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'hot_leads': {
            'count': projects.filter(status__category='hot_lead').count(),
            'value': projects.filter(status__category='hot_lead').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'won': {
            'count': projects.filter(status__category='won').count(),
            # The summary strip renders counts only — it spans every region, so a
            # single mixed-currency total would be meaningless. These values stay
            # estimate sums; the per-region tiles carry the real costing money.
            'value': projects.filter(status__category='won').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'lost': {
            'count': projects.filter(status__category='lost').count(),
            'value': projects.filter(status__category='lost').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
    }

    # Chart data for each region (for JS)
    def get_chart_data(region_codes, currency):
        region_projects = projects.filter(region__code__in=region_codes)
        return {
            'active': region_projects.filter(status__category='active').count(),
            'hot_leads': region_projects.filter(status__category='hot_lead').count(),
            'won': region_projects.filter(status__category='won').count(),
            'lost': region_projects.filter(status__category='lost').count(),
            'ongoing': region_projects.filter(status__category='ongoing').count(),
            'active_value': float(region_projects.filter(status__category='active').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
            # Both match the tiles above — actual costing value, not estimate.
            'hot_leads_value': float(_resolved_value_for(
                region_projects.filter(status__category='hot_lead'), sales_values, currency, rates)),
            'won_value': float(_resolved_value_for(
                region_projects.filter(status__category='won'), sales_values, currency, rates)),
            'lost_value': float(region_projects.filter(status__category='lost').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
        }

    chart_data = {
        'lnuk': get_chart_data(['UK', 'GLB'], 'GBP'),
        'lna': get_chart_data(['LNA'], 'SAR'),
        'pa': get_chart_data(['PA'], 'SAR'),
        'neo_dubai': get_chart_data(['NEO-Dubai'], 'AED'),
        'neo_ksa': get_chart_data(['NEO-KSA'], 'SAR'),
    }

    context = {
        'overall_stats': overall_stats,
        'lnuk': lnuk_stats,
        'lna': lna_stats,
        'pa': pa_stats,
        'neo_dubai': neo_dubai_stats,
        'neo_ksa': neo_ksa_stats,
        'chart_data': chart_data,
    }

    return render(request, 'dashboard/index.html', context)


@login_required
@require_capability('dashboard.access')
def chart_data(request):
    """API endpoint for dashboard charts"""
    user = request.user

    if user.is_super_admin_user:
        projects = Project.objects.all()
    elif user.is_admin_user or user.is_manager_user:
        projects = Project.objects.filter(region=user.region)
    else:
        projects = Project.objects.filter(owner=user)

    # Region distribution
    regions = Region.objects.filter(is_active=True).annotate(
        value=Sum('projects__estimated_value', filter=Q(projects__in=projects))
    ).values('name', 'value')

    # Status distribution
    statuses = ProjectStatus.objects.filter(is_active=True).annotate(
        count=Count('projects', filter=Q(projects__in=projects))
    ).values('name', 'color', 'count')

    return JsonResponse({
        'regions': list(regions),
        'statuses': list(statuses),
    })


@login_required
def storage_report(request):
    """Super-admin view of file-storage usage by class + R2 orphan detection.

    Hardcoded super_admin gate (matches the permission-grid page). The backend
    scan is cached briefly so refreshing doesn't re-list the whole bucket;
    add ?refresh=1 to force a fresh scan.
    """
    from django.core.cache import cache
    from django.core.exceptions import PermissionDenied
    from dashboard.storage_report import build_storage_report

    if not request.user.is_super_admin_user:
        raise PermissionDenied

    cache_key = 'storage_report'
    report = None if request.GET.get('refresh') else cache.get(cache_key)
    if report is None:
        report = build_storage_report()
        cache.set(cache_key, report, 300)  # 5 minutes

    return render(request, 'dashboard/storage_report.html', {'report': report})


@login_required
def storage_orphan_preview(request):
    """Super-admin: open a storage object by key to inspect it before cleanup.

    Read-only — redirects to the file's (signed) URL. Django's storage layer
    rejects path-traversal keys, and this is super-admin only, so it can't be
    used to escape the media namespace.
    """
    from django.core.exceptions import PermissionDenied
    from django.core.files.storage import default_storage
    from django.http import Http404, HttpResponseRedirect

    if not request.user.is_super_admin_user:
        raise PermissionDenied
    key = request.GET.get('key', '')
    if not key:
        raise Http404('No key given')
    try:
        exists = default_storage.exists(key)
    except Exception:
        exists = False  # traversal / bad key rejected by the storage layer
    if not exists:
        raise Http404('Object not found')
    return HttpResponseRedirect(default_storage.url(key))
