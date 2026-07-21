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

from .models import Employee, Asset, AssetAssignment, Vehicle, VehicleDocument, EmployeeDocument, LeaveType, Holiday, LeaveEntitlement, LeaveRecord, AttendanceRecord, AttendanceSettings, WorkingDay, WFHRecord
from .forms import (
    EmployeeForm, EmployeeFilterForm, EmployeeImportForm,
    AssetForm, AssetFilterForm, AssetImportForm, AssetIssueForm, AssetReturnForm,
    VehicleForm, VehicleFilterForm, EmployeeDocumentForm, VehicleDocumentForm,
    LeaveTypeForm, HolidayForm, WorkingDayForm, LeaveRecordForm, WFHRecordForm,
    AttendanceSettingsForm,
)
from .leave_services import generate_year_entitlements
from .attendance_services import derive_status
from .attendance_matrix import period_range, build_matrix, display_status_no_record


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_super_admin_user or self.request.user.is_admin_user


class SuperAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Stricter than AdminRequiredMixin — Super Admin only, no 'admin' role.
    Used for the conditional-leave approval queue per the access-control spec."""
    def test_func(self):
        return self.request.user.is_super_admin_user


def is_designated_approver(user):
    """True if `user` currently holds active approval authority for conditional
    leave requests. This — not a username check — is what makes Aamna Khan and
    Ali Sultan (or whoever holds these rows) able to actually approve/reject."""
    from .models import LeaveApprover
    return LeaveApprover.objects.filter(user=user, is_active=True).exists()


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


@login_required
def my_profile(request):
    """Self-service portal: the logged-in user's own HR record — attendance,
    leave balance, assets held, documents (iqama/passport), and any vehicle.
    Shows a friendly prompt if the account isn't linked to an employee yet."""
    from django.db.models import Q
    from django.utils import timezone
    from .models import AttendanceRecord, Asset, Vehicle

    emp = getattr(request.user, 'employee_profile', None)
    context = {'employee': emp}
    if emp:
        today = timezone.localtime(timezone.now()).date()
        # Assets in custody. The roster tracks holders two ways: proper
        # issue/return assignments AND a denormalised Asset.employee_name (used
        # by the imported data). Union both, deduped, so nothing is missed.
        asset_ids = set(
            emp.asset_assignments.filter(returned_at__isnull=True)
            .values_list('asset_id', flat=True))
        if emp.full_name:
            asset_ids |= set(
                Asset.objects.filter(employee_name__iexact=emp.full_name)
                .values_list('id', flat=True))
        context['assets'] = Asset.objects.filter(id__in=asset_ids).order_by('asset_name')
        # Documents (iqama/passport copies, contracts, etc.).
        context['documents'] = emp.documents.all()
        # Leave balance for the current year.
        context['entitlements'] = (
            emp.leave_entitlements.filter(year=today.year)
            .select_related('leave_type'))
        # Attendance summary for the current month.
        month_records = AttendanceRecord.objects.filter(
            employee=emp, date__year=today.year, date__month=today.month)
        summary = {}
        for rec in month_records:
            summary[rec.status] = summary.get(rec.status, 0) + 1
        context['attendance_summary'] = summary
        context['attendance_month'] = today
        context['recent_leaves'] = emp.leave_records.select_related(
            'leave_type').order_by('-start_date')[:5]
        # Vehicles. No hard FK, so match on driver_id == iqama (the reliable
        # key in the data) or driver_name == full name.
        veh_q = Q()
        if emp.iqama_number:
            veh_q |= Q(driver_id=emp.iqama_number)
        if emp.full_name:
            veh_q |= Q(driver_name__iexact=emp.full_name)
        context['vehicles'] = (
            Vehicle.objects.filter(veh_q) if veh_q else Vehicle.objects.none())
    return render(request, 'hr/my_profile.html', context)


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
        work_location = self.request.GET.get('work_location', '')
        status = self.request.GET.get('status', '')

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
        if work_location:
            queryset = queryset.filter(work_location=work_location)
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)

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
@require_POST
def employee_bulk_work_location(request):
    """TEMPORARY: bulk-set the Office/Site (work_location) for many employees at
    once from the list page. Remove this view (and its URL + list-page controls)
    once the initial back-fill is done."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:employee_list')
    value = request.POST.get('work_location', '')
    if value not in dict(Employee.WORK_LOCATION_CHOICES):
        messages.error(request, 'Pick Office or Site.')
        return redirect('hr:employee_list')
    ids = [int(x) for x in request.POST.getlist('employee_ids') if x.isdigit()]
    if not ids:
        messages.error(request, 'Select at least one employee.')
        return redirect('hr:employee_list')
    updated = Employee.objects.filter(id__in=ids).update(work_location=value)
    messages.success(request, f'Set {updated} employee(s) to {dict(Employee.WORK_LOCATION_CHOICES)[value]}.')
    return redirect(request.META.get('HTTP_REFERER') or 'hr:employee_list')


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
        status = self.request.GET.get('status', '')

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
        if status == 'decommissioned':
            queryset = queryset.filter(is_decommissioned=True)
        elif status == 'in_service':
            queryset = queryset.filter(is_decommissioned=False)

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


def _asset_employees_context():
    """Active employees for the asset form's 'pick employee' dropdown, plus a
    map (pk -> name/designation) so selecting one auto-fills the text fields.
    Returned as a dict for json_script (which serialises it in the template)."""
    emps = list(Employee.objects.filter(is_active=True).order_by('full_name'))
    data = {
        str(e.pk): {'name': e.full_name, 'designation': e.designation or ''}
        for e in emps
    }
    return emps, data


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
        context['employees'], context['employees_json'] = _asset_employees_context()
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
        context['employees'], context['employees_json'] = _asset_employees_context()
        return context


class AssetDeleteView(AdminRequiredMixin, DeleteView):
    model = Asset
    template_name = 'hr/asset_confirm_delete.html'
    success_url = reverse_lazy('hr:asset_list')

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Asset deleted successfully.')
        return super().delete(request, *args, **kwargs)


@login_required
@require_POST
def asset_decommission(request, pk):
    """Mark an asset as out of service (dead / no longer usable). It can never
    be in stock once decommissioned."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:asset_list')
    from django.utils import timezone
    asset = get_object_or_404(Asset, pk=pk)
    asset.is_decommissioned = True
    asset.in_stock = False  # out of service can't be in stock
    if not asset.decommissioned_on:
        asset.decommissioned_on = timezone.localdate()
    reason = (request.POST.get('reason') or '').strip()
    if reason:
        asset.decommission_reason = reason
    asset.save(update_fields=['is_decommissioned', 'in_stock',
                              'decommissioned_on', 'decommission_reason', 'updated_at'])
    messages.success(request, f'"{asset.asset_name}" marked out of service.')
    return redirect(request.POST.get('next') or 'hr:asset_list')


