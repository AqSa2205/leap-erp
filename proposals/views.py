from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Q

from .models import TechnicalProposal, ProposalBoilerplate
from .forms import (
    ProposalMetadataForm, ProposalContentForm, EngineeringDocumentFormSet,
    ProposalFilterForm, ProposalBoilerplateForm,
)


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

class ProposalEditContentView(ProposalPermissionMixin, UpdateView):
    model = TechnicalProposal
    form_class = ProposalContentForm
    template_name = 'proposals/proposal_edit_content.html'

    def test_func(self):
        obj = self.get_object()
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user:
            return True
        return obj.created_by == user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['eng_formset'] = EngineeringDocumentFormSet(
                self.request.POST, instance=self.object, prefix='eng'
            )
        else:
            context['eng_formset'] = EngineeringDocumentFormSet(
                instance=self.object, prefix='eng'
            )
        context['section_fields'] = TechnicalProposal.SECTION_FIELDS
        context['boilerplates'] = ProposalBoilerplate.objects.all()
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        eng_formset = context['eng_formset']
        if eng_formset.is_valid():
            self.object = form.save()
            eng_formset.instance = self.object
            eng_formset.save()
            messages.success(self.request, 'Proposal content saved.')
            return super().form_valid(form)
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse('proposals:detail', kwargs={'pk': self.object.pk})


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
