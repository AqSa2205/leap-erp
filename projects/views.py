from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.db.models import Q, Sum, Count
from django.core.paginator import Paginator
import openpyxl

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
        owner = self.request.GET.get('owner')

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
        if owner:
            queryset = queryset.filter(owner_id=owner)

        return queryset.order_by('-updated_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = ProjectFilterForm(self.request.GET, user=self.request.user)

        # Add summary stats
        queryset = self.get_queryset()
        context['total_count'] = queryset.count()
        context['total_value'] = queryset.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0

        # Regions for import modal
        context['regions'] = Region.objects.filter(is_active=True)

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
        return True  # All authenticated users can edit

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


class ProjectBulkDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Delete all projects matching the current filters (admin only)"""

    REGION_CODE_MAP = ProjectListView.REGION_CODE_MAP

    def test_func(self):
        return self.request.user.can_delete_project()

    def post(self, request):
        queryset = Project.objects.select_related('status', 'region', 'owner').all()

        # Apply the same filters as ProjectListView
        search = request.POST.get('search')
        region = request.POST.get('region')
        year = request.POST.get('year')
        status = request.POST.get('status')
        category = request.POST.get('category')
        quarter = request.POST.get('quarter')
        owner = request.POST.get('owner')

        if search:
            queryset = queryset.filter(
                Q(project_name__icontains=search) |
                Q(proposal_reference__icontains=search) |
                Q(client_rfq_reference__icontains=search)
            )
        if region:
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
        if owner:
            queryset = queryset.filter(owner_id=owner)

        count = queryset.count()
        queryset.delete()
        messages.success(request, f'{count} project(s) deleted successfully.')
        return redirect('projects:list')


class ProjectImportView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Import projects from an Excel file"""

    def test_func(self):
        return self.request.user.is_admin_user or self.request.user.is_manager_user

    def post(self, request):
        excel_file = request.FILES.get('excel_file')
        region_id = request.POST.get('region')

        if not excel_file:
            messages.error(request, 'Please select an Excel file to import.')
            return redirect('projects:list')

        if not region_id:
            messages.error(request, 'Please select a region.')
            return redirect('projects:list')

        try:
            region = Region.objects.get(pk=region_id)
        except Region.DoesNotExist:
            messages.error(request, 'Invalid region selected.')
            return redirect('projects:list')

        try:
            wb = openpyxl.load_workbook(excel_file, read_only=True, data_only=True)
            ws = wb.active

            # Auto-detect header row by scanning for known column names
            header_row = None
            header_map = {}
            for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=False), start=1):
                for cell in row:
                    val = str(cell.value).strip() if cell.value else ''
                    if val.lower() in ('leap proposal reference', 'project name'):
                        header_row = row_idx
                        break
                if header_row:
                    break

            if not header_row:
                messages.error(request, 'Could not find header row. Ensure the Excel file has a "Leap Proposal Reference" or "Project Name" column.')
                return redirect('projects:list')

            # Build column index mapping from header row
            COLUMN_MAP = {
                'project name': 'project_name',
                'leap proposal reference': 'proposal_reference',
                'client rfq ref number': 'client_rfq_reference',
                'submission date': 'submission_deadline',
                'owner': 'owner',
                'epc': 'epc',
                'bid status': 'status',
                'est. value (sar)': 'estimated_value',
                'est. value ($usd)': 'estimated_value_usd',
                'est. value (sar) - per annum': 'estimated_value_per_annum',
                'est. gp': 'estimated_gp',
                'po award - q': 'po_award_quarter',
                'success quotient': 'success_quotient',
                'minimum achievement': 'minimum_achievement',
                'contact with': 'contact_with',
                'remarks': 'remarks',
            }

            for cell in list(ws.iter_rows(min_row=header_row, max_row=header_row, values_only=False))[0]:
                col_name = str(cell.value).strip().lower() if cell.value else ''
                if col_name in COLUMN_MAP:
                    header_map[COLUMN_MAP[col_name]] = cell.column - 1  # 0-based index

            if 'proposal_reference' not in header_map:
                messages.error(request, 'Excel file must contain a "Leap Proposal Reference" column.')
                return redirect('projects:list')

            # Cache lookups
            from accounts.models import User
            users = {u.get_full_name().lower(): u for u in User.objects.filter(is_active=True) if u.get_full_name()}
            users.update({u.username.lower(): u for u in User.objects.filter(is_active=True)})
            statuses = {s.name.lower(): s for s in ProjectStatus.objects.filter(is_active=True)}

            # Get a default status for new projects
            default_status = ProjectStatus.objects.filter(is_active=True).first()

            imported_count = 0
            errors = []
            row_num = header_row  # for error reporting

            for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
                row_num += 1

                # Get values by column index
                def get_val(field_name):
                    idx = header_map.get(field_name)
                    if idx is not None and idx < len(row):
                        return row[idx]
                    return None

                proposal_ref = get_val('proposal_reference')
                if not proposal_ref:
                    continue  # Skip rows without proposal reference
                proposal_ref = str(proposal_ref).strip()
                if not proposal_ref:
                    continue

                try:
                    defaults = {'region': region}

                    # Project name
                    project_name = get_val('project_name')
                    if project_name:
                        defaults['project_name'] = str(project_name).strip()
                    else:
                        defaults['project_name'] = proposal_ref

                    # Client RFQ Reference
                    client_rfq = get_val('client_rfq_reference')
                    if client_rfq:
                        defaults['client_rfq_reference'] = str(client_rfq).strip()

                    # Submission Date
                    sub_date = get_val('submission_deadline')
                    if sub_date:
                        import datetime
                        if isinstance(sub_date, datetime.datetime):
                            defaults['submission_deadline'] = sub_date.date()
                        elif isinstance(sub_date, datetime.date):
                            defaults['submission_deadline'] = sub_date
                        else:
                            try:
                                from django.utils.dateparse import parse_date
                                parsed = parse_date(str(sub_date).strip())
                                if parsed:
                                    defaults['submission_deadline'] = parsed
                            except (ValueError, TypeError):
                                pass

                    # Owner
                    owner_val = get_val('owner')
                    if owner_val:
                        owner_key = str(owner_val).strip().lower()
                        matched_user = users.get(owner_key)
                        if matched_user:
                            defaults['owner'] = matched_user

                    # EPC
                    epc_val = get_val('epc')
                    if epc_val:
                        defaults['epc'] = str(epc_val).strip()

                    # Bid Status
                    status_val = get_val('status')
                    if status_val:
                        status_key = str(status_val).strip().lower()
                        matched_status = statuses.get(status_key)
                        if matched_status:
                            defaults['status'] = matched_status
                        elif default_status:
                            defaults['status'] = default_status
                    elif default_status:
                        defaults['status'] = default_status

                    # Decimal fields
                    decimal_fields = [
                        'estimated_value', 'estimated_value_usd',
                        'estimated_value_per_annum', 'estimated_gp',
                        'success_quotient', 'minimum_achievement',
                    ]
                    for field_name in decimal_fields:
                        val = get_val(field_name)
                        if val is not None:
                            try:
                                defaults[field_name] = Decimal(str(val).strip().replace(',', ''))
                            except (InvalidOperation, ValueError):
                                pass

                    # PO Award Quarter
                    po_q = get_val('po_award_quarter')
                    if po_q:
                        po_q_str = str(po_q).strip().upper()
                        if po_q_str in ('Q1', 'Q2', 'Q3', 'Q4'):
                            defaults['po_award_quarter'] = po_q_str

                    # Contact With
                    contact = get_val('contact_with')
                    if contact:
                        defaults['contact_with'] = str(contact).strip()

                    # Remarks
                    remarks = get_val('remarks')
                    if remarks:
                        defaults['remarks'] = str(remarks).strip()

                    Project.objects.update_or_create(
                        proposal_reference=proposal_ref,
                        defaults=defaults,
                    )
                    imported_count += 1

                except Exception as e:
                    errors.append(f'Row {row_num}: {str(e)}')

            wb.close()

            if imported_count > 0:
                messages.success(request, f'Successfully imported {imported_count} project(s).')
            if errors:
                error_summary = '; '.join(errors[:5])
                if len(errors) > 5:
                    error_summary += f' ... and {len(errors) - 5} more errors'
                messages.warning(request, f'Import completed with errors: {error_summary}')
            if imported_count == 0 and not errors:
                messages.warning(request, 'No projects were found in the Excel file.')

        except Exception as e:
            messages.error(request, f'Error processing Excel file: {str(e)}')

        return redirect('projects:list')


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