@login_required
@require_POST
def asset_restore(request, pk):
    """Restore a decommissioned asset back into service."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:asset_list')
    asset = get_object_or_404(Asset, pk=pk)
    asset.is_decommissioned = False
    asset.decommissioned_on = None
    asset.decommission_reason = ''
    asset.save(update_fields=['is_decommissioned', 'decommissioned_on',
                              'decommission_reason', 'updated_at'])
    messages.success(request, f'"{asset.asset_name}" restored to service.')
    return redirect(request.POST.get('next') or 'hr:asset_list')


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
def leave_request_document_download(request, pk):
    """Stream a LeaveRequest's uploaded document only to the employee it
    belongs to, a designated approver, or a super admin — never via a public
    media URL. This deliberately does NOT follow the EmployeeDocument/
    medical_certificate pattern (those link straight to MEDIA_URL); this field
    holds sensitive personal documents and needs a real permission check."""
    from django.http import FileResponse, Http404
    from .models import LeaveRequest
    # Look up without get_object_or_404 and check authorization before ever
    # confirming the object exists — otherwise a nonexistent pk (404) and an
    # existing-but-forbidden pk (403) are distinguishable, letting any
    # authenticated user enumerate valid LeaveRequest ids. Both "doesn't
    # exist" and "exists but you can't see it" collapse to the same 404.
    leave_request = LeaveRequest.objects.filter(pk=pk).first()
    user = request.user
    is_owner = bool(leave_request) and leave_request.employee.user_id == user.id
    authorized = leave_request is not None and (
        is_owner or is_designated_approver(user) or user.is_super_admin_user)
    if not authorized:
        raise Http404('No such leave request.')
    if not leave_request.document:
        raise Http404('No document attached to this request.')
    return FileResponse(leave_request.document.open('rb'), as_attachment=True,
                        filename=leave_request.document.name.rsplit('/', 1)[-1])


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['documents'] = self.object.documents.all()
        context['doc_form'] = VehicleDocumentForm()
        return context


@login_required
def vehicle_document_upload(request, pk):
    """Upload a document for a vehicle."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:vehicle_list')

    vehicle = get_object_or_404(Vehicle, pk=pk)
    if request.method == 'POST':
        form = VehicleDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.vehicle = vehicle
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, f'Document "{doc.title}" uploaded.')
        else:
            errs = '; '.join(f'{f}: {", ".join(e)}' for f, e in form.errors.items())
            messages.error(request, f'Could not upload document. {errs}')
    return redirect('hr:vehicle_detail', pk=pk)


