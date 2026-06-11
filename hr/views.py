import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.db.models import Q, Count, Sum
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import PermissionDenied
from datetime import datetime, date, timedelta
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

from .models import Employee, Asset, AssetAssignment, Vehicle, EmployeeDocument, LeaveType, Holiday, LeaveEntitlement, LeaveRecord, AttendanceRecord, AttendanceSettings
from .forms import (
    EmployeeForm, EmployeeFilterForm, EmployeeImportForm,
    AssetForm, AssetFilterForm, AssetImportForm, AssetIssueForm, AssetReturnForm,
    VehicleForm, VehicleFilterForm, EmployeeDocumentForm,
    LeaveTypeForm, HolidayForm, LeaveRecordForm,
    AttendanceSettingsForm,
)
from .leave_services import generate_year_entitlements
from .attendance_services import derive_status
from .attendance_matrix import period_range, build_matrix


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_super_admin_user or self.request.user.is_admin_user


@login_required
def hr_dashboard(request):
    """Comprehensive HR Admin Dashboard with employees, assets, vehicles, and assignments."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('dashboard:index')

    employees = Employee.objects.all()
    assets = Asset.objects.all()
    vehicles = Vehicle.objects.all()

    # Employee stats
    total_employees = employees.count()
    active_employees = employees.filter(is_active=True).count()
    contract_breakdown = {}
    for ct in ['permanent', 'yearly', 'ajeer']:
        contract_breakdown[ct] = employees.filter(contract_type=ct).count()

    # Top nationalities
    nationalities = employees.values('nationality').annotate(
        count=Count('id')
    ).order_by('-count')[:6]

    # Top deployments
    deployments = employees.values('deployment').annotate(
        count=Count('id')
    ).exclude(deployment='').order_by('-count')[:6]

    # Asset stats
    total_assets = assets.count()
    assets_in_stock = assets.filter(in_stock=True).count()
    assets_assigned = assets.filter(in_stock=False).count()
    total_asset_value = assets.aggregate(val=Sum('price'))['val'] or 0

    # Vehicle stats
    total_vehicles = vehicles.count()
    valid_vehicles = vehicles.filter(vehicle_status='valid').count()
    compliance_issues = sum(1 for v in vehicles if v.has_compliance_issue)

    # Vehicle makers breakdown
    makers = vehicles.values('vehicle_maker').annotate(
        count=Count('id')
    ).order_by('-count')[:6]

    # Employee-Asset-Vehicle assignments
    assignments = []
    unassigned_employees = []
    for emp in employees.filter(is_active=True).order_by('full_name'):
        emp_assets = assets.filter(employee_name__icontains=emp.full_name.split()[0]) if emp.full_name else assets.none()
        emp_vehicles = vehicles.filter(driver_name__icontains=emp.full_name.split()[0]) if emp.full_name else vehicles.none()
        if emp_assets.exists() or emp_vehicles.exists():
            assignments.append({
                'employee': emp,
                'assets': list(emp_assets),
                'vehicles': list(emp_vehicles),
            })
        else:
            unassigned_employees.append(emp)

    # Unassigned assets & vehicles
    unassigned_assets = assets.filter(in_stock=True)
    unassigned_vehicles = vehicles.filter(Q(driver_name='') | Q(driver_name='-') | Q(driver_name__isnull=True))

    # Recent employees
    recent_employees = employees.order_by('-created_at')[:5]

    # Asset types breakdown
    asset_types = assets.values('asset_type').annotate(count=Count('id')).exclude(asset_type='').order_by('-count')[:8]

    # JSON data for Chart.js
    nationality_labels = [n['nationality'] or 'Unknown' for n in nationalities]
    nationality_data = [n['count'] for n in nationalities]
    maker_labels = [m['vehicle_maker'] for m in makers]
    maker_data = [m['count'] for m in makers]
    asset_type_labels = [a['asset_type'] for a in asset_types]
    asset_type_data = [a['count'] for a in asset_types]

    context = {
        'total_employees': total_employees,
        'active_employees': active_employees,
        'inactive_employees': total_employees - active_employees,
        'contract_breakdown': contract_breakdown,
        'nationalities': nationalities,
        'deployments': deployments,
        'total_assets': total_assets,
        'assets_in_stock': assets_in_stock,
        'assets_assigned': assets_assigned,
        'total_asset_value': total_asset_value,
        'total_vehicles': total_vehicles,
        'valid_vehicles': valid_vehicles,
        'compliance_issues': compliance_issues,
        'makers': makers,
        'assignments': assignments,
        'unassigned_employees': unassigned_employees[:20],
        'unassigned_assets': unassigned_assets,
        'unassigned_vehicles': unassigned_vehicles,
        'recent_employees': recent_employees,
        'asset_types': asset_types,
        # Chart.js JSON
        'nationality_labels': json.dumps(nationality_labels),
        'nationality_data': json.dumps(nationality_data),
        'maker_labels': json.dumps(maker_labels),
        'maker_data': json.dumps(maker_data),
        'asset_type_labels': json.dumps(asset_type_labels),
        'asset_type_data': json.dumps(asset_type_data),
        'contract_data': json.dumps([contract_breakdown.get('permanent', 0), contract_breakdown.get('yearly', 0), contract_breakdown.get('ajeer', 0)]),
    }
    return render(request, 'hr/dashboard.html', context)


class EmployeeListView(AdminRequiredMixin, ListView):
    model = Employee
    template_name = 'hr/employee_list.html'
    context_object_name = 'employees'
    paginate_by = 25

    def get_queryset(self):
        queryset = Employee.objects.all()

        search = self.request.GET.get('search', '')
        contract_type = self.request.GET.get('contract_type', '')
        nationality = self.request.GET.get('nationality', '')
        deployment = self.request.GET.get('deployment', '')

        if search:
            queryset = queryset.filter(
                Q(full_name__icontains=search) |
                Q(iqama_number__icontains=search) |
                Q(work_email__icontains=search) |
                Q(mobile_number__icontains=search)
            )
        if contract_type:
            queryset = queryset.filter(contract_type=contract_type)
        if nationality:
            queryset = queryset.filter(nationality__icontains=nationality)
        if deployment:
            queryset = queryset.filter(deployment__icontains=deployment)

        return queryset.order_by('full_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = EmployeeFilterForm(self.request.GET)
        context['total_count'] = Employee.objects.count()
        context['active_count'] = Employee.objects.filter(is_active=True).count()

        # Counts by contract type
        contract_counts = Employee.objects.values('contract_type').annotate(
            count=Count('id')
        ).order_by('contract_type')
        context['contract_counts'] = {
            item['contract_type']: item['count'] for item in contract_counts
        }

        # Counts by nationality (top nationalities)
        nationality_counts = Employee.objects.exclude(nationality='').values('nationality').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        context['nationality_counts'] = nationality_counts

        return context


class EmployeeDetailView(AdminRequiredMixin, DetailView):
    model = Employee
    template_name = 'hr/employee_detail.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['documents'] = self.object.documents.all()
        context['doc_form'] = EmployeeDocumentForm()
        return context


class EmployeeCreateView(AdminRequiredMixin, CreateView):
    model = Employee
    form_class = EmployeeForm
    template_name = 'hr/employee_form.html'
    success_url = reverse_lazy('hr:employee_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Employee created successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add New Employee'
        context['button_text'] = 'Save Employee'
        return context


class EmployeeUpdateView(AdminRequiredMixin, UpdateView):
    model = Employee
    form_class = EmployeeForm
    template_name = 'hr/employee_form.html'
    success_url = reverse_lazy('hr:employee_list')

    def form_valid(self, form):
        messages.success(self.request, 'Employee updated successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Employee'
        context['button_text'] = 'Update Employee'
        return context


class EmployeeDeleteView(AdminRequiredMixin, DeleteView):
    model = Employee
    template_name = 'hr/employee_confirm_delete.html'
    success_url = reverse_lazy('hr:employee_list')

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Employee deleted successfully.')
        return super().delete(request, *args, **kwargs)


@login_required
def employee_import(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'You do not have permission to import employees.')
        return redirect('hr:employee_list')

    if request.method == 'POST':
        form = EmployeeImportForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['excel_file']

            try:
                wb = openpyxl.load_workbook(excel_file, data_only=True)
                ws = wb.active

                # Detect header row — look for "NAME" or "S/No." in first 5 rows
                header_row = None
                headers = []
                for row_num in range(1, 6):
                    row_values = [cell.value for cell in ws[row_num]]
                    for val in row_values:
                        if val and str(val).strip().upper() in ('NAME', 'S/NO.', 'S/NO'):
                            header_row = row_num
                            headers = row_values
                            break
                    if header_row:
                        break

                if not header_row:
                    # Fallback: use first row
                    header_row = 1
                    headers = [cell.value for cell in ws[1]]

                # Build header map (lowercase stripped header → column index)
                header_map = {}
                for idx, h in enumerate(headers):
                    if h:
                        header_map[str(h).strip().lower()] = idx

                imported_count = 0
                updated_count = 0

                for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
                    if not any(row):
                        continue

                    def get_value(possible_headers):
                        for h in possible_headers:
                            if h in header_map:
                                idx = header_map[h]
                                if idx < len(row):
                                    return row[idx]
                        return None

                    name = get_value(['name', 'full name', 'employee name'])
                    if not name or not str(name).strip():
                        continue

                    iqama = get_value(['iqamah/passport', 'iqama/passport', 'iqamah', 'iqama', 'passport', 'iqama no', 'iqama no.'])
                    if not iqama or not str(iqama).strip():
                        continue

                    iqama_str = str(iqama).strip()

                    # Parse dates
                    def parse_date(val):
                        if val is None:
                            return None
                        if isinstance(val, datetime):
                            return val.date()
                        try:
                            from datetime import date
                            if isinstance(val, date):
                                return val
                        except Exception:
                            pass
                        return None

                    dob = parse_date(get_value(['date of birth', 'dob', 'birth date']))
                    joining = parse_date(get_value(['joining date', 'date of joining', 'join date']))

                    # Parse contract type
                    contract_raw = str(get_value(['contract type', 'contract']) or '').strip().lower()
                    contract_type = ''
                    if 'permanent' in contract_raw:
                        contract_type = 'permanent'
                    elif 'yearly' in contract_raw:
                        contract_type = 'yearly'
                    elif 'ajeer' in contract_raw:
                        contract_type = 'ajeer'

                    # Parse marital status
                    marital_raw = str(get_value(['marital status', 'marital']) or '').strip().lower()
                    marital_status = ''
                    if 'married' in marital_raw:
                        marital_status = 'married'
                    elif 'single' in marital_raw:
                        marital_status = 'single'

                    # Blood group — exact match
                    blood_raw = str(get_value(['blood group', 'blood']) or '').strip()
                    valid_blood = [c[0] for c in Employee.BLOOD_GROUP_CHOICES]
                    blood_group = blood_raw if blood_raw in valid_blood else ''

                    # Handle two email columns — personal (col ~11) and work (col ~15)
                    personal_email = str(get_value(['personal email', 'personal e-mail']) or '').strip()
                    work_email = str(get_value(['work email', 'work e-mail', 'official email']) or '').strip()

                    # If we have generic "email" headers, openpyxl may see them positionally
                    # The Excel has two email columns — try to pick them up
                    if not personal_email and not work_email:
                        # Look for any 'email' column
                        email_val = str(get_value(['email', 'e-mail']) or '').strip()
                        if email_val:
                            work_email = email_val

                    defaults = {
                        'full_name': str(name).strip()[:255],
                        'designation': str(get_value(['designation', 'position', 'title']) or '').strip()[:255],
                        'qualification': str(get_value(['qualification', 'qualifications']) or '').strip()[:255],
                        'date_of_birth': dob,
                        'joining_date': joining,
                        'nationality': str(get_value(['nationality']) or '').strip()[:100],
                        'marital_status': marital_status,
                        'blood_group': blood_group,
                        'personal_email': personal_email,
                        'documents_link': str(get_value(['documents', 'documents link', 'document link', 'doc link']) or '').strip()[:500],
                        'deployment': str(get_value(['deployment', 'location', 'site']) or '').strip()[:100],
                        'contract_type': contract_type,
                        'work_email': work_email,
                        'mobile_number': str(get_value(['mobile no', 'mobile number', 'mobile', 'phone', 'contact no']) or '').strip()[:20],
                        'created_by': request.user,
                    }

                    _, created = Employee.objects.update_or_create(
                        iqama_number=iqama_str,
                        defaults=defaults,
                    )

                    if created:
                        imported_count += 1
                    else:
                        updated_count += 1

                msg_parts = []
                if imported_count:
                    msg_parts.append(f'{imported_count} new employees imported')
                if updated_count:
                    msg_parts.append(f'{updated_count} employees updated')
                if msg_parts:
                    messages.success(request, 'Successfully ' + ' and '.join(msg_parts) + '.')
                else:
                    messages.warning(request, 'No employees were imported. Check file format.')

                return redirect('hr:employee_list')

            except Exception as e:
                messages.error(request, f'Error importing file: {str(e)}')
    else:
        form = EmployeeImportForm()

    return render(request, 'hr/employee_import.html', {'form': form})


@login_required
def employee_export(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'You do not have permission to export employees.')
        return redirect('hr:employee_list')

    queryset = Employee.objects.all()

    # Apply filters
    search = request.GET.get('search', '')
    contract_type = request.GET.get('contract_type', '')
    nationality = request.GET.get('nationality', '')
    deployment = request.GET.get('deployment', '')

    if search:
        queryset = queryset.filter(
            Q(full_name__icontains=search) |
            Q(iqama_number__icontains=search) |
            Q(work_email__icontains=search)
        )
    if contract_type:
        queryset = queryset.filter(contract_type=contract_type)
    if nationality:
        queryset = queryset.filter(nationality__icontains=nationality)
    if deployment:
        queryset = queryset.filter(deployment__icontains=deployment)

    queryset = queryset.order_by('full_name')

    # Create workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Employees'

    # Styles (matching Leap red header)
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin'),
    )

    headers = [
        'S/No.', 'Iqama/Passport', 'Name', 'Designation', 'Qualification',
        'Date of Birth', 'Joining Date', 'Nationality', 'Marital Status',
        'Blood Group', 'Personal Email', 'Documents', 'Deployment',
        'Contract Type', 'Work Email', 'Mobile No', 'Status',
    ]

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    for row_num, emp in enumerate(queryset, 2):
        data = [
            row_num - 1,
            emp.iqama_number,
            emp.full_name,
            emp.designation,
            emp.qualification,
            emp.date_of_birth.strftime('%Y-%m-%d') if emp.date_of_birth else '',
            emp.joining_date.strftime('%Y-%m-%d') if emp.joining_date else '',
            emp.nationality,
            emp.get_marital_status_display(),
            emp.blood_group,
            emp.personal_email,
            emp.documents_link,
            emp.deployment,
            emp.get_contract_type_display(),
            emp.work_email,
            emp.mobile_number,
            'Active' if emp.is_active else 'Inactive',
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col, value=value)
            cell.border = thin_border

    # Column widths
    column_widths = [8, 18, 25, 20, 20, 14, 14, 15, 14, 12, 28, 30, 15, 14, 28, 16, 10]
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f'employees_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ─── Asset Views ─────────────────────────────────────────────────────────────


class AssetListView(AdminRequiredMixin, ListView):
    model = Asset
    template_name = 'hr/asset_list.html'
    context_object_name = 'assets'
    paginate_by = 25

    def get_queryset(self):
        queryset = Asset.objects.all()

        search = self.request.GET.get('search', '')
        asset_type = self.request.GET.get('asset_type', '')
        condition = self.request.GET.get('condition', '')
        in_stock = self.request.GET.get('in_stock', '')

        if search:
            queryset = queryset.filter(
                Q(asset_name__icontains=search) |
                Q(serial_number__icontains=search) |
                Q(employee_name__icontains=search) |
                Q(invoice_number__icontains=search)
            )
        if asset_type:
            queryset = queryset.filter(asset_type__icontains=asset_type)
        if condition:
            queryset = queryset.filter(condition=condition)
        if in_stock == 'true':
            queryset = queryset.filter(in_stock=True)
        elif in_stock == 'false':
            queryset = queryset.filter(in_stock=False)

        return queryset.order_by('asset_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = AssetFilterForm(self.request.GET)
        context['total_count'] = Asset.objects.count()
        context['in_stock_count'] = Asset.objects.filter(in_stock=True).count()
        context['assigned_count'] = Asset.objects.filter(in_stock=False).count()
        context['total_value'] = Asset.objects.aggregate(total=Sum('price'))['total'] or 0

        # Counts by asset type (top types)
        type_counts = Asset.objects.exclude(asset_type='').values('asset_type').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        context['type_counts'] = type_counts

        # Set of serial numbers (lower-cased) that appear on more than one
        # currently-assigned asset. The list template uses this to flag rows
        # at a glance instead of running an .exists() query per row.
        conflict_serials = (
            Asset.objects
            .exclude(serial_number='')
            .filter(assignments__returned_at__isnull=True)
            .values('serial_number')
            .annotate(c=Count('id', distinct=True))
            .filter(c__gt=1)
            .values_list('serial_number', flat=True)
        )
        context['conflict_serials'] = {s.lower() for s in conflict_serials}
        context['conflict_count'] = len(context['conflict_serials'])

        return context


class AssetDetailView(AdminRequiredMixin, DetailView):
    model = Asset
    template_name = 'hr/asset_detail.html'
    context_object_name = 'asset'


class AssetCreateView(AdminRequiredMixin, CreateView):
    model = Asset
    form_class = AssetForm
    template_name = 'hr/asset_form.html'
    success_url = reverse_lazy('hr:asset_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Asset created successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add New Asset'
        context['button_text'] = 'Save Asset'
        return context


class AssetUpdateView(AdminRequiredMixin, UpdateView):
    model = Asset
    form_class = AssetForm
    template_name = 'hr/asset_form.html'
    success_url = reverse_lazy('hr:asset_list')

    def form_valid(self, form):
        messages.success(self.request, 'Asset updated successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Asset'
        context['button_text'] = 'Update Asset'
        return context


class AssetDeleteView(AdminRequiredMixin, DeleteView):
    model = Asset
    template_name = 'hr/asset_confirm_delete.html'
    success_url = reverse_lazy('hr:asset_list')

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Asset deleted successfully.')
        return super().delete(request, *args, **kwargs)


# ─── Asset Assignment (Issue / Return) ───────────────────────────────────────


class AssetIssueView(AdminRequiredMixin, CreateView):
    """Issue an asset to an employee.

    Creates a new active AssetAssignment row and flips the asset's in_stock
    flag off so existing list filters keep working. Refuses if the asset
    already has an open assignment (defended by the unique partial index
    on AssetAssignment as well).
    """
    model = AssetAssignment
    form_class = AssetIssueForm
    template_name = 'hr/asset_issue_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.asset = get_object_or_404(Asset, pk=kwargs['pk'])
        if self.asset.active_assignment is not None:
            messages.error(
                request,
                f'{self.asset.asset_name} is already assigned to '
                f'{self.asset.current_holder.full_name}. Return it first.',
            )
            return redirect('hr:asset_detail', pk=self.asset.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {'assigned_at': date.today(), 'condition_out': self.asset.condition or 'used'}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['asset'] = self.asset
        return context

    def form_valid(self, form):
        form.instance.asset = self.asset
        form.instance.assigned_by = self.request.user
        response = super().form_valid(form)
        # Mirror state to legacy fields so the existing list/filter UI is
        # consistent without forcing a wider refactor.
        self.asset.in_stock = False
        self.asset.employee_name = self.object.employee.full_name
        self.asset.handover_date = self.object.assigned_at
        self.asset.handover_by = (
            self.request.user.get_full_name() or self.request.user.username
        )
        self.asset.condition = self.object.condition_out
        self.asset.save(update_fields=[
            'in_stock', 'employee_name', 'handover_date', 'handover_by',
            'condition', 'updated_at',
        ])
        messages.success(
            self.request,
            f'{self.asset.asset_name} issued to {self.object.employee.full_name}.',
        )
        return response

    def get_success_url(self):
        return reverse_lazy('hr:asset_detail', kwargs={'pk': self.asset.pk})


class AssetReturnView(AdminRequiredMixin, UpdateView):
    """Close the asset's active assignment by recording the return."""
    model = AssetAssignment
    form_class = AssetReturnForm
    template_name = 'hr/asset_return_form.html'

    def get_object(self, queryset=None):
        asset = get_object_or_404(Asset, pk=self.kwargs['pk'])
        active = asset.active_assignment
        if active is None:
            return None
        self.asset = asset
        return active

    def dispatch(self, request, *args, **kwargs):
        active = self.get_object()
        if active is None:
            messages.error(request, 'No active assignment to return.')
            return redirect('hr:asset_detail', pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {'returned_at': date.today(), 'condition_in': self.object.condition_out or 'used'}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['asset'] = self.asset
        return context

    def form_valid(self, form):
        form.instance.returned_by = self.request.user
        response = super().form_valid(form)
        # Flip the asset back into stock so the list filters reflect reality.
        self.asset.in_stock = True
        self.asset.employee_name = ''
        self.asset.condition = self.object.condition_in or self.asset.condition
        self.asset.save(update_fields=[
            'in_stock', 'employee_name', 'condition', 'updated_at',
        ])
        messages.success(
            self.request,
            f'{self.asset.asset_name} returned by {self.object.employee.full_name}.',
        )
        return response

    def get_success_url(self):
        return reverse_lazy('hr:asset_detail', kwargs={'pk': self.asset.pk})


@login_required
def asset_import(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'You do not have permission to import assets.')
        return redirect('hr:asset_list')

    if request.method == 'POST':
        form = AssetImportForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['excel_file']

            try:
                wb = openpyxl.load_workbook(excel_file, data_only=True)
                ws = wb.active

                # Detect header row — look for "Asset Name" or "S. No" in first 5 rows
                header_row = None
                headers = []
                for row_num in range(1, 6):
                    row_values = [cell.value for cell in ws[row_num]]
                    for val in row_values:
                        if val and str(val).strip().upper() in (
                            'ASSET NAME', 'S. NO', 'S.NO', 'S. NO.', 'SERIAL NO.',
                            'SERIAL NO', 'ASSET TYPE',
                        ):
                            header_row = row_num
                            headers = row_values
                            break
                    if header_row:
                        break

                if not header_row:
                    header_row = 2
                    headers = [cell.value for cell in ws[2]]

                # Build header map
                header_map = {}
                for idx, h in enumerate(headers):
                    if h:
                        header_map[str(h).strip().lower()] = idx

                imported_count = 0
                updated_count = 0

                for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
                    if not any(row):
                        continue

                    def get_value(possible_headers):
                        for h in possible_headers:
                            if h in header_map:
                                idx = header_map[h]
                                if idx < len(row):
                                    return row[idx]
                        return None

                    asset_name = get_value(['asset name', 'asset'])
                    if not asset_name or not str(asset_name).strip():
                        continue

                    def parse_date(val):
                        if val is None:
                            return None
                        if isinstance(val, datetime):
                            return val.date()
                        if isinstance(val, date):
                            return val
                        return None

                    # Parse in_stock
                    in_stock_raw = get_value(['in stock?', 'in stock', 'stock'])
                    in_stock = False
                    if in_stock_raw is not None:
                        in_stock_str = str(in_stock_raw).strip().lower()
                        in_stock = in_stock_str in ('yes', 'true', '1', 'y', 'in stock')

                    # Parse condition
                    condition_raw = str(get_value(['condition']) or '').strip().lower()
                    condition = ''
                    if 'new' in condition_raw:
                        condition = 'new'
                    elif 'used' in condition_raw:
                        condition = 'used'

                    # Parse quantity
                    qty_raw = get_value(['qty', 'quantity'])
                    quantity = 1
                    if qty_raw is not None:
                        try:
                            quantity = int(float(str(qty_raw)))
                            if quantity < 1:
                                quantity = 1
                        except (ValueError, TypeError):
                            quantity = 1

                    # Parse price
                    price_raw = get_value(['price (sar)', 'price', 'price(sar)'])
                    price = None
                    if price_raw is not None:
                        try:
                            price = round(float(str(price_raw)), 2)
                        except (ValueError, TypeError):
                            price = None

                    serial_number = str(get_value(['serial no.', 'serial no', 'serial number', 'serial']) or '').strip()

                    # Return date is free text
                    return_date_raw = get_value(['return date', 'return'])
                    return_date = str(return_date_raw).strip() if return_date_raw else ''
                    # If it's a datetime object, format it
                    if isinstance(return_date_raw, (datetime, date)):
                        return_date = return_date_raw.strftime('%Y-%m-%d') if hasattr(return_date_raw, 'strftime') else str(return_date_raw)

                    defaults = {
                        'asset_type': str(get_value(['asset type', 'type']) or '').strip()[:50],
                        'specifications': str(get_value(['specifications', 'specs', 'specification']) or '').strip()[:255],
                        'invoice_number': str(get_value(['invoice number', 'invoice no', 'invoice no.', 'invoice']) or '').strip()[:100],
                        'employee_name': str(get_value(['employee name', 'employee', 'name']) or '').strip()[:255],
                        'department': str(get_value(['department', 'dept']) or '').strip()[:100],
                        'designation': str(get_value(['designation', 'position']) or '').strip()[:255],
                        'handover_date': parse_date(get_value(['handover date', 'handover'])),
                        'handover_by': str(get_value(['handover by', 'handed by']) or '').strip()[:255],
                        'condition': condition,
                        'return_date': return_date[:100],
                        'return_to': str(get_value(['return to', 'returned to']) or '').strip()[:255],
                        'quantity': quantity,
                        'purchase_date': parse_date(get_value(['purchase date', 'purchased date', 'purchase'])),
                        'price': price,
                        'planned_life': str(get_value(['planned asset life', 'planned life', 'asset life']) or '').strip()[:50],
                        'in_stock': in_stock,
                        'created_by': request.user,
                    }

                    _, created = Asset.objects.update_or_create(
                        asset_name=str(asset_name).strip()[:255],
                        serial_number=serial_number[:255],
                        defaults=defaults,
                    )

                    if created:
                        imported_count += 1
                    else:
                        updated_count += 1

                msg_parts = []
                if imported_count:
                    msg_parts.append(f'{imported_count} new assets imported')
                if updated_count:
                    msg_parts.append(f'{updated_count} assets updated')
                if msg_parts:
                    messages.success(request, 'Successfully ' + ' and '.join(msg_parts) + '.')
                else:
                    messages.warning(request, 'No assets were imported. Check file format.')

                return redirect('hr:asset_list')

            except Exception as e:
                messages.error(request, f'Error importing file: {str(e)}')
    else:
        form = AssetImportForm()

    return render(request, 'hr/asset_import.html', {'form': form})


@login_required
def asset_export(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'You do not have permission to export assets.')
        return redirect('hr:asset_list')

    queryset = Asset.objects.all()

    # Apply filters
    search = request.GET.get('search', '')
    asset_type = request.GET.get('asset_type', '')
    condition = request.GET.get('condition', '')
    in_stock = request.GET.get('in_stock', '')

    if search:
        queryset = queryset.filter(
            Q(asset_name__icontains=search) |
            Q(serial_number__icontains=search) |
            Q(employee_name__icontains=search)
        )
    if asset_type:
        queryset = queryset.filter(asset_type__icontains=asset_type)
    if condition:
        queryset = queryset.filter(condition=condition)
    if in_stock == 'true':
        queryset = queryset.filter(in_stock=True)
    elif in_stock == 'false':
        queryset = queryset.filter(in_stock=False)

    queryset = queryset.order_by('asset_name')

    # Create workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Assets'

    # Styles (matching Leap red header)
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin'),
    )

    headers = [
        'S. No', 'Asset Name', 'Asset Type', 'Serial No.', 'Specifications',
        'Invoice Number', 'Employee Name', 'Department', 'Designation',
        'Handover Date', 'Handover By', 'Condition', 'Return Date',
        'Return To', 'QTY', 'Purchase Date', 'Price (SAR)',
        'Planned Asset Life', 'Asset Current Age', 'In Stock?',
    ]

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    for row_num, asset in enumerate(queryset, 2):
        data = [
            row_num - 1,
            asset.asset_name,
            asset.asset_type,
            asset.serial_number,
            asset.specifications,
            asset.invoice_number,
            asset.employee_name,
            asset.department,
            asset.designation,
            asset.handover_date.strftime('%Y-%m-%d') if asset.handover_date else '',
            asset.handover_by,
            asset.get_condition_display() if asset.condition else '',
            asset.return_date,
            asset.return_to,
            asset.quantity,
            asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '',
            float(asset.price) if asset.price else '',
            asset.planned_life,
            asset.current_age,
            'Yes' if asset.in_stock else 'No',
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col, value=value)
            cell.border = thin_border

    # Column widths
    column_widths = [8, 22, 14, 22, 30, 16, 22, 16, 18, 14, 16, 12, 18, 16, 8, 14, 14, 18, 18, 10]
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f'assets_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE DOCUMENTS
# ═══════════════════════════════════════════════════════════════

