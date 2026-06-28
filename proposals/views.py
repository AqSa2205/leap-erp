from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Max

from .models import (
    TechnicalProposal, ProposalBoilerplate,
    PrequalificationDocument, PQDAttachment,
    ProposalSection, SectionHeading,
)
from .forms import (
    ProposalMetadataForm, ProposalContentForm, EngineeringDocumentFormSet,
    ProposalSectionFormSet, ProposalFilterForm, ProposalBoilerplateForm,
    PQDMetadataForm, PQDFilterForm,
)


@login_required
def proposals_dashboard(request):
    """Landing page for the Proposals menu — shows stats + recent activity
    across Technical (proposals), BOMs/Costing (costing sheets), Vendor
    Quotes, and Client RFQs."""
    from costing.models import CostingSheet, VendorQuote, CostingSheetRevision
    from projects.models import Document
    user = request.user

    # Counts + recent items, scoped to what the user can see
    tp_qs = TechnicalProposal.objects.select_related('project', 'created_by')
    if not user.is_super_admin_user:
        tp_qs = tp_qs.filter(
            Q(created_by=user) |
            Q(project__region=user.region)
        ) if (user.is_admin_user or user.is_manager_user) else tp_qs.filter(created_by=user)
    tp_counts = {
        'total':     tp_qs.count(),
        'draft':     tp_qs.filter(status='draft').count(),
        'review':    tp_qs.filter(status='review').count(),
        'final':     tp_qs.filter(status='final').count(),
        'submitted': tp_qs.filter(status='submitted').count(),
    }

    cs_qs = CostingSheet.objects.select_related('project', 'created_by')
    if not user.is_super_admin_user and not getattr(user, 'is_proposal_team_user', False):
        if user.is_admin_user or user.is_manager_user:
            cs_qs = cs_qs.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            cs_qs = cs_qs.filter(created_by=user)
    cs_counts = {
        'total':           cs_qs.count(),
        'bom':             cs_qs.filter(workflow_stage='bom_in_progress').count(),
        'ready':           cs_qs.filter(workflow_stage='ready_for_costing').count(),
        'costing':         cs_qs.filter(workflow_stage='costing_in_progress').count(),
        'finalized':       cs_qs.filter(workflow_stage='finalized').count(),
    }

    vq_count = VendorQuote.objects.filter(sheet__in=cs_qs).count()
    cp_pdf_count = CostingSheetRevision.objects.filter(sheet__in=cs_qs, export_format='pdf').count()
    rfq_count = Document.objects.filter(document_type='rfq').count()

    recent_tp = tp_qs.order_by('-updated_at')[:5]
    recent_bom = cs_qs.filter(workflow_stage__in=['bom_in_progress', 'ready_for_costing']).order_by('-updated_at')[:5]
    recent_costing = cs_qs.filter(workflow_stage__in=['costing_in_progress', 'finalized']).order_by('-updated_at')[:5]

    return render(request, 'proposals/dashboard.html', {
        'tp_counts': tp_counts,
        'cs_counts': cs_counts,
        'vq_count': vq_count,
        'cp_pdf_count': cp_pdf_count,
        'rfq_count': rfq_count,
        'recent_tp': recent_tp,
        'recent_bom': recent_bom,
        'recent_costing': recent_costing,
    })


class ProposalPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_queryset(self):
        queryset = TechnicalProposal.objects.select_related('project', 'created_by').all()
        user = self.request.user
        if user.is_super_admin_user:
            return queryset
        elif user.is_admin_user or user.is_manager_user:
            return queryset.filter(
                Q(created_by=user) |
                Q(project__region=user.region)
            )
        else:
            return queryset.filter(created_by=user)


# ─── Proposal CRUD ───────────────────────────────────────────