@login_required
def vehicle_document_edit(request, pk):
    """Edit a vehicle document's details, optionally replacing the file.
    Replacing the file reclaims the old one via the central pre_save signal."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:vehicle_list')

    doc = get_object_or_404(VehicleDocument, pk=pk)
    if request.method == 'POST':
        form = VehicleDocumentForm(request.POST, request.FILES, instance=doc)
        if form.is_valid():
            form.save()
            messages.success(request, f'Document "{doc.title}" updated.')
            return redirect('hr:vehicle_detail', pk=doc.vehicle_id)
        messages.error(request, 'Please fix the errors below.')
    else:
        form = VehicleDocumentForm(instance=doc)
    return render(request, 'hr/vehicle_document_edit.html', {
        'form': form, 'doc': doc, 'vehicle': doc.vehicle})


@login_required
def vehicle_document_delete(request, pk):
    """Delete a vehicle document (its file is reclaimed by the cleanup signal)."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:vehicle_list')

    doc = get_object_or_404(VehicleDocument, pk=pk)
    vehicle_pk = doc.vehicle_id
    doc.delete()
    messages.success(request, 'Document deleted.')
    return redirect('hr:vehicle_detail', pk=vehicle_pk)


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


# ─── WorkingDay CRUD ──────────────────────────────────────────

class WorkingDayListView(AdminRequiredMixin, ListView):
    model = WorkingDay
    template_name = 'hr/workingday_list.html'
    context_object_name = 'working_days'
    paginate_by = 25

class WorkingDayCreateView(AdminRequiredMixin, CreateView):
    model = WorkingDay
    form_class = WorkingDayForm
    template_name = 'hr/workingday_form.html'
    success_url = reverse_lazy('hr:workingday_list')
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs); ctx['title'] = 'Add Working Day'; ctx['button_text'] = 'Save'; return ctx

class WorkingDayUpdateView(AdminRequiredMixin, UpdateView):
    model = WorkingDay
    form_class = WorkingDayForm
    template_name = 'hr/workingday_form.html'
    success_url = reverse_lazy('hr:workingday_list')
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs); ctx['title'] = 'Edit Working Day'; ctx['button_text'] = 'Save Changes'; return ctx

