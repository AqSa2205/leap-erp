from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse, reverse_lazy
from django.db.models import Q, Sum, Count
from django.core.paginator import Paginator
import openpyxl

from .models import Project, Region, ProjectStatus, ProjectHistory, Document, ProjectRevision
from .forms import ProjectForm, ProjectFilterForm, DocumentForm, DocumentFilterForm
from notifications.services import notify_users


class ProjectPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Base mixin for project permissions"""

    def get_queryset(self):
        """Filter queryset based on user role"""
        queryset = Project.objects.select_related('status', 'region', 'owner').all()
        user = self.request.user

        if user.is_super_admin_user:
            return queryset
        # Procurement only kicks in after a project is Won, so they only
        # ever need to see Won-status projects in their region. Admins and
        # managers continue to see every status in their region.
        if getattr(user, 'is_procurement_user', False):
            return queryset.filter(
                region=user.region,
                status__category='won',
            )
        if user.is_admin_user or user.is_manager_user:
            return queryset.filter(region=user.region)
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
        'LNUK': ['UK', 'GLB', 'LNUK'],  # UK and Global together
        'LNA': ['LNA'],
        'PA': ['PA'],
        'NEO-Dubai': ['NEO-Dubai'],
        'NEO-KSA': ['NEO-KSA'],
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
        project = self.object
        context['history'] = project.history.select_related(
            'old_status', 'new_status', 'changed_by'
        ).all()[:10]
        context['revisions'] = project.revisions.select_related('created_by').all()

        # ── Workflow data: everything linked to this proposal_reference ──
        # RFQ documents
        rfq_docs = Document.objects.filter(project=project, document_type='rfq')
        # Costing sheets (BOM and Costing are the same record)
        costing_sheets = list(project.costing_sheets.select_related('created_by').all())
        # Technical proposals (write-ups)
        from proposals.models import TechnicalProposal
        tech_proposals = list(
            TechnicalProposal.objects.filter(project=project).order_by('-updated_at')
        )
        # Vendor quotes + Commercial Proposal PDFs reached via any linked costing sheet
        from costing.models import VendorQuote, CostingSheetRevision
        vendor_quotes = VendorQuote.objects.filter(sheet__in=costing_sheets).count()
        commercial_pdfs = CostingSheetRevision.objects.filter(sheet__in=costing_sheets).count()

        context['workflow'] = {
            'rfq_count':            rfq_docs.count(),
            'rfq_first_url':        rfq_docs.first().file.url if rfq_docs.exists() else None,
            'costing_sheets':       costing_sheets,
            'tech_proposals':       tech_proposals,
            'vendor_quote_count':   vendor_quotes,
            'commercial_pdf_count': commercial_pdfs,
        }

        # ── Timeline: flat, chronologically sorted event stream ─────────
        # Each event: {when, icon, color, title, subtitle, actor, url}
        events = []

        def _actor_name(u):
            return (u.get_full_name() or u.username) if u else ''

        # Project created
        if project.created_at:
            events.append({
                'when':     project.created_at,
                'icon':     'bi-folder-plus',
                'color':    '#1a1a1a',
                'title':    'Pipeline entry created',
                'subtitle': project.project_name or project.proposal_reference,
                'actor':    _actor_name(project.owner),
                'url':      '',
            })

        # RFQ documents uploaded
        for doc in rfq_docs.select_related('uploaded_by'):
            events.append({
                'when':     doc.uploaded_at,
                'icon':     'bi-file-earmark-arrow-up',
                'color':    '#0dcaf0',
                'title':    'Client RFQ uploaded',
                'subtitle': doc.name,
                'actor':    _actor_name(doc.uploaded_by),
                'url':      reverse_lazy('projects:document_detail', kwargs={'pk': doc.pk}),
            })

        # Costing sheet lifecycle
        for sheet in costing_sheets:
            if sheet.created_at:
                events.append({
                    'when':     sheet.created_at,
                    'icon':     'bi-list-check',
                    'color':    '#ffc107',
                    'title':    'BOM started',
                    'subtitle': sheet.title,
                    'actor':    _actor_name(sheet.created_by),
                    'url':      reverse_lazy('costing:detail', kwargs={'pk': sheet.pk}) + '?view=bom',
                })
            if getattr(sheet, 'handed_over_at', None):
                events.append({
                    'when':     sheet.handed_over_at,
                    'icon':     'bi-arrow-right-square',
                    'color':    '#0d6efd',
                    'title':    'BOM handed over to sales',
                    'subtitle': sheet.title,
                    'actor':    _actor_name(getattr(sheet, 'handed_over_by', None)),
                    'url':      reverse_lazy('costing:detail', kwargs={'pk': sheet.pk}),
                })
            if getattr(sheet, 'costing_started_at', None):
                events.append({
                    'when':     sheet.costing_started_at,
                    'icon':     'bi-cash-coin',
                    'color':    '#198754',
                    'title':    'Costing in progress',
                    'subtitle': sheet.title,
                    'actor':    _actor_name(getattr(sheet, 'costing_started_by', None)),
                    'url':      reverse_lazy('costing:detail', kwargs={'pk': sheet.pk}),
                })
            if getattr(sheet, 'finalized_at', None):
                events.append({
                    'when':     sheet.finalized_at,
                    'icon':     'bi-check2-circle',
                    'color':    '#212529',
                    'title':    'Costing finalized',
                    'subtitle': f'{sheet.title} · Grand total {sheet.grand_total:.2f} {sheet.output_currency}',
                    'actor':    _actor_name(getattr(sheet, 'finalized_by', None)),
                    'url':      reverse_lazy('costing:detail', kwargs={'pk': sheet.pk}),
                })

        # Commercial Proposal PDF exports
        from costing.models import CostingSheetRevision as _Rev
        for rev in _Rev.objects.filter(sheet__in=costing_sheets, export_format='pdf').select_related('created_by', 'sheet'):
            events.append({
                'when':     rev.created_at,
                'icon':     'bi-file-earmark-pdf',
                'color':    '#C41E3A',
                'title':    f'Commercial Proposal {rev.revision_label} exported',
                'subtitle': rev.change_summary or rev.sheet.title,
                'actor':    _actor_name(rev.created_by),
                'url':      rev.file.url if rev.file else reverse_lazy('costing:detail', kwargs={'pk': rev.sheet_id}),
            })

        # Vendor quotes uploaded
        from costing.models import VendorQuote as _VQ
        for vq in _VQ.objects.filter(sheet__in=costing_sheets).select_related('uploaded_by', 'sheet'):
            events.append({
                'when':     vq.uploaded_at,
                'icon':     'bi-receipt',
                'color':    '#6f42c1',
                'title':    f'Vendor quote from {vq.vendor_name}',
                'subtitle': f'{vq.quote_reference or "no ref"} · {vq.sheet.title}',
                'actor':    _actor_name(vq.uploaded_by),
                'url':      reverse_lazy('costing:detail', kwargs={'pk': vq.sheet_id}),
            })

        # Technical proposals
        for tp in tech_proposals:
            if tp.created_at:
                events.append({
                    'when':     tp.created_at,
                    'icon':     'bi-file-earmark-richtext',
                    'color':    '#0dcaf0',
                    'title':    'Technical Proposal created',
                    'subtitle': tp.title,
                    'actor':    _actor_name(tp.created_by),
                    'url':      reverse_lazy('proposals:detail', kwargs={'pk': tp.pk}),
                })

        # Pipeline-entry revisions
        for rev in project.revisions.select_related('created_by'):
            events.append({
                'when':     rev.created_at,
                'icon':     'bi-bookmark-star',
                'color':    '#fd7e14',
                'title':    f'Revision {rev.revision_label} saved',
                'subtitle': rev.notes or 'Snapshot of pipeline state',
                'actor':    _actor_name(rev.created_by),
                'url':      reverse_lazy('projects:revision_detail', kwargs={'pk': project.pk, 'revision_pk': rev.pk}),
            })

        # Sort newest first
        events.sort(key=lambda e: e['when'] or project.created_at, reverse=True)
        context['timeline'] = events
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
        response = super().form_valid(form)

        # Notify region managers about new project
        from accounts.models import User, Role
        project = self.object
        region_managers = User.objects.filter(
            role__name=Role.MANAGER, region=project.region, is_active=True
        )
        notify_users(
            recipients=region_managers,
            verb='created a new project',
            actor=self.request.user,
            target=project,
            target_url=reverse_lazy('projects:detail', kwargs={'pk': project.pk}),
            description=f'New project: {project.project_name}',
            level='info',
        )
        return response


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
        old_owner = Project.objects.get(pk=self.object.pk).owner

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
        response = super().form_valid(form)

        # Notify on owner change
        new_owner = self.object.owner
        if 'owner' in form.changed_data and old_owner != new_owner:
            target_url = str(reverse_lazy('projects:detail', kwargs={'pk': self.object.pk}))
            recipients = set()
            if old_owner:
                recipients.add(old_owner)
            if new_owner:
                recipients.add(new_owner)
            notify_users(
                recipients=recipients,
                verb=f'changed project owner for "{self.object.project_name}"',
                actor=self.request.user,
                target=self.object,
                target_url=target_url,
                description=f'Owner changed from {old_owner or "None"} to {new_owner or "None"}',
                level='warning',
                send_email=True,
            )

        return response

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
        return self.request.user.is_super_admin_user or self.request.user.is_admin_user or self.request.user.is_manager_user

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
                'epc': 'customer',
                'customer': 'customer',
                'end user': 'end_user',
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

                    # Customer (was EPC)
                    customer_val = get_val('customer')
                    if customer_val:
                        defaults['customer'] = str(customer_val).strip()

                    # End User
                    end_user_val = get_val('end_user')
                    if end_user_val:
                        defaults['end_user'] = str(end_user_val).strip()

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
                # Notify importer on completion
                from notifications.services import create_notification
                create_notification(
                    recipient=request.user,
                    verb=f'Excel import completed: {imported_count} project(s)',
                    target_url=str(reverse_lazy('projects:list')),
                    description=f'Successfully imported {imported_count} project(s) into region {region.name}.',
                    level='success',
                )
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
def _documents_visible_to(user):
    """Scoped Document queryset, mirroring ProjectPermissionMixin.

    - Super admin: every document.
    - Admin/manager: documents on projects in their region, plus any
      standalone (project=None) documents they uploaded themselves.
    - Anyone else: documents on projects they own, plus their own uploads
      (standalone or otherwise).
    """
    qs = Document.objects.select_related('project', 'project__region', 'uploaded_by')
    if user.is_super_admin_user:
        return qs
    if user.is_admin_user or user.is_manager_user:
        return qs.filter(
            Q(project__region=user.region) |
            Q(project__isnull=True, uploaded_by=user)
        ).distinct()
    return qs.filter(
        Q(project__owner=user) |
        Q(uploaded_by=user)
    ).distinct()


class DocumentListView(LoginRequiredMixin, ListView):
    """List all documents with filtering"""
    model = Document
    template_name = 'projects/document_list.html'
    context_object_name = 'documents'
    paginate_by = 25

    def get_queryset(self):
        queryset = _documents_visible_to(self.request.user)

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

        # Group by document type for summary, scoped to what the user can see
        scoped = _documents_visible_to(self.request.user)
        type_counts = scoped.values('document_type').annotate(count=Count('id'))
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

    def get_queryset(self):
        return _documents_visible_to(self.request.user)


class DocumentUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """Edit document metadata (and optionally replace the file)."""
    model = Document
    form_class = DocumentForm
    template_name = 'projects/document_form.html'

    def get_queryset(self):
        return _documents_visible_to(self.request.user)

    def test_func(self):
        document = self.get_object()
        user = self.request.user
        return (
            user.is_super_admin_user
            or user.is_admin_user
            or document.uploaded_by == user
        )

    def get_success_url(self):
        return reverse('projects:document_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        # If the user replaced the file, delete the previous R2 object so we
        # don't accumulate orphans on every edit.
        old_file = None
        if 'file' in form.changed_data and self.object.pk:
            old_file = Document.objects.get(pk=self.object.pk).file
        response = super().form_valid(form)
        if old_file and old_file.name and old_file.name != self.object.file.name:
            old_file.storage.delete(old_file.name)
        messages.success(self.request, 'Document updated successfully.')
        return response


class DocumentDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    """Delete a document"""
    model = Document
    template_name = 'projects/document_confirm_delete.html'
    success_url = reverse_lazy('projects:document_list')

    def test_func(self):
        document = self.get_object()
        user = self.request.user
        # Allow deletion if user is admin or uploaded the document
        return user.is_super_admin_user or user.is_admin_user or document.uploaded_by == user

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


# ─── Commercial Pipeline Revisions ───────────────────────────

def _build_project_snapshot(project):
    """Return a dict capturing the Project's state at this moment.

    Foreign keys are expanded to name/code so the snapshot remains
    readable even if related rows are later renamed or deleted.
    """
    def _dec(v):
        return None if v is None else str(v)

    return {
        'snapshot_version': 1,
        'identity': {
            'serial_number': project.serial_number,
            'project_name': project.project_name,
            'proposal_reference': project.proposal_reference,
            'client_rfq_reference': project.client_rfq_reference,
            'po_number': project.po_number,
        },
        'parties': {
            'customer': project.customer,
            'end_user': project.end_user,
            'owner': (project.owner.get_full_name() or project.owner.username) if project.owner else None,
            'owner_username': project.owner.username if project.owner else None,
            'contact_with': project.contact_with,
        },
        'classification': {
            'project_stage': project.project_stage,
            'project_stage_display': project.get_project_stage_display() if project.project_stage else None,
            'region': {
                'code': project.region.code if project.region else None,
                'name': project.region.name if project.region else None,
                'currency': project.region.currency if project.region else None,
            },
            'status': {
                'name': project.status.name if project.status else None,
                'category': project.status.category if project.status else None,
                'color': project.status.color if project.status else None,
            },
        },
        'dates': {
            'submission_deadline': project.submission_deadline.isoformat() if project.submission_deadline else None,
            'estimated_po_date': project.estimated_po_date.isoformat() if project.estimated_po_date else None,
            'year': project.year,
            'po_award_quarter': project.po_award_quarter,
        },
        'financials': {
            'estimated_value': _dec(project.estimated_value),
            'estimated_value_usd': _dec(project.estimated_value_usd),
            'estimated_value_per_annum': _dec(project.estimated_value_per_annum),
            'estimated_gp': _dec(project.estimated_gp),
            'success_quotient': _dec(project.success_quotient),
            'minimum_achievement': _dec(project.minimum_achievement),
            'actual_sales': _dec(project.actual_sales),
        },
        'narrative': {
            'remarks': project.remarks,
            'notes': project.notes,
            'portal_url': project.portal_url,
        },
        'metadata': {
            'created_at': project.created_at.isoformat() if project.created_at else None,
            'updated_at': project.updated_at.isoformat() if project.updated_at else None,
            'created_by': (project.created_by.get_full_name() or project.created_by.username) if project.created_by else None,
        },
    }


class ProjectRevisionCreateView(ProjectPermissionMixin, View):
    """POST: create a new revision of the current project state."""

    def test_func(self):
        return True

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        label = ProjectRevision.next_label_for(project)
        notes = (request.POST.get('notes') or '').strip()
        ProjectRevision.objects.create(
            project=project,
            revision_label=label,
            snapshot=_build_project_snapshot(project),
            notes=notes,
            created_by=request.user,
        )
        messages.success(request, f'Revision {label} saved.')
        return redirect('projects:detail', pk=project.pk)


class ProjectRevisionDetailView(ProjectPermissionMixin, View):
    """GET: render a past revision as a read-only page."""

    def test_func(self):
        return True

    def get(self, request, pk, revision_pk):
        project = get_object_or_404(Project, pk=pk)
        revision = get_object_or_404(ProjectRevision, pk=revision_pk, project=project)
        return render(request, 'projects/project_revision_detail.html', {
            'project': project,
            'revision': revision,
            'snapshot': revision.snapshot,
        })


# ─── Pipeline Print to PDF ───────────────────────────────────
def _apply_pipeline_filters(request, queryset):
    """Apply the same filter parameters ProjectListView uses.

    Kept here so the print view renders exactly what the user sees on
    screen after clicking Filter, without rebuilding the logic.
    """
    region_map = {
        'LNUK':      ['UK', 'GLB', 'LNUK'],
        'LNA':       ['LNA'],
        'PA':        ['PA'],
        'NEO-Dubai': ['NEO-Dubai'],
        'NEO-KSA':   ['NEO-KSA'],
    }
    g = request.GET
    search   = g.get('search')
    region   = g.get('region')
    year     = g.get('year')
    status   = g.get('status')
    category = g.get('category')
    quarter  = g.get('quarter')
    owner    = g.get('owner')
    if search:
        queryset = queryset.filter(
            Q(project_name__icontains=search) |
            Q(proposal_reference__icontains=search) |
            Q(client_rfq_reference__icontains=search)
        )
    if region:
        codes = region_map.get(region, [])
        if codes:
            queryset = queryset.filter(region__code__in=codes)
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


@login_required
def pipeline_print_pdf(request):
    """Render the currently-filtered Commercial Pipeline list as PDF.

    Honors the same ?search=/region/status/category/owner/year/quarter
    querystring as the on-screen list view so users can preview in the
    browser then "Print PDF" and get the exact same rows.
    """
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from django.http import HttpResponse
    from django.utils import timezone

    # Permission scoping — mirror ProjectPermissionMixin.get_queryset
    user = request.user
    qs = Project.objects.select_related('status', 'region', 'owner').all()
    if not user.is_super_admin_user:
        if user.is_admin_user or user.is_manager_user:
            qs = qs.filter(region=user.region)
        else:
            qs = qs.filter(owner=user)
    qs = _apply_pipeline_filters(request, qs)

    total_value = sum((p.estimated_value or Decimal('0')) for p in qs)

    # Filter summary line for the PDF header
    summary_parts = []
    for key in ('search', 'region', 'year', 'status', 'category', 'quarter', 'owner'):
        val = request.GET.get(key)
        if val:
            summary_parts.append(f'{key}={val}')
    filter_summary = ' · '.join(summary_parts) if summary_parts else 'All entries (no filter)'

    # Build PDF
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=10*mm, rightMargin=10*mm,
        topMargin=12*mm, bottomMargin=12*mm,
        title='Commercial Pipeline',
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=16, alignment=TA_LEFT, textColor=colors.HexColor('#1a1a1a'))
    meta = ParagraphStyle('meta', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#6c757d'))
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, leading=10)

    elements = []
    elements.append(Paragraph('Commercial Pipeline', h1))
    elements.append(Paragraph(
        f'Generated {timezone.localtime().strftime("%d %b %Y %H:%M")} · {qs.count()} entries · Filter: {filter_summary}',
        meta,
    ))
    elements.append(Spacer(1, 6))

    # Paragraph parses input as mini-XML, so any '<' or '&' in user-typed
    # fields would crash the parser ("unclosed tags"). Escape first.
    from xml.sax.saxutils import escape as _xml_escape
    def _safe(text, limit=None):
        s = '' if text is None else str(text)
        if limit:
            s = s[:limit]
        return _xml_escape(s) or '-'

    header = ['Reference', 'Project', 'Client', 'Region', 'Status', 'Value', 'Owner', 'Updated']
    data = [header]
    for p in qs:
        owner_name = (p.owner.get_full_name() or p.owner.username) if p.owner_id else '-'
        data.append([
            Paragraph(_safe(p.proposal_reference), small),
            Paragraph(_safe(p.project_name, 80), small),
            Paragraph(_safe(p.customer, 40), small),
            _safe(p.region.code) if p.region_id else '-',
            _safe(p.status.name) if p.status_id else '-',
            f'{(p.estimated_value or 0):,.0f}',
            _safe(owner_name),
            p.updated_at.strftime('%d %b %Y') if p.updated_at else '-',
        ])
    data.append([
        Paragraph('<b>TOTAL</b>', small), '', '', '', '',
        f'{total_value:,.0f}', '', '',
    ])

    tbl = Table(
        data,
        colWidths=[28*mm, 70*mm, 45*mm, 18*mm, 28*mm, 24*mm, 32*mm, 22*mm],
        repeatRows=1,
    )
    tbl.setStyle(TableStyle([
        ('BACKGROUND',  (0, 0), (-1, 0), colors.HexColor('#1a1a1a')),
        ('TEXTCOLOR',   (0, 0), (-1, 0), colors.white),
        ('FONTNAME',    (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',    (0, 0), (-1, 0), 8),
        ('ALIGN',       (0, 0), (-1, 0), 'CENTER'),
        ('ALIGN',       (5, 1), (5, -1), 'RIGHT'),
        ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE',    (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
        ('BACKGROUND',  (0, -1), (-1, -1), colors.HexColor('#C41E3A')),
        ('TEXTCOLOR',   (0, -1), (-1, -1), colors.white),
        ('FONTNAME',    (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID',        (0, 0), (-1, -1), 0.3, colors.HexColor('#dee2e6')),
    ]))
    elements.append(tbl)

    doc.build(elements)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    stamp = timezone.localtime().strftime('%Y%m%d_%H%M')
    resp['Content-Disposition'] = f'inline; filename="commercial_pipeline_{stamp}.pdf"'
    return resp