class ProposalListView(ProposalPermissionMixin, ListView):
    model = TechnicalProposal
    template_name = 'proposals/proposal_list.html'
    context_object_name = 'proposals'
    paginate_by = 25

    def test_func(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) |
                Q(proposal_reference__icontains=search) |
                Q(client_name__icontains=search) |
                Q(project__project_name__icontains=search)
            )
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by('-updated_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = ProposalFilterForm(self.request.GET)
        context['total_count'] = self.get_queryset().count()
        return context


class ProposalCreateView(LoginRequiredMixin, CreateView):
    model = TechnicalProposal
    form_class = ProposalMetadataForm
    template_name = 'proposals/proposal_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Proposal created successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('proposals:content', kwargs={'pk': self.object.pk})


class ProposalDetailView(ProposalPermissionMixin, DetailView):
    model = TechnicalProposal
    template_name = 'proposals/proposal_detail.html'
    context_object_name = 'proposal'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['engineering_docs'] = self.object.engineering_documents.all()
        return context


class ProposalUpdateView(ProposalPermissionMixin, UpdateView):
    model = TechnicalProposal
    form_class = ProposalMetadataForm
    template_name = 'proposals/proposal_form.html'

    def test_func(self):
        obj = self.get_object()
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user:
            return True
        return obj.created_by == user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, 'Proposal updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('proposals:detail', kwargs={'pk': self.object.pk})


class ProposalDeleteView(ProposalPermissionMixin, DeleteView):
    model = TechnicalProposal
    template_name = 'proposals/proposal_confirm_delete.html'
    success_url = reverse_lazy('proposals:list')

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user:
            return True
        obj = self.get_object()
        return obj.created_by == user

    def form_valid(self, form):
        messages.success(self.request, 'Proposal deleted.')
        return super().form_valid(form)


# ─── Tabbed Content Editor ────────────────────────────────────

def _can_edit_proposal(user, proposal):
    return (user.is_super_admin_user or user.is_admin_user
            or proposal.created_by == user)


class ProposalEditContentView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Content editor: a dynamic, reorderable list of proposal sections (each a
    heading + rich text) plus the engineering-documents table. Sections are
    chosen from the heading library or typed custom."""
    template_name = 'proposals/proposal_edit_content.html'

    def get_object(self):
        return get_object_or_404(TechnicalProposal, pk=self.kwargs['pk'])

    def test_func(self):
        return _can_edit_proposal(self.request.user, self.get_object())

    def _context(self, obj, section_fs, eng_fs):
        return {
            'object': obj,
            'section_formset': section_fs,
            'eng_formset': eng_fs,
            'headings': SectionHeading.objects.filter(is_active=True),
        }

    def get(self, request, pk):
        obj = self.get_object()
        return render(request, self.template_name, self._context(
            obj,
            ProposalSectionFormSet(instance=obj, prefix='sec'),
            EngineeringDocumentFormSet(instance=obj, prefix='eng'),
        ))

    def post(self, request, pk):
        obj = self.get_object()
        section_fs = ProposalSectionFormSet(request.POST, instance=obj, prefix='sec')
        eng_fs = EngineeringDocumentFormSet(request.POST, instance=obj, prefix='eng')
        if section_fs.is_valid() and eng_fs.is_valid():
            section_fs.save()
            eng_fs.save()
            messages.success(request, 'Proposal content saved.')
            return redirect('proposals:detail', pk=obj.pk)
        return render(request, self.template_name,
                      self._context(obj, section_fs, eng_fs))


@login_required
@require_POST
def add_proposal_section(request, pk):
    """Add one or more sections to a proposal at once — any number of checked
    library headings (each pre-fills its default content) plus an optional typed
    custom heading. Returns to the editor."""
    proposal = get_object_or_404(TechnicalProposal, pk=pk)
    if not _can_edit_proposal(request.user, proposal):
        messages.error(request, 'Permission denied.')
        return redirect('proposals:content', pk=pk)

    # Checked library headings, in the order the form submitted them, plus an
    # optional typed custom heading.
    chosen = [h.strip() for h in request.POST.getlist('heading') if h.strip()]
    custom = (request.POST.get('custom_heading') or '').strip()
    if custom:
        chosen.append(custom)
    if not chosen:
        messages.error(request, 'Pick at least one heading, or type a custom one.')
        return redirect('proposals:content', pk=pk)

    next_order = (proposal.sections.aggregate(m=Max('order'))['m'] or 0) + 1
    is_super = request.user.is_super_admin_user
    for heading in chosen:
        lib = SectionHeading.objects.filter(name__iexact=heading).first()
        # A super admin's custom (non-library) headings join the shared library
        # so they appear in the Add panel for every future proposal.
        if lib is None and is_super:
            lib_order = (SectionHeading.objects.aggregate(m=Max('order'))['m'] or 0) + 1
            lib = SectionHeading.objects.create(name=heading, order=lib_order)
        ProposalSection.objects.create(
            proposal=proposal,
            heading=lib.name if lib else heading,
            content=(lib.default_content if lib else ''),
            order=next_order)
        next_order += 1

    count = len(chosen)
    messages.success(
        request, f'{count} section{"" if count == 1 else "s"} added.')
    return redirect('proposals:content', pk=pk)


# ─── AJAX ─────────────────────────────────────────────────────

@login_required
@require_POST
def ajax_save_section(request, pk):
    proposal = get_object_or_404(TechnicalProposal, pk=pk)
    # Permission check
    user = request.user
    if not (user.is_super_admin_user or user.is_admin_user) and proposal.created_by != user:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    field = request.POST.get('field')
    value = request.POST.get('value', '')

    allowed = [f[0] for f in TechnicalProposal.SECTION_FIELDS]
    if field not in allowed:
        return JsonResponse({'error': 'Invalid field'}, status=400)

    setattr(proposal, field, value)
    proposal.save(update_fields=[field, 'updated_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def ajax_upload_image(request, pk):
    """Upload an image for use in the proposal content editor.
    Returns JSON { location: '/media/...' } per TinyMCE's upload handler contract."""
    import os
    from django.conf import settings
    from django.utils.text import slugify
    from django.core.files.storage import default_storage
    from django.core.files.base import ContentFile
    from uuid import uuid4

    proposal = get_object_or_404(TechnicalProposal, pk=pk)
    user = request.user
    if not (user.is_super_admin_user or user.is_admin_user) and proposal.created_by != user:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file'}, status=400)

    # Enforce size + type
    if uploaded.size > 10 * 1024 * 1024:  # 10 MB
        return JsonResponse({'error': 'File too large (max 10MB)'}, status=400)
    content_type = uploaded.content_type or ''
    if not content_type.startswith('image/'):
        return JsonResponse({'error': 'Only image files allowed'}, status=400)

    # Build a safe filename
    ext = os.path.splitext(uploaded.name)[1].lower() or '.png'
    safe_name = f'{uuid4().hex}{ext}'
    path = f'proposals/images/proposal_{proposal.pk}/{safe_name}'

    saved_path = default_storage.save(path, ContentFile(uploaded.read()))
    # Build absolute URL with leading slash — MEDIA_URL may or may not have one
    media_prefix = '/' + settings.MEDIA_URL.strip('/')
    location = media_prefix + '/' + saved_path.replace('\\', '/')

    return JsonResponse({'location': location})


@login_required
def ajax_load_boilerplate(request, pk):
    boilerplate = get_object_or_404(ProposalBoilerplate, pk=pk)
    return JsonResponse({
        'content': boilerplate.content,
        'section': boilerplate.section,
        'name': boilerplate.name,
    })


# ─── DOCX Export ──────────────────────────────────────────────

@login_required
def proposal_export_docx(request, pk):
    proposal = get_object_or_404(TechnicalProposal, pk=pk)
    from .docx_export import generate_proposal_docx
    return generate_proposal_docx(proposal)


# ─── Boilerplate CRUD ─────────────────────────────────────────

class BoilerplateListView(LoginRequiredMixin, ListView):
    model = ProposalBoilerplate
    template_name = 'proposals/boilerplate_list.html'
    context_object_name = 'boilerplates'
    paginate_by = 25


class BoilerplateCreateView(LoginRequiredMixin, CreateView):
    model = ProposalBoilerplate
    form_class = ProposalBoilerplateForm
    template_name = 'proposals/boilerplate_form.html'
    success_url = reverse_lazy('proposals:boilerplate_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Boilerplate created.')
        return super().form_valid(form)


class BoilerplateUpdateView(LoginRequiredMixin, UpdateView):
    model = ProposalBoilerplate
    form_class = ProposalBoilerplateForm
    template_name = 'proposals/boilerplate_form.html'
    success_url = reverse_lazy('proposals:boilerplate_list')

    def form_valid(self, form):
        messages.success(self.request, 'Boilerplate updated.')
        return super().form_valid(form)


class BoilerplateDeleteView(LoginRequiredMixin, DeleteView):
    model = ProposalBoilerplate
    template_name = 'proposals/proposal_confirm_delete.html'
    success_url = reverse_lazy('proposals:boilerplate_list')

    def form_valid(self, form):
        messages.success(self.request, 'Boilerplate deleted.')
        return super().form_valid(form)


# ─── Prequalification Document (PQD) ─────────────────────────

class PQDPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    """PQD access is restricted to super admins only."""

    def test_func(self):
        return bool(getattr(self.request.user, 'is_super_admin_user', False))

    def get_queryset(self):
        # Only super admins reach here (test_func already gates access).
        return PrequalificationDocument.objects.select_related('project', 'created_by').all()


class PQDListView(PQDPermissionMixin, ListView):
    model = PrequalificationDocument
    template_name = 'proposals/pqd_list.html'
    context_object_name = 'pqds'
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset()
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(pqd_reference__icontains=search) |
                Q(client_name__icontains=search) |
                Q(project__project_name__icontains=search)
            )
        if status:
            qs = qs.filter(status=status)
        return qs.order_by('-updated_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = PQDFilterForm(self.request.GET)
        context['total_count'] = self.get_queryset().count()
        return context


class PQDCreateView(PQDPermissionMixin, CreateView):
    model = PrequalificationDocument
    form_class = PQDMetadataForm
    template_name = 'proposals/pqd_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Prequalification document created.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('proposals:pqd_content', kwargs={'pk': self.object.pk})


class PQDDetailView(PQDPermissionMixin, DetailView):
    model = PrequalificationDocument
    template_name = 'proposals/pqd_detail.html'
    context_object_name = 'pqd'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        attachments_by_section = {}
        for att in self.object.attachments.all():
            attachments_by_section.setdefault(att.section, []).append(att)
        context['attachments_by_section'] = attachments_by_section
        return context


class PQDUpdateView(PQDPermissionMixin, UpdateView):
    model = PrequalificationDocument
    form_class = PQDMetadataForm
    template_name = 'proposals/pqd_form.html'
    context_object_name = 'pqd'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, 'PQD metadata updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('proposals:pqd_detail', kwargs={'pk': self.object.pk})


class PQDDeleteView(PQDPermissionMixin, DeleteView):
    model = PrequalificationDocument
    template_name = 'proposals/pqd_confirm_delete.html'
    success_url = reverse_lazy('proposals:pqd_list')
    context_object_name = 'pqd'

    def form_valid(self, form):
        messages.success(self.request, 'PQD deleted.')
        return super().form_valid(form)


class PQDEditContentView(PQDPermissionMixin, DetailView):
    """Tab-based editor — every PQD section has both a TinyMCE text area
    and a file upload zone."""
    model = PrequalificationDocument
    template_name = 'proposals/pqd_edit_content.html'
    context_object_name = 'pqd'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Group attachments by section for rendering
        attachments_by_section = {k: [] for k in PrequalificationDocument.FILE_SECTION_KEYS}
        for att in self.object.attachments.all():
            attachments_by_section.setdefault(att.section, []).append(att)
        context['attachments_by_section'] = attachments_by_section
        context['sections'] = PrequalificationDocument.SECTIONS
        return context


@login_required
@require_POST
def ajax_save_pqd_cover_field(request, pk):
    """Update a single cover-page metadata field on a PQD."""
    pqd = get_object_or_404(PrequalificationDocument, pk=pk)
    user = request.user
    if not getattr(user, 'is_super_admin_user', False):
        return JsonResponse({'error': 'Permission denied — super admin only'}, status=403)

    field = request.POST.get('field', '')
    value = request.POST.get('value', '').strip()

    allowed = {
        'title', 'pqd_reference', 'document_type', 'client_name',
        'project_description', 'region_entity', 'revision', 'revision_date',
        'prepared_by_initials', 'checked_by_initials', 'approved_by_initials',
    }
    if field not in allowed:
        return JsonResponse({'error': f'Invalid field: {field!r}'}, status=400)

    # Validate region against choices
    if field == 'region_entity':
        valid = [c for c, _ in PrequalificationDocument.REGION_CHOICES]
        if value and value not in valid:
            return JsonResponse({'error': 'Invalid region'}, status=400)

    # Parse date
    if field == 'revision_date':
        if not value:
            return JsonResponse({'error': 'Revision date is required'}, status=400)
        from datetime import datetime
        try:
            parsed = datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'error': 'Date must be YYYY-MM-DD'}, status=400)
        setattr(pqd, field, parsed)
    else:
        # Ensure unique reference
        if field == 'pqd_reference':
            if not value:
                return JsonResponse({'error': 'Reference cannot be empty'}, status=400)
            exists = PrequalificationDocument.objects.exclude(pk=pk).filter(pqd_reference=value).exists()
            if exists:
                return JsonResponse({'error': 'Reference already in use'}, status=400)
        setattr(pqd, field, value)

    pqd.save(update_fields=[field, 'updated_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def ajax_save_pqd_section(request, pk):
    """Save a text section (TinyMCE HTML) to a PQD."""
    pqd = get_object_or_404(PrequalificationDocument, pk=pk)
    user = request.user
    if not getattr(user, 'is_super_admin_user', False):
        return JsonResponse({'error': 'Permission denied — super admin only'}, status=403)

    field = request.POST.get('field', '')
    value = request.POST.get('value', '')

    allowed = [f[0] for f in PrequalificationDocument.TEXT_SECTION_FIELDS]
    if field not in allowed:
        return JsonResponse({'error': 'Invalid field'}, status=400)

    setattr(pqd, field, value)
    pqd.save(update_fields=[field, 'updated_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def ajax_pqd_upload_attachment(request, pk):
    """Upload a file into one of the 4 attachment sections of a PQD."""
    pqd = get_object_or_404(PrequalificationDocument, pk=pk)
    user = request.user
    if not getattr(user, 'is_super_admin_user', False):
        return JsonResponse({'error': 'Permission denied — super admin only'}, status=403)

    section = request.POST.get('section', '')
    if section not in PrequalificationDocument.FILE_SECTION_KEYS:
        return JsonResponse({'error': f'Invalid section: {section!r}'}, status=400)

    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file received by server'}, status=400)

    # Size limit 150 MB (large scanned PDFs for ISO certs etc.)
    if uploaded.size > 150 * 1024 * 1024:
        return JsonResponse({
            'error': f'File too large ({uploaded.size // (1024*1024)}MB). Max is 150MB.'
        }, status=400)

    import os
    ext = os.path.splitext(uploaded.name)[1].lower().lstrip('.')
    allowed_ext = {'pdf', 'doc', 'docx', 'ppt', 'pptx', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
    if ext not in allowed_ext:
        return JsonResponse({'error': f'File type ".{ext}" not allowed. Allowed: PDF, Word, PowerPoint, images.'}, status=400)

    # Next order within section
    last = pqd.attachments.filter(section=section).order_by('-order').values_list('order', flat=True).first() or 0

    att = PQDAttachment.objects.create(
        pqd=pqd,
        section=section,
        file=uploaded,
        original_filename=uploaded.name,
        order=last + 1,
    )
    return JsonResponse({
        'ok': True,
        'id': att.pk,
        'name': att.original_filename,
        'url': att.file.url,
        'section': att.section,
        'is_pdf': att.is_pdf,
        'is_image': att.is_image,
        'is_word': att.is_word,
        'is_powerpoint': att.is_powerpoint,
        'extension': att.extension,
    })


@login_required
def pqd_export(request, pk):
    """Generate and return the merged PQD PDF (super admin only)."""
    pqd = get_object_or_404(PrequalificationDocument, pk=pk)
    user = request.user
    if not getattr(user, 'is_super_admin_user', False):
        return JsonResponse({'error': 'Permission denied — super admin only'}, status=403)

    from .pqd_export import export_pqd_merged_pdf
    try:
        content, filename, content_type = export_pqd_merged_pdf(pqd)
    except RuntimeError as e:
        messages.error(request, str(e))
        return JsonResponse({'error': str(e)}, status=500)

    from django.http import HttpResponse
    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_POST
def ajax_pqd_delete_attachment(request, pk):
    """Delete a PQD attachment (super admin only)."""
    att = get_object_or_404(PQDAttachment, pk=pk)
    user = request.user
    if not getattr(user, 'is_super_admin_user', False):
        return JsonResponse({'error': 'Permission denied — super admin only'}, status=403)

    # Remove the file from disk too
    try:
        att.file.delete(save=False)
    except Exception:
        pass
    att.delete()
    return JsonResponse({'ok': True})
