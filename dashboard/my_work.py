"""Per-user 'My Work' page.

Pulls together the things the logged-in user actually needs to act on,
based on their role — handoffs in their court, drafts they started,
stale items they own, and recent notifications. Replaces the need to
scan the full dashboard to figure out what to open next.
"""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone


STALE_DAYS = 14
RECENT_DAYS = 7


def _role_flags(user):
    return {
        'is_proposal':    getattr(user, 'is_proposal_team_user', False),
        'is_sales':       getattr(user, 'is_sales_team_user', False) or user.is_admin_user or user.is_manager_user,
        'is_finance':     getattr(user, 'is_finance_team_user', False),
        'is_procurement': getattr(user, 'is_procurement_user', False),
        'is_admin':       user.is_super_admin_user or user.is_admin_user,
    }


@login_required
def my_work(request):
    from costing.models import CostingSheet, CostingSheetChangeLog
    from projects.models import Project
    from proposals.models import TechnicalProposal, PrequalificationDocument
    from notifications.models import Notification

    user = request.user
    flags = _role_flags(user)
    now = timezone.now()

    action_items = []
    drafts = []
    stale = []
    pricing_changes = []

    # ─── Needs your attention ──────────────────────────────────
    if flags['is_proposal'] or user.is_super_admin_user:
        bom_qs = CostingSheet.objects.filter(workflow_stage='bom_in_progress')
        if not user.is_super_admin_user:
            bom_qs = bom_qs.filter(created_by=user)
        for sheet in bom_qs.select_related('project').order_by('-updated_at')[:10]:
            action_items.append({
                'title':    f'Finish BOM: {sheet.title}',
                'subtitle': (sheet.project.proposal_reference if sheet.project_id else '') + ' · still drafting',
                'url':      reverse('costing:detail', args=[sheet.pk]) + '?view=bom',
                'icon':     'bi-list-check',
                'color':    '#ffc107',
                'meta':     sheet.updated_at,
            })

    if flags['is_sales'] or user.is_super_admin_user:
        # Sheets waiting for the sales team to pick up
        ready_qs = CostingSheet.objects.filter(workflow_stage='ready_for_costing')
        if not user.is_super_admin_user:
            ready_qs = ready_qs.filter(
                Q(created_by=user) | Q(project__region=user.region)
            )
        for sheet in ready_qs.select_related('project', 'handed_over_by').order_by('-handed_over_at')[:10]:
            ho = sheet.handed_over_by
            action_items.append({
                'title':    f'Start costing: {sheet.title}',
                'subtitle': (sheet.project.proposal_reference if sheet.project_id else '') + (f' · handed over by {ho.get_full_name() or ho.username}' if ho else ''),
                'url':      reverse('costing:detail', args=[sheet.pk]),
                'icon':     'bi-arrow-right-square',
                'color':    '#0d6efd',
                'meta':     sheet.handed_over_at or sheet.updated_at,
            })

        # Sheets the sales team is mid-way through
        mid_qs = CostingSheet.objects.filter(workflow_stage='costing_in_progress')
        if not user.is_super_admin_user:
            mid_qs = mid_qs.filter(
                Q(created_by=user) | Q(project__region=user.region)
            )
        for sheet in mid_qs.select_related('project').order_by('-updated_at')[:5]:
            action_items.append({
                'title':    f'Finalize: {sheet.title}',
                'subtitle': (sheet.project.proposal_reference if sheet.project_id else '') + ' · costing in progress',
                'url':      reverse('costing:detail', args=[sheet.pk]),
                'icon':     'bi-cash-coin',
                'color':    '#198754',
                'meta':     sheet.updated_at,
            })

    # ─── Finance: sheets queued for budget review ──────────────
    if flags['is_finance'] or user.is_super_admin_user:
        review_qs = CostingSheet.objects.filter(workflow_stage='finance_review').select_related('project', 'finance_review_by')
        if not user.is_super_admin_user:
            review_qs = review_qs.filter(project__region=user.region)
        for sheet in review_qs.order_by('-finance_review_at')[:10]:
            handed_by = sheet.finance_review_by
            subtitle_parts = []
            if sheet.project_id:
                subtitle_parts.append(sheet.project.proposal_reference or sheet.project.project_name)
            subtitle_parts.append('sent for finance review')
            if handed_by:
                subtitle_parts.append(f'by {handed_by.get_full_name() or handed_by.username}')
            action_items.append({
                'title':    f'Review budget: {sheet.title}',
                'subtitle': ' · '.join(filter(None, subtitle_parts)),
                'url':      reverse('costing:detail', args=[sheet.pk]),
                'icon':     'bi-bank',
                'color':    '#6f42c1',
                'meta':     sheet.finance_review_at or sheet.updated_at,
            })

    # ─── Procurement: BOMs ready to procure + POs awaiting signature ──
    if flags['is_procurement'] or user.is_super_admin_user:
        ready_qs = CostingSheet.objects.filter(
            workflow_stage='finance_approved',
            project__status__category='won',
        ).select_related('project', 'finance_approved_by')
        if not user.is_super_admin_user:
            ready_qs = ready_qs.filter(project__region=user.region)
        for sheet in ready_qs.order_by('-finance_approved_at')[:10]:
            action_items.append({
                'title':    f'Procure BOM: {sheet.title}',
                'subtitle': (sheet.project.proposal_reference if sheet.project_id else '') + ' · finance approved · open the tracker',
                'url':      reverse('procurement:bom_procurement_tracker', args=[sheet.pk]),
                'icon':     'bi-cart-plus',
                'color':    '#fd7e14',
                'meta':     sheet.finance_approved_at or sheet.updated_at,
            })

    # POs awaiting *this* user's approval — filtered Python-side because
    # current_stage is a derived property. Limited to 50 recent POs to
    # keep the page fast; that's far more than any healthy backlog.
    if flags['is_procurement'] or flags['is_admin'] or user.is_super_admin_user:
        from procurement.views import _visible_pos_for
        recent_pos = _visible_pos_for(user).order_by('-updated_at')[:50]
        for po in recent_pos:
            cur = po.current_stage
            if not cur or not po.can_user_approve_stage(user, cur['key']):
                continue
            action_items.append({
                'title':    f'Sign {cur["label"]}: {po.po_number}',
                'subtitle': f'{po.vendor_name or "no vendor yet"} · {po.project_name or ""}',
                'url':      reverse('procurement:po_detail', args=[po.pk]),
                'icon':     'bi-pen',
                'color':    '#0d6efd',
                'meta':     po.updated_at,
            })

    action_items.sort(key=lambda e: e['meta'] or now, reverse=True)
    action_items = action_items[:15]

    # ─── Your drafts ───────────────────────────────────────────
    tp_qs = TechnicalProposal.objects.filter(status='draft', created_by=user).select_related('project').order_by('-updated_at')[:10]
    for tp in tp_qs:
        drafts.append({
            'title':    tp.title,
            'subtitle': f'{tp.proposal_reference} · Technical Proposal draft',
            'url':      reverse('proposals:content', args=[tp.pk]),
            'icon':     'bi-file-earmark-richtext',
            'color':    '#0dcaf0',
            'meta':     tp.updated_at,
        })

    pqd_qs = PrequalificationDocument.objects.filter(created_by=user).select_related('project').order_by('-updated_at')[:5]
    for pqd in pqd_qs:
        drafts.append({
            'title':    pqd.title,
            'subtitle': f'{pqd.pqd_reference} · Prequalification',
            'url':      reverse('proposals:pqd_detail', args=[pqd.pk]),
            'icon':     'bi-file-earmark-check',
            'color':    '#6c757d',
            'meta':     pqd.updated_at,
        })

    # Pipeline entries I own with no costing sheet yet
    owned_no_cost = Project.objects.filter(owner=user).exclude(
        costing_sheets__isnull=False
    ).order_by('-updated_at')[:8]
    for p in owned_no_cost:
        drafts.append({
            'title':    p.proposal_reference,
            'subtitle': f'{p.project_name or ""} · pipeline entry without a costing sheet',
            'url':      reverse('projects:detail', args=[p.pk]),
            'icon':     'bi-folder',
            'color':    '#1a1a1a',
            'meta':     p.updated_at,
        })

    drafts.sort(key=lambda e: e['meta'] or now, reverse=True)
    drafts = drafts[:12]

    # ─── Stale items I own ─────────────────────────────────────
    stale_cutoff = now - timedelta(days=STALE_DAYS)
    stale_qs = Project.objects.filter(owner=user, updated_at__lt=stale_cutoff).exclude(
        status__category__in=['won', 'lost']
    ).select_related('status').order_by('updated_at')[:10]
    for p in stale_qs:
        days = (now - p.updated_at).days if p.updated_at else 0
        stale.append({
            'title':    p.proposal_reference,
            'subtitle': f'{p.project_name or ""} · no update in {days} day{"s" if days != 1 else ""}',
            'url':      reverse('projects:detail', args=[p.pk]),
            'icon':     'bi-clock-history',
            'color':    '#fd7e14',
            'meta':     p.updated_at,
        })

    # ─── Pricing changes worth reviewing (sales only) ──────────
    if flags['is_sales'] or user.is_super_admin_user:
        recent_cutoff = now - timedelta(days=RECENT_DAYS)
        log_qs = CostingSheetChangeLog.objects.filter(
            created_at__gte=recent_cutoff,
            is_pricing_change=True,
            actor_team='proposal',
        ).select_related('sheet', 'actor').order_by('-created_at')
        if not user.is_super_admin_user:
            log_qs = log_qs.filter(
                Q(sheet__created_by=user) | Q(sheet__project__region=user.region)
            )
        for entry in log_qs[:10]:
            act = entry.actor
            pricing_changes.append({
                'title':    f'{entry.scope_label or "Sheet"}: {entry.field}',
                'subtitle': f'{entry.sheet.title} · "{entry.before}" → "{entry.after}"' + (f' · by {act.get_full_name() or act.username}' if act else ''),
                'url':      reverse('costing:detail', args=[entry.sheet_id]),
                'icon':     'bi-pencil-square',
                'color':    '#dc3545',
                'meta':     entry.created_at,
            })

    # ─── Unread notifications (last 8) ─────────────────────────
    unread_notif_qs = Notification.objects.filter(recipient=user, is_read=False).select_related('actor').order_by('-created_at')
    notif_unread_count = unread_notif_qs.count()
    notif_qs = unread_notif_qs[:8]

    return render(request, 'dashboard/my_work.html', {
        'action_items':       action_items,
        'drafts':             drafts,
        'stale':              stale,
        'pricing_changes':    pricing_changes,
        'notifications':      notif_qs,
        'notif_unread_count': notif_unread_count,
        'flags':              flags,
    })
