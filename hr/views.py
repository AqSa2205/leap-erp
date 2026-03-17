from django.shortcuts import render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Q, Count, Sum
from django.http import HttpResponse
from datetime import datetime, date
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

from .models import Employee, Asset
from .forms import (
    EmployeeForm, EmployeeFilterForm, EmployeeImportForm,
    AssetForm, AssetFilterForm, AssetImportForm,
)


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_super_admin_user or self.request.user.is_admin_user


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
