from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Q, Sum, Count
from django.core.paginator import Paginator

from .models import Project, Region, ProjectStatus, ProjectHistory, Document
from .forms import ProjectForm, ProjectFilterForm, DocumentForm, DocumentFilterForm


class ProjectPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Base mixin for project permissions"""

    def get_queryset(self):
        """Filter queryset based on user role"""
        queryset = Project.objects.select_related('status', 'region', 'owner').all()
        user = self.request.user

        if user.is_admin_user:
            return queryset
        elif user.is_manager_user:
            return queryset.filter(region=user.region)
        else:
            return queryset.filter(owner=user)


class ProjectListView(ProjectPermissionMixin, ListView):
    """List all projects with filtering"""
    model = Project
    template_name = 'projects/project_list.html'
    context_object_name = 'projects'
    paginate_by = 25

    def test_func(self):
        return True  # All authenticated users can view

    # Mapping of consolidated regions to database region codes
    REGION_CODE_MAP = {
        'LNUK': ['UK', 'GLB'],  # UK and Global together
        'LNA': ['LNA'],
        'PA': ['PA'],
    }

    def get_queryset(self):
        queryset = super().get_queryset()

        # Apply filters
        search = self.request.GET.get('search')
        region = self.request.GET.get('region')
        year = self.request.GET.get('year')
        status = self.request.GET.get('status')
        category = self.request.GET.get('category')
        quarter = self.request.GET.get('quarter')

        if search:
            queryset = queryset.filter(
                Q(project_name__icontains=search) |
                Q(proposal_reference__icontains=search) |
                Q(client_rfq_reference__icontains=search)
            )
        if region:
            # Use consolidated region mapping
            region_codes = self.REGION_CODE_MAP.get(region, [])
            if region_codes:
                queryset = queryset.filter(region__code__in=region_codes)
        if year:
            queryset = queryset.filter(year=year)
        if status:
            queryset = queryset.filter(status_id=status)
        if category:
            queryset = queryset.filter(status__category=category)
        if quarter:
            queryset = queryset.filter(po_award_quarter=quarter)

        return queryset.order_by('-updated_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = ProjectFilterForm(self.request.GET)

        # Add summary stats
        queryset = self.get_queryset()
        context['total_count'] = queryset.count()
        context['total_value'] = queryset.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0

        return context


class ProjectDetailView(ProjectPermissionMixin, DetailView):
    """View project details"""
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['history'] = self.object.history.select_related(
            'old_status', 'new_status', 'changed_by'
        ).all()[:10]
        return context


class ProjectCreateView(LoginRequiredMixin, CreateView):
    """Create a new project"""
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        if not form.instance.owner:
            form.instance.owner = self.request.user
        messages.success(self.request, 'Project created successfully.')
        return super().form_valid(form)


class ProjectUpdateView(ProjectPermissionMixin, UpdateView):
    """Update a project"""
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'

    def test_func(self):
        project = self.get_object()
        return self.request.user.can_edit_project(project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        # Track status change
        if 'status' in form.changed_data:
            old_status = Project.objects.get(pk=self.object.pk).status
            ProjectHistory.objects.create(
                project=self.object,
                old_status=old_status,
                new_status=form.cleaned_data['status'],
                changed_by=self.request.user
            )

        messages.success(self.request, 'Project updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:detail', kwargs={'pk': self.object.pk})


class ProjectDeleteView(ProjectPermissionMixin, DeleteView):
    """Delete a project (admin only)"""
    model = Project
    template_name = 'projects/project_confirm_delete.html'
    success_url = reverse_lazy('projects:list')

    def test_func(self):
        return self.request.user.can_delete_project()

    def form_valid(self, form):
        messages.success(self.request, 'Project deleted successfully.')
        return super().form_valid(form)


# Document Views
class DocumentListView(LoginRequiredMixin, ListView):
    """List all documents with filtering"""
    model = Document
    template_name = 'projects/document_list.html'
    context_object_name = 'documents'
    paginate_by = 25

    def get_queryset(self):
        queryset = Document.objects.select_related('project', 'uploaded_by').all()

        # Apply filters
        search = self.request.GET.get('search')
        document_type = self.request.GET.get('document_type')
        project = self.request.GET.get('project')

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(reference_number__icontains=search) |
                Q(vendor_name__icontains=search) |
                Q(description__icontains=search)
            )
        if document_type:
            queryset = queryset.filter(document_type=document_type)
        if project:
            queryset = queryset.filter(project_id=project)

        return queryset.order_by('-uploaded_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = DocumentFilterForm(self.request.GET)
        context['total_count'] = self.get_queryset().count()

        # Group by document type for summary
        type_counts = Document.objects.values('document_type').annotate(count=Count('id'))
        context['type_counts'] = {t['document_type']: t['count'] for t in type_counts}

        return context


class DocumentCreateView(LoginRequiredMixin, CreateView):
    """Upload a new document"""
    model = Document
    form_class = DocumentForm
    template_name = 'projects/document_form.html'
    success_url = reverse_lazy('projects:document_list')

    def get_initial(self):
        initial = super().get_initial()
        # Pre-fill project if passed in URL
        project_id = self.request.GET.get('project')
        if project_id:
            initial['project'] = project_id
        return initial

    def form_valid(self, form):
        form.instance.uploaded_by = self.request.user
        messages.success(self.request, 'Document uploaded successfully.')
        response = super().form_valid(form)

        # Redirect back to project detail if project was specified
        project_id = self.request.GET.get('project')
        if project_id:
            return redirect('projects:detail', pk=project_id)
        return response


class DocumentDetailView(LoginRequiredMixin, DetailView):
    """View document details"""
    model = Document
    template_name = 'projects/document_detail.html'
    context_object_name = 'document'


class DocumentDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    """Delete a document"""
    model = Document
    template_name = 'projects/document_confirm_delete.html'
    success_url = reverse_lazy('projects:document_list')

    def test_func(self):
        document = self.get_object()
        user = self.request.user
        # Allow deletion if user is admin or uploaded the document
        return user.is_admin_user or document.uploaded_by == user

    def form_valid(self, form):
        document = self.get_object()
        # Delete the file from storage
        if document.file:
            document.file.delete(save=False)
        messages.success(self.request, 'Document deleted successfully.')
        return super().form_valid(form)


@login_required
def add_project_document(request, pk):
    """Add document to a specific project"""
    project = get_object_or_404(Project, pk=pk)

    if request.method == 'POST':
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.project = project
            document.uploaded_by = request.user
            document.save()
            messages.success(request, 'Document uploaded successfully.')
            return redirect('projects:detail', pk=pk)
    else:
        form = DocumentForm(initial={'project': project})

    return render(request, 'projects/document_form.html', {
        'form': form,
        'project': project,
        'title': f'Add Document to {project.project_name}'
    })
