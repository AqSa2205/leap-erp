from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.http import JsonResponse

from projects.models import Project, Region, ProjectStatus
from accounts.models import User


def get_region_stats(projects, region_codes):
    """Helper to get stats for a specific region or regions"""
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
            'value': hot_leads.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'won': {
            'count': won.count(),
            'value': won.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
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
    """Main dashboard view with regional tabs"""
    user = request.user

    # Base queryset based on user role
    if user.is_admin_user:
        projects = Project.objects.all()
    elif user.is_manager_user:
        projects = Project.objects.filter(region=user.region)
    else:
        projects = Project.objects.filter(owner=user)

    # Get stats for each region group
    # LNUK = UK + Global (GBP)
    lnuk_stats = get_region_stats(projects, ['UK', 'GLB'])
    lnuk_stats['currency'] = 'GBP'
    lnuk_stats['currency_symbol'] = '£'
    lnuk_stats['name'] = 'LNUK'
    lnuk_stats['full_name'] = 'Leap Networks UK & Global'

    # LNA = Leap Networks Arabia (SAR)
    lna_stats = get_region_stats(projects, ['LNA'])
    lna_stats['currency'] = 'SAR'
    lna_stats['currency_symbol'] = 'SAR'
    lna_stats['name'] = 'LNA'
    lna_stats['full_name'] = 'Leap Networks Arabia'

    # PA = Pace Arabia (SAR)
    pa_stats = get_region_stats(projects, ['PA'])
    pa_stats['currency'] = 'SAR'
    pa_stats['currency_symbol'] = 'SAR'
    pa_stats['name'] = 'PA'
    pa_stats['full_name'] = 'Pace Arabia'

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
            'value': projects.filter(status__category='won').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
        'lost': {
            'count': projects.filter(status__category='lost').count(),
            'value': projects.filter(status__category='lost').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0
        },
    }

    # Chart data for each region (for JS)
    def get_chart_data(region_codes):
        region_projects = projects.filter(region__code__in=region_codes)
        return {
            'active': region_projects.filter(status__category='active').count(),
            'hot_leads': region_projects.filter(status__category='hot_lead').count(),
            'won': region_projects.filter(status__category='won').count(),
            'lost': region_projects.filter(status__category='lost').count(),
            'ongoing': region_projects.filter(status__category='ongoing').count(),
            'active_value': float(region_projects.filter(status__category='active').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
            'hot_leads_value': float(region_projects.filter(status__category='hot_lead').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
            'won_value': float(region_projects.filter(status__category='won').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
            'lost_value': float(region_projects.filter(status__category='lost').aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
        }

    chart_data = {
        'lnuk': get_chart_data(['UK', 'GLB']),
        'lna': get_chart_data(['LNA']),
        'pa': get_chart_data(['PA']),
    }

    context = {
        'overall_stats': overall_stats,
        'lnuk': lnuk_stats,
        'lna': lna_stats,
        'pa': pa_stats,
        'chart_data': chart_data,
    }

    return render(request, 'dashboard/index.html', context)


@login_required
def chart_data(request):
    """API endpoint for dashboard charts"""
    user = request.user

    if user.is_admin_user:
        projects = Project.objects.all()
    elif user.is_manager_user:
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
