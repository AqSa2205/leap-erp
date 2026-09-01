from collections import OrderedDict
from decimal import Decimal

from django.shortcuts import render
from django.utils.text import slugify
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

    # Base queryset based on user role. Sales reps (the else branch) see the
    # UNION of what they personally own plus their own region's pipeline -
    # not one or the other - so a rep assigned to one region who happens to
    # own a deal filed under a different region still sees that deal too.
    if user.is_super_admin_user:
        projects = Project.objects.all()
    elif user.is_admin_user or user.is_manager_user:
        projects = Project.objects.filter(region=user.region)
    elif getattr(user, 'is_finance_team_user', False):
        # Finance sees their own region (capability gate already passed).
        projects = Project.objects.filter(region=user.region)
    elif user.region_id:
        projects = Project.objects.filter(
            Q(owner=user) | Q(region=user.region)).distinct()
    else:
        projects = Project.objects.filter(owner=user)

    # Won / Hot Lead values come from the actual sales costing sheets. Resolved
    # once for every priced project the user can see, then reused by each region
    # tile and its chart so the costing tree is walked a single time per request.
    sales_values, rates = _resolve_sales_values(projects)

    # Region tabs are built dynamically from the Regions table rather than a
    # fixed list, so a newly created region appears automatically. Regions
    # sharing a dashboard_group (e.g. UK and Global both tagged 'LNUK') are
    # combined into one tab, exactly as LNUK was before this - a region with
    # no group set gets a tab of its own, keyed by its own code.
    CURRENCY_SYMBOLS = {'GBP': '£', 'USD': '$', 'SAR': 'SAR ', 'AED': 'AED '}

    region_groups = OrderedDict()
    for r in Region.objects.filter(is_active=True).order_by('name'):
        key = r.dashboard_group.strip() if r.dashboard_group else r.code
        region_groups.setdefault(key, []).append(r)

    # A Super Admin sees every tab with real figures. Everyone else only sees
    # real figures for the tab their own region belongs to - every other tab
    # still appears (so people know the region exists) but renders as a
    # skeleton with no numbers, never a computed real one.
    user_group_key = None
    if not user.is_super_admin_user and user.region_id:
        ur = user.region
        user_group_key = ur.dashboard_group.strip() if ur.dashboard_group else ur.code

    region_tabs = []
    for key, group_regions in region_groups.items():
        codes = [r.code for r in group_regions]
        currency = group_regions[0].currency
        can_view = user.is_super_admin_user or key == user_group_key
        if can_view:
            stats = get_region_stats(projects, codes, sales_values, currency, rates)
        else:
            stats = None
        region_tabs.append({
            'key': key,
            'slug': slugify(key),
            'name': key,
            'full_name': ' & '.join(r.name for r in group_regions),
            'currency': currency,
            'currency_symbol': CURRENCY_SYMBOLS.get(currency, currency),
            'codes': codes,
            'can_view': can_view,
            'stats': stats,
        })

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

    # Chart data for each region (for JS). Only computed for tabs the viewer
    # can actually see - a locked tab gets no chart entry at all, so nothing
    # ever reaches the page source for a region someone shouldn't see.
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
        tab['slug']: get_chart_data(tab['codes'], tab['currency'])
        for tab in region_tabs if tab['can_view']
    }

    context = {
        'overall_stats': overall_stats,
        'region_tabs': region_tabs,
        'chart_data': chart_data,
        'is_super_admin': user.is_super_admin_user,
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

    if not (request.user.is_super_admin_user or request.user.is_erp_admin_user):
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

    if not (request.user.is_super_admin_user or request.user.is_erp_admin_user):
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