@login_required
def employee_document_upload(request, pk):
    """Upload a document for an employee."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:employee_list')

    employee = get_object_or_404(Employee, pk=pk)

    if request.method == 'POST':
        form = EmployeeDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.employee = employee
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, f'Document "{doc.title}" uploaded.')
        else:
            messages.error(request, 'Please fix the errors below.')

    return redirect('hr:employee_detail', pk=pk)


@login_required
def employee_document_delete(request, pk):
    """Delete an employee document."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:employee_list')

    doc = get_object_or_404(EmployeeDocument, pk=pk)
    employee_pk = doc.employee_id
    if doc.file:
        doc.file.delete(save=False)
    doc.delete()
    messages.success(request, 'Document deleted.')
    return redirect('hr:employee_detail', pk=employee_pk)


# ═══════════════════════════════════════════════════════════════
# VEHICLES
# ═══════════════════════════════════════════════════════════════

class VehicleListView(AdminRequiredMixin, ListView):
    model = Vehicle
    template_name = 'hr/vehicle_list.html'
    context_object_name = 'vehicles'
    paginate_by = 25

    def get_queryset(self):
        queryset = Vehicle.objects.all()
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        if search:
            queryset = queryset.filter(
                Q(plate_number__icontains=search) |
                Q(vehicle_maker__icontains=search) |
                Q(vehicle_model__icontains=search) |
                Q(driver_name__icontains=search) |
                Q(chassis_number__icontains=search)
            )
        if status:
            queryset = queryset.filter(vehicle_status=status)
        compliance = self.request.GET.get('compliance')
        if compliance == 'issues':
            pks = [v.pk for v in queryset if v.has_compliance_issue]
            queryset = queryset.filter(pk__in=pks)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = VehicleFilterForm(self.request.GET)
        context['total_count'] = self.get_queryset().count()
        all_vehicles = Vehicle.objects.all()
        context['valid_count'] = all_vehicles.filter(vehicle_status='valid').count()
        context['compliance_issues'] = sum(1 for v in all_vehicles if v.has_compliance_issue)
        return context


