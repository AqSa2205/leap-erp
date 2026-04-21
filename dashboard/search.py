"""Global search endpoint.

Returns matches across the main business objects so the top-bar
Ctrl+K box can jump to anything by reference, name, client, vendor,
etc. Each result carries the absolute URL to its detail page.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse

from projects.models import Project, Document
from proposals.models import TechnicalProposal, PrequalificationDocument
from costing.models import CostingSheet, VendorQuote


MAX_PER_GROUP = 5


def _scope_projects(qs, user):
    if user.is_super_admin_user:
        return qs
    if user.is_admin_user or user.is_manager_user:
        return qs.filter(Q(owner=user) | Q(region=user.region))
    return qs.filter(owner=user)


def _scope_costing(qs, user):
    if user.is_super_admin_user or getattr(user, 'is_proposal_team_user', False):
        return qs
    if user.is_admin_user or user.is_manager_user:
        return qs.filter(Q(created_by=user) | Q(project__region=user.region))
    return qs.filter(created_by=user)


def _scope_proposals(qs, user):
    if user.is_super_admin_user:
        return qs
    if user.is_admin_user or user.is_manager_user:
        return qs.filter(Q(created_by=user) | Q(project__region=user.region))
    return qs.filter(created_by=user)


@login_required
def global_search(request):
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'q': q, 'groups': []})

    user = request.user
    groups = []

    # Commercial Pipeline (Projects)
    proj_qs = Project.objects.select_related('region').filter(
        Q(proposal_reference__icontains=q) |
        Q(project_name__icontains=q) |
        Q(customer__icontains=q) |
        Q(end_user__icontains=q) |
        Q(client_rfq_reference__icontains=q) |
        Q(po_number__icontains=q)
    )
    proj_qs = _scope_projects(proj_qs, user)[:MAX_PER_GROUP]
    proj_hits = [{
        'title': p.proposal_reference,
        'subtitle': p.project_name or p.customer or '',
        'url': reverse('projects:detail', args=[p.pk]),
    } for p in proj_qs]
    if proj_hits:
        groups.append({'label': 'Commercial Pipeline', 'icon': 'bi-folder', 'hits': proj_hits})

    # Technical Proposals
    tp_qs = TechnicalProposal.objects.select_related('project').filter(
        Q(title__icontains=q) |
        Q(proposal_reference__icontains=q) |
        Q(client_name__icontains=q) |
        Q(project_description__icontains=q)
    )
    tp_qs = _scope_proposals(tp_qs, user)[:MAX_PER_GROUP]
    tp_hits = [{
        'title': tp.title,
        'subtitle': f'{tp.proposal_reference} · {tp.client_name}',
        'url': reverse('proposals:detail', args=[tp.pk]),
    } for tp in tp_qs]
    if tp_hits:
        groups.append({'label': 'Technical Proposals', 'icon': 'bi-file-earmark-richtext', 'hits': tp_hits})

    # Costing Sheets
    cs_qs = CostingSheet.objects.select_related('project').filter(
        Q(title__icontains=q) |
        Q(customer_reference__icontains=q) |
        Q(customer_name__icontains=q) |
        Q(end_user__icontains=q) |
        Q(project__proposal_reference__icontains=q) |
        Q(project__project_name__icontains=q)
    )
    cs_qs = _scope_costing(cs_qs, user)[:MAX_PER_GROUP]
    cs_hits = [{
        'title': cs.title,
        'subtitle': (cs.project.proposal_reference if cs.project_id else cs.customer_reference or ''),
        'url': reverse('costing:detail', args=[cs.pk]),
    } for cs in cs_qs]
    if cs_hits:
        groups.append({'label': 'Costing Sheets', 'icon': 'bi-calculator', 'hits': cs_hits})

    # Vendor Quotes
    vq_qs = VendorQuote.objects.select_related('sheet', 'sheet__project').filter(
        Q(vendor_name__icontains=q) |
        Q(quote_reference__icontains=q) |
        Q(original_filename__icontains=q) |
        Q(notes__icontains=q)
    )
    if not user.is_super_admin_user:
        if user.is_admin_user or user.is_manager_user:
            vq_qs = vq_qs.filter(Q(sheet__created_by=user) | Q(sheet__project__region=user.region))
        elif getattr(user, 'is_proposal_team_user', False):
            vq_qs = vq_qs.none()
        else:
            vq_qs = vq_qs.filter(sheet__created_by=user)
    vq_qs = vq_qs[:MAX_PER_GROUP]
    vq_hits = [{
        'title': vq.vendor_name,
        'subtitle': f'{vq.quote_reference or "no ref"} · {vq.sheet.title}',
        'url': reverse('costing:detail', args=[vq.sheet_id]),
    } for vq in vq_qs]
    if vq_hits:
        groups.append({'label': 'Vendor Quotes', 'icon': 'bi-receipt', 'hits': vq_hits})

    # Prequalification Documents
    pqd_qs = PrequalificationDocument.objects.select_related('project').filter(
        Q(title__icontains=q) |
        Q(pqd_reference__icontains=q) |
        Q(client_name__icontains=q)
    )
    pqd_qs = _scope_proposals(pqd_qs, user)[:MAX_PER_GROUP]
    pqd_hits = [{
        'title': pqd.title,
        'subtitle': f'{pqd.pqd_reference} · {pqd.client_name}',
        'url': reverse('proposals:pqd_detail', args=[pqd.pk]),
    } for pqd in pqd_qs]
    if pqd_hits:
        groups.append({'label': 'Prequalification', 'icon': 'bi-file-earmark-check', 'hits': pqd_hits})

    # Documents (standalone files)
    doc_qs = Document.objects.select_related('project').filter(
        Q(name__icontains=q) |
        Q(reference_number__icontains=q) |
        Q(vendor_name__icontains=q)
    )
    if not user.is_super_admin_user:
        if user.is_admin_user or user.is_manager_user:
            doc_qs = doc_qs.filter(Q(uploaded_by=user) | Q(project__region=user.region))
        else:
            doc_qs = doc_qs.filter(uploaded_by=user)
    doc_qs = doc_qs[:MAX_PER_GROUP]
    doc_hits = [{
        'title': d.name,
        'subtitle': f'{d.get_document_type_display()} · {d.reference_number or d.vendor_name or ""}'.strip(' ·'),
        'url': reverse('projects:document_detail', args=[d.pk]),
    } for d in doc_qs]
    if doc_hits:
        groups.append({'label': 'Documents', 'icon': 'bi-file-earmark-text', 'hits': doc_hits})

    return JsonResponse({'q': q, 'groups': groups})