class WorkingDayDeleteView(AdminRequiredMixin, DeleteView):
    model = WorkingDay
    template_name = 'hr/workingday_confirm_delete.html'
    success_url = reverse_lazy('hr:workingday_list')


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
        if request.POST.get('action') == 'reapply':
            from hr.leave_services import reapply_leave_type_defaults
            updated = reapply_leave_type_defaults(post_year)
            messages.success(
                request,
                f'Re-applied leave-type day counts to {updated} entitlement(s) for {post_year}.')
        else:
            created = generate_year_entitlements(post_year, actor=request.user)
            messages.success(request, f'Generated {created} entitlement row(s) for {post_year}.')
        return redirect(f"{reverse('hr:entitlement_year')}?year={post_year}")
    entitlements = (LeaveEntitlement.objects.filter(year=year)
                    .select_related('employee', 'leave_type')
                    .order_by('employee__full_name', 'leave_type__name'))

    # Batch the taken-days per (employee, leave_type) in one query (avoid N+1).
    from decimal import Decimal
    from collections import OrderedDict
    taken_map = {}
    for r in (LeaveRecord.objects.filter(start_date__year=year)
              .values('employee_id', 'leave_type_id')
              .annotate(t=Sum('days'))):
        taken_map[(r['employee_id'], r['leave_type_id'])] = r['t'] or Decimal('0')

    # Group entitlement rows under each employee with combined totals.
    groups = OrderedDict()
    for e in entitlements:
        g = groups.get(e.employee_id)
        if g is None:
            g = groups[e.employee_id] = {
                'employee': e.employee, 'rows': [],
                'total_entitled': Decimal('0'), 'total_taken': Decimal('0'),
                'total_remaining': Decimal('0'),
            }
        taken = taken_map.get((e.employee_id, e.leave_type_id), Decimal('0'))
        remaining = e.entitled_days - taken
        g['rows'].append({'leave_type': e.leave_type, 'entitled': e.entitled_days,
                          'taken': taken, 'remaining': remaining})
        g['total_entitled'] += e.entitled_days
        g['total_taken'] += taken
        g['total_remaining'] += remaining

    return render(request, 'hr/entitlement_year.html',
                  {'year': year, 'groups': list(groups.values()),
                   'entitlement_count': entitlements.count()})


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
                'late': counts.get('late', 0), 'wfh': counts.get('wfh', 0),
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
    # Office / Site segregation. 'office' tab = office + unassigned (so nobody is
    # hidden until back-filled); 'site' tab = site only.
    location = (request.GET.get('location') or request.POST.get('location') or 'office')
    emp_qs = Employee.objects.filter(is_active=True)
    if location == 'site':
        emp_qs = emp_qs.filter(work_location='site')
    else:
        location = 'office'
        emp_qs = emp_qs.exclude(work_location='site')
    employees = list(emp_qs.order_by('full_name'))

    if request.method == 'POST':
        for emp in employees:
            ci = request.POST.get(f'check_in_{emp.pk}') or None
            co = request.POST.get(f'check_out_{emp.pk}') or None
            ci_t = datetime.strptime(ci, '%H:%M').time() if ci else None
            co_t = datetime.strptime(co, '%H:%M').time() if co else None
            # Reconcile per-day WFH flag before deriving status.
            wants_wfh = request.POST.get(f'wfh_{emp.pk}') == '1'
            if wants_wfh:
                WFHRecord.objects.get_or_create(
                    employee=emp, start_date=day, end_date=day,
                    defaults={'created_by': request.user})
            else:
                # Only delete single-day records; never touch multi-day WFH.
                WFHRecord.objects.filter(employee=emp, start_date=day, end_date=day).delete()
            status, hours = derive_status(emp, day, ci_t, co_t)
            AttendanceRecord.objects.update_or_create(
                employee=emp, date=day,
                defaults={'check_in': ci_t, 'check_out': co_t, 'status': status,
                          'hours_worked': hours, 'created_by': request.user})
        messages.success(request, f'Attendance saved for {day:%Y-%m-%d}.')
        return redirect(f"{reverse('hr:attendance_grid')}?date={day:%Y-%m-%d}&location={location}")

    existing = {r.employee_id: r for r in AttendanceRecord.objects.filter(date=day)}
    rows = []
    for emp in employees:
        rec = existing.get(emp.pk)
        preview_status, _ph = derive_status(emp, day, rec.check_in if rec else None,
                                            rec.check_out if rec else None)
        locked = preview_status in ('leave', 'holiday', 'weekend')
        is_wfh = WFHRecord.objects.filter(employee=emp, start_date=day, end_date=day).exists()
        rows.append({'employee': emp, 'record': rec,
                     'status': rec.status if rec else preview_status, 'locked': locked,
                     'is_wfh': is_wfh})
    return render(request, 'hr/attendance_grid.html', {
        'day': day, 'rows': rows, 'location': location})


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
            obj.expected_in_by = form.cleaned_data.get('expected_in_by') or obj.expected_in_by
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
    # Office / Site segregation (office tab = office + unassigned).
    location = request.GET.get('location') or 'office'
    emp_qs = Employee.objects.filter(is_active=True)
    if location == 'site':
        emp_qs = emp_qs.filter(work_location='site')
    else:
        location = 'office'
        emp_qs = emp_qs.exclude(work_location='site')
    employees = list(emp_qs.order_by('full_name'))
    days, rows, weekend_dates = build_matrix(employees, start, end, with_weekend_dates=True)
    prev_anchor = start - timedelta(days=1)
    next_anchor = end + timedelta(days=1)
    return render(request, 'hr/attendance_matrix.html', {
        'period': period, 'anchor': anchor, 'start': start, 'end': end,
        'days': days, 'rows': rows, 'location': location,
        'prev_anchor': prev_anchor, 'next_anchor': next_anchor,
        'today': timezone.now().date(),
        'leave_types': LeaveType.objects.filter(is_active=True).order_by('name'),
        'weekend_dates': weekend_dates,
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

    # Certificate-required leave (e.g. Sick) can't be granted from the one-click
    # grid action — there's nowhere to attach the file. Send them to Add Leave.
    if leave_type.requires_medical_certificate:
        return JsonResponse(
            {'error': f'{leave_type.name} needs a medical certificate — use the Add Leave form.'},
            status=400)

    # Guard against double-booking: a day already inside any leave (incl. a
    # multi-day record showing stale 'present' in the matrix) must not get a
    # second overlapping LeaveRecord that would double-count the balance.
    if LeaveRecord.objects.filter(employee=employee, start_date__lte=day, end_date__gte=day).exists():
        return JsonResponse({'error': 'Already on leave that day.'}, status=400)

    with transaction.atomic():
        lr = LeaveRecord.objects.create(
            employee=employee, leave_type=leave_type,
            start_date=day, end_date=day, created_by=request.user)
        # Don't blank check_in/check_out — if the day already had clock times,
        # preserving them keeps the mark->unmark round trip lossless (unmark
        # re-derives back to present). hours_worked is nulled (no hours on leave).
        AttendanceRecord.objects.update_or_create(
            employee=employee, date=day,
            defaults={'status': 'leave', 'hours_worked': None, 'created_by': request.user})
    return JsonResponse({'ok': True, 'status': 'leave', 'leave_record_id': lr.pk})


@login_required
@require_POST
def attendance_unmark_leave(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        raise PermissionDenied
    try:
        lr_id = int(json.loads(request.body or '{}')['leave_record_id'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Bad payload'}, status=400)

    lr = LeaveRecord.objects.filter(pk=lr_id).first()
    if lr is None:
        return JsonResponse({'error': 'Not found'}, status=404)
    if lr.start_date != lr.end_date:
        return JsonResponse({'error': 'Part of a multi-day leave — edit from the leave summary.'}, status=400)

    emp, emp_id, day = lr.employee, lr.employee_id, lr.start_date
    with transaction.atomic():
        lr.delete()
        ar = AttendanceRecord.objects.filter(employee_id=emp_id, date=day).first()
        if ar and (ar.check_in or ar.check_out):
            status, hours = derive_status(ar.employee, day, ar.check_in, ar.check_out)
            AttendanceRecord.objects.filter(pk=ar.pk).update(status=status, hours_worked=hours)
            new_status = status
        else:
            if ar:
                ar.delete()  # leave-only row -> restore blank/derived cell
            new_status = display_status_no_record(day, emp)
    return JsonResponse({'ok': True, 'status': new_status})


# ─── WFH Records ──────────────────────────────────────────────────────────────


class WFHRecordListView(AdminRequiredMixin, ListView):
    model = WFHRecord
    template_name = 'hr/wfhrecord_list.html'
    context_object_name = 'wfh_records'
    paginate_by = 25

    def get_queryset(self):
        return WFHRecord.objects.select_related('employee').all()


class WFHRecordCreateView(AdminRequiredMixin, CreateView):
    model = WFHRecord
    form_class = WFHRecordForm
    template_name = 'hr/wfhrecord_form.html'
    success_url = reverse_lazy('hr:wfh_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'WFH recorded.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Record WFH'
        ctx['button_text'] = 'Save WFH'
        return ctx


class WFHRecordDeleteView(AdminRequiredMixin, DeleteView):
    model = WFHRecord
    template_name = 'hr/wfhrecord_confirm_delete.html'
    success_url = reverse_lazy('hr:wfh_list')