class VehicleCreateView(AdminRequiredMixin, CreateView):
    model = Vehicle
    form_class = VehicleForm
    template_name = 'hr/vehicle_form.html'
    success_url = reverse_lazy('hr:vehicle_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add Vehicle'
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Vehicle added.')
        return super().form_valid(form)


class VehicleDetailView(AdminRequiredMixin, DetailView):
    model = Vehicle
    template_name = 'hr/vehicle_detail.html'
    context_object_name = 'vehicle'


class VehicleUpdateView(AdminRequiredMixin, UpdateView):
    model = Vehicle
    form_class = VehicleForm
    template_name = 'hr/vehicle_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit: {self.object.plate_number}'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Vehicle updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('hr:vehicle_detail', kwargs={'pk': self.object.pk})


class VehicleDeleteView(AdminRequiredMixin, DeleteView):
    model = Vehicle
    template_name = 'hr/vehicle_confirm_delete.html'
    success_url = reverse_lazy('hr:vehicle_list')

    def form_valid(self, form):
        messages.success(self.request, 'Vehicle deleted.')
        return super().form_valid(form)


@login_required
def vehicle_import(request):
    """Import vehicles from MOI Excel export."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:vehicle_list')

    if request.method != 'POST':
        return redirect('hr:vehicle_list')

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        messages.error(request, 'Please select a file.')
        return redirect('hr:vehicle_list')

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        ws = wb.active

        header_row = None
        for r in range(1, min(20, ws.max_row + 1)):
            val = str(ws.cell(row=r, column=1).value or '').strip()
            if 'plate' in val.lower():
                header_row = r
                break
        if not header_row:
            messages.error(request, 'Could not find header row.')
            return redirect('hr:vehicle_list')

        imported = 0
        for r in range(header_row + 1, ws.max_row + 1):
            plate = ws.cell(row=r, column=1).value
            if not plate:
                continue
            plate_str = str(plate).strip()
            if not plate_str:
                continue

            def cv(col):
                v = ws.cell(row=r, column=col).value
                return str(v).strip() if v is not None else ''

            mvpi_raw = cv(16).lower()
            mvpi = 'valid' if mvpi_raw == 'valid' else ('expired' if 'expir' in mvpi_raw else 'not_exist')
            ins_raw = cv(17).lower()
            ins = 'valid' if ins_raw == 'valid' else ('expired' if 'expir' in ins_raw else 'not_exist')
            rest_raw = cv(18).lower()
            rest = 'unrestricted' if 'unrestrict' in rest_raw else 'restricted'
            vstatus_raw = cv(10).lower()
            vstatus = 'valid' if vstatus_raw == 'valid' else 'expired'

            Vehicle.objects.update_or_create(
                plate_number=plate_str,
                defaults={
                    'plate_type': cv(2),
                    'branch_name': cv(3),
                    'vehicle_maker': cv(4),
                    'vehicle_model': cv(5),
                    'model_year': cv(6),
                    'sequence_number': cv(7),
                    'chassis_number': cv(8),
                    'major_color': cv(9),
                    'vehicle_status': vstatus,
                    'ownership_date': cv(11),
                    'license_expiry': cv(12),
                    'inspection_expiry': cv(13),
                    'driver_id': cv(14),
                    'driver_name': cv(15),
                    'mvpi_status': mvpi,
                    'insurance_status': ins,
                    'restriction_status': rest,
                    'license_issue_date': cv(19),
                    'body_type': cv(21),
                    'created_by': request.user,
                },
            )
            imported += 1

        messages.success(request, f'Imported {imported} vehicles.')
        return redirect('hr:vehicle_list')

    except Exception as e:
        messages.error(request, f'Import error: {str(e)}')
        return redirect('hr:vehicle_list')


@login_required
def vehicle_export(request):
    """Export vehicles to Excel."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:vehicle_list')

    vehicles = Vehicle.objects.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Vehicles'

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    headers = [
        'Plate Number', 'Plate Type', 'Branch', 'Maker', 'Model', 'Year',
        'Sequence No.', 'Chassis No.', 'Color', 'Status',
        'Ownership Date', 'License Expiry', 'Inspection Expiry',
        'Driver ID', 'Driver Name', 'MVPI', 'Insurance', 'Restriction',
        'License Issue Date', 'Body Type',
    ]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border

    for row_num, v in enumerate(vehicles, 2):
        data = [
            v.plate_number, v.plate_type, v.branch_name, v.vehicle_maker,
            v.vehicle_model, v.model_year, v.sequence_number, v.chassis_number,
            v.major_color, v.get_vehicle_status_display(),
            v.ownership_date, v.license_expiry, v.inspection_expiry,
            v.driver_id, v.driver_name,
            v.get_mvpi_status_display(), v.get_insurance_status_display(),
            v.get_restriction_status_display(), v.license_issue_date, v.body_type,
        ]
        for col, val in enumerate(data, 1):
            ws.cell(row=row_num, column=col, value=val).border = thin_border

    widths = [16, 14, 10, 12, 16, 8, 14, 22, 10, 10, 14, 14, 14, 14, 30, 12, 12, 14, 14, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="vehicles_{datetime.now().strftime("%Y%m%d")}.xlsx"'
    wb.save(response)
    return response


# ─── Leave Type CRUD ──────────────────────────────────────────


class LeaveTypeListView(AdminRequiredMixin, ListView):
    model = LeaveType
    template_name = 'hr/leavetype_list.html'
    context_object_name = 'leave_types'
    paginate_by = 25


class LeaveTypeCreateView(AdminRequiredMixin, CreateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'hr/leavetype_form.html'
    success_url = reverse_lazy('hr:leavetype_list')

    def form_valid(self, form):
        messages.success(self.request, 'Leave type created successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add Leave Type'
        context['button_text'] = 'Save Leave Type'
        return context


class LeaveTypeUpdateView(AdminRequiredMixin, UpdateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'hr/leavetype_form.html'
    success_url = reverse_lazy('hr:leavetype_list')

    def form_valid(self, form):
        messages.success(self.request, 'Leave type updated successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Leave Type'
        context['button_text'] = 'Save Changes'
        return context


# ─── Holiday CRUD ─────────────────────────────────────────────

class HolidayListView(AdminRequiredMixin, ListView):
    model = Holiday
    template_name = 'hr/holiday_list.html'
    context_object_name = 'holidays'
    ordering = ['date']
    paginate_by = 25


class HolidayCreateView(AdminRequiredMixin, CreateView):
    model = Holiday
    form_class = HolidayForm
    template_name = 'hr/holiday_form.html'
    success_url = reverse_lazy('hr:holiday_list')

    def form_valid(self, form):
        messages.success(self.request, 'Holiday created successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add Holiday'
        context['button_text'] = 'Save Holiday'
        return context


class HolidayUpdateView(AdminRequiredMixin, UpdateView):
    model = Holiday
    form_class = HolidayForm
    template_name = 'hr/holiday_form.html'
    success_url = reverse_lazy('hr:holiday_list')

    def form_valid(self, form):
        messages.success(self.request, 'Holiday updated successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Holiday'
        context['button_text'] = 'Save Changes'
        return context


class HolidayDeleteView(AdminRequiredMixin, DeleteView):
    model = Holiday
    template_name = 'hr/holiday_confirm_delete.html'
    success_url = reverse_lazy('hr:holiday_list')

    def form_valid(self, form):
        messages.success(self.request, 'Holiday deleted.')
        return super().form_valid(form)


# ─── Leave Records, Summary & Entitlement Generation ─────────────────────────


class LeaveRecordCreateView(AdminRequiredMixin, CreateView):
    model = LeaveRecord
    form_class = LeaveRecordForm
    template_name = 'hr/leaverecord_form.html'

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        if form.cleaned_data.get('days') is None:
            form.instance.days = None  # let the model auto-compute working days
            # (an explicit 0 is preserved — only a blank field auto-computes)
        messages.success(self.request, 'Leave recorded.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('hr:leave_summary', kwargs={'pk': self.object.employee_id})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Record Leave'
        ctx['button_text'] = 'Save Leave'
        return ctx


class LeaveRecordDeleteView(AdminRequiredMixin, DeleteView):
    model = LeaveRecord
    template_name = 'hr/leaverecord_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('hr:leave_summary', kwargs={'pk': self.object.employee_id})


class EmployeeLeaveSummaryView(AdminRequiredMixin, DetailView):
    model = Employee
    template_name = 'hr/leave_summary.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year = _int_or(self.request.GET.get('year'), timezone.now().year)
        ctx['year'] = year
        ctx['entitlements'] = LeaveEntitlement.objects.filter(
            employee=self.object, year=year).select_related('leave_type')
        ctx['records'] = self.object.leave_records.filter(
            start_date__year=year).select_related('leave_type')
        return ctx


@login_required
def entitlement_year(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    year = _int_or(request.GET.get('year'), timezone.now().year)
    if request.method == 'POST':
        post_year = _int_or(request.POST.get('year'), year)
        created = generate_year_entitlements(post_year, actor=request.user)
        messages.success(request, f'Generated {created} entitlement row(s) for {post_year}.')
        return redirect(f"{reverse('hr:entitlement_year')}?year={post_year}")
    entitlements = LeaveEntitlement.objects.filter(year=year).select_related('employee', 'leave_type')
    return render(request, 'hr/entitlement_year.html', {'year': year, 'entitlements': entitlements})


class AttendanceHistoryView(AdminRequiredMixin, DetailView):
    model = Employee
    template_name = 'hr/attendance_history.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        year = _int_or(self.request.GET.get('year'), now.year)
        month = _int_or(self.request.GET.get('month'), now.month, lo=1, hi=12)
        qs = self.object.attendance.filter(date__year=year, date__month=month).order_by('date')
        counts = {row['status']: row['n'] for row in qs.values('status').annotate(n=Count('id'))}
        total_hours = qs.aggregate(s=Sum('hours_worked'))['s'] or 0
        ctx.update({
            'year': year, 'month': month, 'records': qs,
            'summary': {
                'present': counts.get('present', 0), 'absent': counts.get('absent', 0),
                'leave': counts.get('leave', 0), 'holiday': counts.get('holiday', 0),
                'weekend': counts.get('weekend', 0), 'total_hours': total_hours,
            },
        })
        return ctx


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return timezone.now().date()


def _int_or(value, default, lo=None, hi=None):
    """Parse an int query param, falling back to `default` on bad/out-of-range input."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        return default
    return n


@login_required
def attendance_grid(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')

    day = _parse_date(request.GET.get('date') or (request.POST.get('date') if request.method == 'POST' else None))
    employees = list(Employee.objects.filter(is_active=True).order_by('full_name'))

    if request.method == 'POST':
        for emp in employees:
            ci = request.POST.get(f'check_in_{emp.pk}') or None
            co = request.POST.get(f'check_out_{emp.pk}') or None
            ci_t = datetime.strptime(ci, '%H:%M').time() if ci else None
            co_t = datetime.strptime(co, '%H:%M').time() if co else None
            status, hours = derive_status(emp, day, ci_t, co_t)
            AttendanceRecord.objects.update_or_create(
                employee=emp, date=day,
                defaults={'check_in': ci_t, 'check_out': co_t, 'status': status,
                          'hours_worked': hours, 'created_by': request.user})
        messages.success(request, f'Attendance saved for {day:%Y-%m-%d}.')
        return redirect(f"{reverse('hr:attendance_grid')}?date={day:%Y-%m-%d}")

    existing = {r.employee_id: r for r in AttendanceRecord.objects.filter(date=day)}
    rows = []
    for emp in employees:
        rec = existing.get(emp.pk)
        preview_status, _ph = derive_status(emp, day, rec.check_in if rec else None,
                                            rec.check_out if rec else None)
        locked = preview_status in ('leave', 'holiday', 'weekend')
        rows.append({'employee': emp, 'record': rec,
                     'status': rec.status if rec else preview_status, 'locked': locked})
    return render(request, 'hr/attendance_grid.html', {'day': day, 'rows': rows})


@login_required
def attendance_settings(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    obj = AttendanceSettings.load()
    if request.method == 'POST':
        form = AttendanceSettingsForm(request.POST)
        if form.is_valid():
            obj.weekend_days = ','.join(form.cleaned_data['weekend_days'])
            obj.save()
            messages.success(request, 'Attendance settings saved.')
            return redirect('hr:attendance_settings')
    else:
        form = AttendanceSettingsForm()
        form.initial_from(obj)
    return render(request, 'hr/attendance_settings.html', {'form': form})


@login_required
@require_POST
def attendance_regenerate(request):
    """Re-derive stored status for all records on a given date (after leave/holiday edits)."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    day = _parse_date(request.POST.get('date'))
    n = 0
    for rec in AttendanceRecord.objects.filter(date=day).select_related('employee'):
        status, hours = derive_status(rec.employee, day, rec.check_in, rec.check_out)
        AttendanceRecord.objects.filter(pk=rec.pk).update(status=status, hours_worked=hours)
        n += 1
    messages.success(request, f'Regenerated {n} record(s) for {day:%Y-%m-%d}.')
    return redirect(f"{reverse('hr:attendance_grid')}?date={day:%Y-%m-%d}")


@login_required
def attendance_matrix(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    period = request.GET.get('period') if request.GET.get('period') in ('week', 'month') else 'month'
    anchor = _parse_date(request.GET.get('date'))
    start, end = period_range(period, anchor)
    employees = list(Employee.objects.filter(is_active=True).order_by('full_name'))
    days, rows = build_matrix(employees, start, end)
    prev_anchor = start - timedelta(days=1)
    next_anchor = end + timedelta(days=1)
    return render(request, 'hr/attendance_matrix.html', {
        'period': period, 'anchor': anchor, 'start': start, 'end': end,
        'days': days, 'rows': rows,
        'prev_anchor': prev_anchor, 'next_anchor': next_anchor,
        'today': timezone.now().date(),
        'leave_types': LeaveType.objects.filter(is_active=True).order_by('name'),
        # so the column-header shading matches the configured weekend (not a hardcoded Fri/Sat)
        'weekend_set': AttendanceSettings.load().weekend_day_set(),
    })


@login_required
@require_POST
def attendance_mark_leave(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        raise PermissionDenied
    try:
        payload = json.loads(request.body or '{}')
        emp_id = int(payload['employee'])
        day = datetime.strptime(payload['date'], '%Y-%m-%d').date()
        lt_id = int(payload['leave_type'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Bad payload'}, status=400)

    employee = Employee.objects.filter(pk=emp_id, is_active=True).first()
    leave_type = LeaveType.objects.filter(pk=lt_id, is_active=True).first()
    if employee is None or leave_type is None:
        return JsonResponse({'error': 'Unknown employee or leave type'}, status=400)

    with transaction.atomic():
        lr = LeaveRecord.objects.create(
            employee=employee, leave_type=leave_type,
            start_date=day, end_date=day, created_by=request.user)
        AttendanceRecord.objects.update_or_create(
            employee=employee, date=day,
            defaults={'status': 'leave', 'check_in': None, 'check_out': None,
                      'hours_worked': None, 'created_by': request.user})
    return JsonResponse({'ok': True, 'status': 'leave', 'leave_record_id': lr.pk})


@login_required
@require_POST
def attendance_unmark_leave(request):
    return JsonResponse({'error': 'not implemented'}, status=501)
