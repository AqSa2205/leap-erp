from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from decimal import Decimal, InvalidOperation

from .models import ExchangeRate, CostingSheet, CostingSection, CostingLineItem
from .forms import (
    CostingSheetForm, CostingSectionForm, CostingLineItemForm,
    ExchangeRateForm, CostingFilterForm,
)


class CostingPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_queryset(self):
        queryset = CostingSheet.objects.select_related('project', 'created_by').all()
        user = self.request.user
        if user.is_admin_user:
            return queryset
        elif user.is_manager_user:
            return queryset.filter(
                Q(created_by=user) |
                Q(project__region=user.region)
            )
        else:
            return queryset.filter(created_by=user)


# ─── Costing Sheet CRUD ───────────────────────────────────────

class CostingListView(CostingPermissionMixin, ListView):
    model = CostingSheet
    template_name = 'costing/costing_list.html'
    context_object_name = 'sheets'
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
                Q(customer_reference__icontains=search) |
                Q(project__project_name__icontains=search)
            )
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by('-updated_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = CostingFilterForm(self.request.GET)
        context['total_count'] = self.get_queryset().count()
        return context


class CostingCreateView(LoginRequiredMixin, CreateView):
    model = CostingSheet
    form_class = CostingSheetForm
    template_name = 'costing/costing_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Costing sheet created successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('costing:detail', kwargs={'pk': self.object.pk})


class CostingDetailView(CostingPermissionMixin, DetailView):
    model = CostingSheet
    template_name = 'costing/costing_detail.html'
    context_object_name = 'sheet'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sheet = self.object
        sections = sheet.sections.prefetch_related('line_items').all()
        context['sections'] = sections
        context['section_form'] = CostingSectionForm()
        context['lineitem_form'] = CostingLineItemForm()
        context['exchange_rates'] = ExchangeRate.objects.all()
        return context


class CostingUpdateView(CostingPermissionMixin, UpdateView):
    model = CostingSheet
    form_class = CostingSheetForm
    template_name = 'costing/costing_form.html'

    def test_func(self):
        sheet = self.get_object()
        user = self.request.user
        if user.is_admin_user:
            return True
        return sheet.created_by == user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, 'Costing sheet updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('costing:detail', kwargs={'pk': self.object.pk})


class CostingDeleteView(CostingPermissionMixin, DeleteView):
    model = CostingSheet
    template_name = 'costing/costing_confirm_delete.html'
    success_url = reverse_lazy('costing:list')

    def test_func(self):
        user = self.request.user
        if user.is_admin_user:
            return True
        sheet = self.get_object()
        return sheet.created_by == user

    def form_valid(self, form):
        messages.success(self.request, 'Costing sheet deleted successfully.')
        return super().form_valid(form)


# ─── Section CRUD ─────────────────────────────────────────────

class SectionCreateView(LoginRequiredMixin, CreateView):
    model = CostingSection
    form_class = CostingSectionForm
    template_name = 'costing/section_form.html'

    def form_valid(self, form):
        sheet = get_object_or_404(CostingSheet, pk=self.kwargs['sheet_pk'])
        form.instance.costing_sheet = sheet
        messages.success(self.request, 'Section added successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('costing:detail', kwargs={'pk': self.kwargs['sheet_pk']})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sheet'] = get_object_or_404(CostingSheet, pk=self.kwargs['sheet_pk'])
        return context


class SectionUpdateView(LoginRequiredMixin, UpdateView):
    model = CostingSection
    form_class = CostingSectionForm
    template_name = 'costing/section_form.html'

    def get_success_url(self):
        return reverse('costing:detail', kwargs={'pk': self.object.costing_sheet.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Section updated successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sheet'] = self.object.costing_sheet
        return context


class SectionDeleteView(LoginRequiredMixin, DeleteView):
    model = CostingSection
    template_name = 'costing/costing_confirm_delete.html'

    def get_success_url(self):
        return reverse('costing:detail', kwargs={'pk': self.object.costing_sheet.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Section deleted successfully.')
        return super().form_valid(form)


# ─── Line Item CRUD ───────────────────────────────────────────

class LineItemCreateView(LoginRequiredMixin, CreateView):
    model = CostingLineItem
    form_class = CostingLineItemForm
    template_name = 'costing/lineitem_form.html'

    def form_valid(self, form):
        section = get_object_or_404(CostingSection, pk=self.kwargs['section_pk'])
        form.instance.section = section
        messages.success(self.request, 'Line item added successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        section = get_object_or_404(CostingSection, pk=self.kwargs['section_pk'])
        return reverse('costing:detail', kwargs={'pk': section.costing_sheet.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = get_object_or_404(CostingSection, pk=self.kwargs['section_pk'])
        return context


class LineItemUpdateView(LoginRequiredMixin, UpdateView):
    model = CostingLineItem
    form_class = CostingLineItemForm
    template_name = 'costing/lineitem_form.html'

    def get_success_url(self):
        return reverse('costing:detail', kwargs={'pk': self.object.section.costing_sheet.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Line item updated successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = self.object.section
        return context


class LineItemDeleteView(LoginRequiredMixin, DeleteView):
    model = CostingLineItem
    template_name = 'costing/costing_confirm_delete.html'

    def get_success_url(self):
        return reverse('costing:detail', kwargs={'pk': self.object.section.costing_sheet.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Line item deleted successfully.')
        return super().form_valid(form)


# ─── Exchange Rates ───────────────────────────────────────────

class ExchangeRateListView(LoginRequiredMixin, ListView):
    model = ExchangeRate
    template_name = 'costing/exchange_rates.html'
    context_object_name = 'rates'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = ExchangeRateForm()
        return context


class ExchangeRateCreateView(LoginRequiredMixin, CreateView):
    model = ExchangeRate
    form_class = ExchangeRateForm
    template_name = 'costing/exchange_rates.html'
    success_url = reverse_lazy('costing:exchange_rates')

    def form_valid(self, form):
        messages.success(self.request, 'Exchange rate added.')
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, 'Error adding exchange rate.')
        return redirect('costing:exchange_rates')


class ExchangeRateUpdateView(LoginRequiredMixin, UpdateView):
    model = ExchangeRate
    form_class = ExchangeRateForm
    template_name = 'costing/exchange_rate_form.html'
    success_url = reverse_lazy('costing:exchange_rates')

    def form_valid(self, form):
        messages.success(self.request, 'Exchange rate updated.')
        return super().form_valid(form)


class ExchangeRateDeleteView(LoginRequiredMixin, DeleteView):
    model = ExchangeRate
    template_name = 'costing/costing_confirm_delete.html'
    success_url = reverse_lazy('costing:exchange_rates')

    def form_valid(self, form):
        messages.success(self.request, 'Exchange rate deleted.')
        return super().form_valid(form)


# ─── AJAX Inline Editing ──────────────────────────────────────

@login_required
@require_POST
def ajax_update_sheet_params(request, pk):
    sheet = get_object_or_404(CostingSheet, pk=pk)
    field = request.POST.get('field')
    value = request.POST.get('value', '').strip()

    allowed = ('margin', 'ddp_rate', 'discount_rate', 'output_currency')
    if field not in allowed:
        return JsonResponse({'error': 'Invalid field'}, status=400)

    if field == 'output_currency':
        sheet.output_currency = value
    else:
        try:
            sheet.__setattr__(field, Decimal(value))
        except (InvalidOperation, ValueError):
            return JsonResponse({'error': 'Invalid number'}, status=400)

    sheet.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def ajax_update_exchange_rate(request, pk):
    rate = get_object_or_404(ExchangeRate, pk=pk)
    value = request.POST.get('value', '').strip()
    try:
        rate.rate_to_usd = Decimal(value)
    except (InvalidOperation, ValueError):
        return JsonResponse({'error': 'Invalid number'}, status=400)
    rate.save()
    return JsonResponse({'ok': True})


# ─── Excel Export ─────────────────────────────────────────────

def costing_export_excel(request, pk):
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill, numbers
    except ImportError:
        messages.error(request, 'openpyxl is required for Excel export.')
        return redirect('costing:detail', pk=pk)

    sheet = get_object_or_404(CostingSheet, pk=pk)
    sections = sheet.sections.prefetch_related('line_items').all()
    rates = {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'BOM'[:31]

    # Styles
    thin = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'),
    )
    hdr_white = Font(bold=True, color='FFFFFF', size=9)
    section_font = Font(bold=True, size=10)
    num_fmt = '#,##0.00'
    supplier_fill = PatternFill(start_color='FFF8E1', end_color='FFF8E1', fill_type='solid')
    usd_fill = PatternFill(start_color='E8F5E9', end_color='E8F5E9', fill_type='solid')
    quoted_usd_fill = PatternFill(start_color='E3F2FD', end_color='E3F2FD', fill_type='solid')
    quoted_local_fill = PatternFill(start_color='FCE4EC', end_color='FCE4EC', fill_type='solid')
    hdr_supplier = PatternFill(start_color='F9A825', end_color='F9A825', fill_type='solid')
    hdr_usd = PatternFill(start_color='43A047', end_color='43A047', fill_type='solid')
    hdr_quoted_usd = PatternFill(start_color='1E88E5', end_color='1E88E5', fill_type='solid')
    hdr_quoted_local = PatternFill(start_color='C62828', end_color='C62828', fill_type='solid')
    hdr_dark = PatternFill(start_color='212529', end_color='212529', fill_type='solid')

    # Row 2: Margin / DDP / Discount + Exchange Rates
    ws['L2'] = 'Margin'
    ws['M2'] = float(sheet.margin)
    ws['O2'] = 'EXCHANGE RATES'
    ws['O2'].font = Font(bold=True)
    ws['L3'] = 'DDP'
    ws['M3'] = float(sheet.ddp_rate)
    ws['L4'] = 'Discount'
    ws['M4'] = float(sheet.discount_rate)

    rate_row = 2
    for code, rate in sorted(rates.items()):
        ws.cell(row=rate_row, column=15, value=code).font = Font(bold=True)  # O
        ws.cell(row=rate_row, column=16, value=float(rate))  # P
        rate_row += 1

    # Row 5: Project info
    ws['A5'] = 'Project:'
    ws['B5'] = sheet.project.project_name if sheet.project else 'N/A'
    ws['C5'] = 'LN Ref:'
    ws['D5'] = sheet.project.proposal_reference if sheet.project else 'N/A'
    ws['F5'] = 'Cust Ref:'
    ws['G5'] = sheet.customer_reference or 'N/A'

    # Row 7: Customer / End User
    ws['A7'] = 'Customer:'
    ws['C7'] = 'End User:'
    ws['F7'] = 'Date:'

    # Row 8: Title
    ws['A8'] = 'BILL OF MATERIAL - COMMERCIAL'
    ws['A8'].font = Font(bold=True, size=12)

    # Row 9-10: Column group headers
    # Group header row
    r = 10
    group_cols = [
        (1, 6, 'Item Details', hdr_dark),
        (7, 8, f'Final Price ({sheet.output_currency})', hdr_dark),
        (9, 10, '', hdr_dark),
        (11, 16, 'INFORMATION FROM SUPPLIERS', hdr_supplier),
        (17, 19, 'PRICE IN USD', hdr_usd),
        (20, 22, 'QUOTED PRICE USD', hdr_quoted_usd),
        (23, 27, f'QUOTED PRICE {sheet.output_currency}', hdr_quoted_local),
    ]
    for start, end, label, fill in group_cols:
        if start != end:
            ws.merge_cells(start_row=r, start_column=start, end_row=r, end_column=end)
        cell = ws.cell(row=r, column=start, value=label)
        cell.font = hdr_white
        cell.fill = fill
        cell.alignment = Alignment(horizontal='center')
        for c in range(start, end + 1):
            ws.cell(row=r, column=c).fill = fill

    # Column headers row
    r = 11
    headers = [
        'Item No', 'Description', 'Make', 'Model', 'Qty', 'Unit',
        'Unit Price', 'Total Price', 'Vendor', 'System',
        'Cur', 'Base Price', 'Base Total', 'Disc', 'Disc Price', 'Disc Total',
        'Rate', 'Unit Price', 'Total Price',
        'Margin', 'Unit Price', 'Total Price',
        'Unit Price', 'DDP Rate', 'DDP Amount', 'Unit Inc DDP', 'Total Price',
    ]
    col_fills = (
        [hdr_dark]*10 +
        [hdr_supplier]*6 +
        [hdr_usd]*3 +
        [hdr_quoted_usd]*3 +
        [hdr_quoted_local]*5
    )
    for col, (h, fill) in enumerate(zip(headers, col_fills), 1):
        cell = ws.cell(row=r, column=col, value=h)
        cell.font = hdr_white
        cell.fill = fill
        cell.border = thin
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    # Data rows
    row = 12
    data_fills = (
        [None]*10 +
        [supplier_fill]*6 +
        [usd_fill]*3 +
        [quoted_usd_fill]*3 +
        [quoted_local_fill]*5
    )

    for section in sections:
        # Section header
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=27)
        cell = ws.cell(row=row, column=1, value=f"{section.section_number}  {section.title}")
        cell.font = section_font
        cell.fill = PatternFill(start_color='F0F0F0', end_color='F0F0F0', fill_type='solid')
        row += 1

        for item in section.line_items.all():
            values = [
                item.item_number,
                item.description,
                item.make,
                item.model_number,
                float(item.quantity),
                item.unit,
                round(float(item.final_unit_price), 2),
                round(float(item.final_total_price), 2),
                item.vendor_name,
                item.system,
                # Supplier info
                item.supplier_currency,
                round(float(item.base_price), 2),
                round(float(item.base_total), 2),
                float(item.sheet.discount_rate),
                round(float(item.discounted_price), 2),
                round(float(item.discounted_total), 2),
                # USD
                float(item.exchange_rate),
                round(float(item.cost_usd), 2),
                round(float(item.cost_usd_total), 2),
                # Quoted USD
                float(item.sheet.margin),
                round(float(item.quoted_usd), 2),
                round(float(item.quoted_usd_total), 2),
                # Quoted local
                round(float(item.quoted_local), 2),
                float(item.sheet.ddp_rate),
                round(float(item.ddp_amount), 2),
                round(float(item.final_unit_price), 2),
                round(float(item.final_total_price), 2),
            ]
            for col, val in enumerate(values, 1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.border = thin
                if data_fills[col - 1]:
                    cell.fill = data_fills[col - 1]
                if isinstance(val, float) and col not in (5,):
                    cell.number_format = num_fmt
            row += 1

        # Subtotal
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        cell = ws.cell(row=row, column=1, value=f"Sub-Total")
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='right')
        st_cell = ws.cell(row=row, column=8, value=round(float(section.subtotal), 2))
        st_cell.font = Font(bold=True)
        st_cell.number_format = num_fmt
        row += 1

    # Grand total
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    cell = ws.cell(row=row, column=1, value=f'GRAND TOTAL ({sheet.output_currency})')
    cell.font = Font(bold=True, size=12, color='FFFFFF')
    cell.fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    cell.alignment = Alignment(horizontal='right')
    gt_cell = ws.cell(row=row, column=8, value=round(float(sheet.grand_total), 2))
    gt_cell.font = Font(bold=True, size=12, color='FFFFFF')
    gt_cell.fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    gt_cell.number_format = num_fmt

    # Column widths
    widths = {
        'A': 10, 'B': 45, 'C': 14, 'D': 14, 'E': 7, 'F': 7,
        'G': 14, 'H': 16, 'I': 14, 'J': 12,
        'K': 6, 'L': 12, 'M': 14, 'N': 7, 'O': 12, 'P': 14,
        'Q': 7, 'R': 12, 'S': 14,
        'T': 7, 'U': 12, 'V': 14,
        'W': 12, 'X': 8, 'Y': 12, 'Z': 14, 'AA': 16,
    }
    for col_letter, w in widths.items():
        ws.column_dimensions[col_letter].width = w

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f"{sheet.title.replace(' ', '_')}_BOM.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response
