from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.template.loader import render_to_string
from decimal import Decimal, InvalidOperation

from .models import ExchangeRate, CostingSheet, CostingSection, CostingLineItem
from notifications.services import notify_users


def _conversion_rate(output_currency, rates_dict):
    """Return factor to convert SAR values to the given output currency."""
    if output_currency == 'SAR':
        return Decimal('1')
    sar_rate = rates_dict.get('SAR')
    target_rate = rates_dict.get(output_currency)
    if sar_rate and target_rate:
        return (sar_rate / target_rate).quantize(Decimal('0.000001'))
    return Decimal('1')
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
        response = super().form_valid(form)

        # Notify managers about new costing sheet
        from accounts.models import User, Role
        managers = User.objects.filter(role__name=Role.MANAGER, is_active=True)
        notify_users(
            recipients=managers,
            verb='created a new costing sheet',
            actor=self.request.user,
            target=self.object,
            target_url=reverse('costing:detail', kwargs={'pk': self.object.pk}),
            description=f'New costing sheet: {self.object.title}',
            level='info',
        )
        return response

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

        # Pre-load exchange rates and build rates dict (single query)
        exchange_rates = list(ExchangeRate.objects.all())
        rates_dict = {r.currency_code: r.rate_to_usd for r in exchange_rates}

        # Load sections with item counts + precompute section subtotals and
        # sheet totals in a single pass (items are NOT sent to the template —
        # they are lazy-loaded via AJAX when a section is expanded).
        sections = list(sheet.sections.prefetch_related('line_items').all())

        sheet_totals = {
            'grand_total': Decimal('0'),
            'total_cost': Decimal('0'),
            'total_base_cost': Decimal('0'),
            'total_discount': Decimal('0'),
            'total_margin_amount': Decimal('0'),
            'total_shipping_amount': Decimal('0'),
            'total_customs_amount': Decimal('0'),
            'total_finances_amount': Decimal('0'),
            'total_installation_amount': Decimal('0'),
        }
        for section in sections:
            section_sub = {
                'subtotal': Decimal('0'),
                'total_cost': Decimal('0'),
                'base_unit_cost': Decimal('0'),
                'discount': Decimal('0'),
                'unit_cost': Decimal('0'),
                'base_unit_price': Decimal('0'),
                'base_total_price': Decimal('0'),
            }
            item_count = 0
            for item in section.line_items.all():
                item.set_exchange_rates_cache(rates_dict)
                item.set_sheet_cache(sheet)
                item_count += 1
                qty = item.quantity
                section_sub['subtotal'] += item.final_total_price
                section_sub['total_cost'] += item.total_cost
                section_sub['base_unit_cost'] += item.base_unit_cost * qty
                section_sub['discount'] += item.discount_amount * qty
                section_sub['unit_cost'] += item.unit_cost * qty
                section_sub['base_unit_price'] += item.base_unit_price * qty
                section_sub['base_total_price'] += item.base_total_price

                ucs = item.unit_cost_sar
                sheet_totals['grand_total'] += item.final_total_price
                sheet_totals['total_cost'] += item.total_cost
                sheet_totals['total_base_cost'] += item.base_unit_cost * qty
                sheet_totals['total_discount'] += item.discount_amount * qty
                sheet_totals['total_margin_amount'] += item.base_total_price - item.total_cost
                sheet_totals['total_shipping_amount'] += ucs * item.effective_shipping_pct * qty
                sheet_totals['total_customs_amount'] += ucs * item.effective_customs_pct * qty
                sheet_totals['total_finances_amount'] += ucs * item.effective_finances_pct * qty
                sheet_totals['total_installation_amount'] += ucs * item.effective_installation_pct * qty

            section._subtotals = {k: v.quantize(Decimal('0.01')) for k, v in section_sub.items()}
            section.item_count_cached = item_count

        sheet._totals = {k: v.quantize(Decimal('0.01')) for k, v in sheet_totals.items()}

        # Compute SAR → output_currency conversion rate
        conversion_rate = _conversion_rate(sheet.output_currency, rates_dict)

        context['sections'] = sections
        context['section_form'] = CostingSectionForm()
        context['lineitem_form'] = CostingLineItemForm()
        context['exchange_rates'] = exchange_rates
        context['conversion_rate'] = conversion_rate
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
        old_status = CostingSheet.objects.get(pk=self.object.pk).status
        messages.success(self.request, 'Costing sheet updated successfully.')
        response = super().form_valid(form)

        # Notify on draft → final
        if old_status == 'draft' and self.object.status == 'final':
            from accounts.models import User, Role
            recipients = set(User.objects.filter(role__name=Role.ADMIN, is_active=True))
            if self.object.project and self.object.project.owner:
                recipients.add(self.object.project.owner)
            notify_users(
                recipients=recipients,
                verb=f'finalized costing sheet "{self.object.title}"',
                actor=self.request.user,
                target=self.object,
                target_url=reverse('costing:detail', kwargs={'pk': self.object.pk}),
                description=f'Costing sheet "{self.object.title}" has been finalized.',
                level='success',
                send_email=True,
            )
        return response

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


# ─── AJAX Section Items (lazy-load) ──────────────────────────

@login_required
def ajax_section_items(request, pk):
    """Return HTML fragment with a section's line items for lazy-loading."""
    section = get_object_or_404(CostingSection, pk=pk)
    sheet = section.costing_sheet
    items = list(section.line_items.all())

    exchange_rates = list(ExchangeRate.objects.all())
    rates_dict = {r.currency_code: r.rate_to_usd for r in exchange_rates}

    for item in items:
        item.set_exchange_rates_cache(rates_dict)
        item.set_sheet_cache(sheet)

    conversion_rate = _conversion_rate(sheet.output_currency, rates_dict)

    html = render_to_string('costing/_section_items.html', {
        'section': section,
        'items': items,
        'exchange_rates': exchange_rates,
        'conversion_rate': conversion_rate,
        'output_currency': sheet.output_currency,
    }, request=request)
    return HttpResponse(html)


# ─── AJAX Inline Editing ──────────────────────────────────────

@login_required
@require_POST
def ajax_update_sheet_params(request, pk):
    sheet = get_object_or_404(CostingSheet, pk=pk)
    field = request.POST.get('field')
    value = request.POST.get('value', '').strip()

    allowed = ('margin', 'discount_rate', 'shipping_rate', 'customs_rate', 'finances_rate', 'installation_rate', 'output_currency')
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


@login_required
@require_POST
def ajax_update_item_margin(request, pk):
    item = get_object_or_404(CostingLineItem, pk=pk)
    value = request.POST.get('margin', '').strip()
    if not value:
        item.margin = None  # Clear to use sheet margin
    else:
        try:
            item.margin = Decimal(value)
        except (InvalidOperation, ValueError):
            return JsonResponse({'error': 'Invalid number'}, status=400)
    item.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def ajax_update_item_field(request, pk):
    item = get_object_or_404(CostingLineItem, pk=pk)
    field = request.POST.get('field', '').strip()
    value = request.POST.get('value', '').strip()

    allowed_fields = ('base_unit_cost', 'discount_pct', 'shipping_pct', 'customs_pct', 'finances_pct', 'installation_pct', 'margin', 'supplier_currency')
    if field not in allowed_fields:
        return JsonResponse({'error': 'Invalid field'}, status=400)

    if field == 'supplier_currency':
        item.supplier_currency = value if value else 'SAR'
    elif field == 'margin' and not value:
        item.margin = None
    else:
        try:
            setattr(item, field, Decimal(value) if value else Decimal('0'))
        except (InvalidOperation, ValueError):
            return JsonResponse({'error': 'Invalid number'}, status=400)

    item.save()

    # Recompute values with caches and return them
    rates_dict = {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}
    item.set_exchange_rates_cache(rates_dict)
    item.set_sheet_cache(item.section.costing_sheet)

    return JsonResponse({
        'ok': True,
        'computed': {
            'unit_cost': str(item.unit_cost),
            'total_cost': str(item.total_cost),
            'base_unit_price': str(item.base_unit_price),
            'base_total_price': str(item.base_total_price),
            'final_unit_price': str(item.final_unit_price),
            'final_total_price': str(item.final_total_price),
        },
    })


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

    # Inject caches into all line items to avoid N+1 queries
    for section in sections:
        for item in section.line_items.all():
            item.set_exchange_rates_cache(rates)
            item.set_sheet_cache(sheet)

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
    # Grey for cost columns (second half)
    cost_fill = PatternFill(start_color='D0D0D0', end_color='D0D0D0', fill_type='solid')
    # Darker grey header for cost columns
    hdr_cost = PatternFill(start_color='495057', end_color='495057', fill_type='solid')
    # Dark header for info columns (first half)
    hdr_dark = PatternFill(start_color='212529', end_color='212529', fill_type='solid')

    # Row 2: Sheet parameters + Exchange Rates
    ws['L2'] = 'Margin'
    ws['M2'] = float(sheet.margin)
    ws['L3'] = 'Discount'
    ws['M3'] = float(sheet.discount_rate)
    ws['L4'] = 'Shipping'
    ws['M4'] = float(sheet.shipping_rate)
    ws['L5'] = 'Customs'
    ws['M5'] = float(sheet.customs_rate)
    ws['L6'] = 'Finances'
    ws['M6'] = float(sheet.finances_rate)
    ws['L7'] = 'Installation'
    ws['M7'] = float(sheet.installation_rate)

    ws['O2'] = 'EXCHANGE RATES'
    ws['O2'].font = Font(bold=True)
    rate_row = 3
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

    # Column headers row
    r = 10
    headers = [
        'Item No', 'Description', 'Make', 'Model', 'Qty', 'Unit',
        'Vendor', 'Unit Price', 'Total Price',
        'Currency', 'Base Unit Cost', 'Discount', 'Unit Cost', 'Total Cost',
        'Margin', 'Base Unit Price', 'Base Total Price',
        'Shipping %', 'Customs %', 'Finances %', 'Installation %',
        'Unit Price', 'Total Price',
    ]
    col_fills = (
        [hdr_dark]*9 +
        [hdr_cost]*14
    )
    for col, (h, fill) in enumerate(zip(headers, col_fills), 1):
        cell = ws.cell(row=r, column=col, value=h)
        cell.font = hdr_white
        cell.fill = fill
        cell.border = thin
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    # Data rows
    row = 11
    data_fills = (
        [None]*9 +
        [cost_fill]*14
    )

    for section in sections:
        # Section header
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=23)
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
                item.vendor_name,
                # Unit Price and Total Price (calculated final)
                round(float(item.final_unit_price), 2),
                round(float(item.final_total_price), 2),
                # Cost breakdown (grey section)
                item.supplier_currency,
                round(float(item.base_unit_cost), 2),
                round(float(item.discount), 2),
                round(float(item.unit_cost), 2),
                round(float(item.total_cost), 2),
                float(item.effective_margin),
                round(float(item.base_unit_price), 2),
                round(float(item.base_total_price), 2),
                float(item.effective_shipping_pct),
                float(item.effective_customs_pct),
                float(item.effective_finances_pct),
                float(item.effective_installation_pct),
                # Final Unit Price and Total Price at end of cost section
                round(float(item.final_unit_price), 2),
                round(float(item.final_total_price), 2),
            ]
            for col, val in enumerate(values, 1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.border = thin
                if col <= len(data_fills) and data_fills[col - 1]:
                    cell.fill = data_fills[col - 1]
                if isinstance(val, float) and col not in (5,):
                    cell.number_format = num_fmt
            row += 1

        # Subtotal
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        cell = ws.cell(row=row, column=1, value=f"Sub-Total")
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='right')
        st_cell = ws.cell(row=row, column=9, value=round(float(section.subtotal), 2))
        st_cell.font = Font(bold=True)
        st_cell.number_format = num_fmt
        row += 1

    # Grand total
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
    cell = ws.cell(row=row, column=1, value=f'GRAND TOTAL ({sheet.output_currency})')
    cell.font = Font(bold=True, size=12, color='FFFFFF')
    cell.fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    cell.alignment = Alignment(horizontal='right')
    gt_cell = ws.cell(row=row, column=9, value=round(float(sheet.grand_total), 2))
    gt_cell.font = Font(bold=True, size=12, color='FFFFFF')
    gt_cell.fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    gt_cell.number_format = num_fmt

    # Column widths
    widths = {
        'A': 10, 'B': 40, 'C': 12, 'D': 12, 'E': 6, 'F': 6,
        'G': 12, 'H': 12, 'I': 12,  # Vendor, Unit Price, Total Price
        'J': 8,  # Currency
        'K': 12, 'L': 10, 'M': 10, 'N': 12,  # Base Unit Cost, Discount, Unit Cost, Total Cost
        'O': 8, 'P': 12, 'Q': 12,  # Margin, Base Unit Price, Base Total Price
        'R': 10, 'S': 10, 'T': 10, 'U': 10,  # Percentages
        'V': 12, 'W': 12,  # Final Unit Price, Total Price
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


# ─── PDF Summary Export ────────────────────────────────────────

def costing_export_pdf(request, pk):
    """Export a professional PDF summary matching the commercial offer format"""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch, mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
        from datetime import datetime
    except ImportError:
        messages.error(request, 'reportlab is required for PDF export. Install with: pip install reportlab')
        return redirect('costing:detail', pk=pk)

    sheet = get_object_or_404(CostingSheet, pk=pk)
    sections = sheet.sections.prefetch_related('line_items').all()

    # Inject caches into all line items to avoid N+1 queries
    rates_dict = {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}
    for section in sections:
        for item in section.line_items.all():
            item.set_exchange_rates_cache(rates_dict)
            item.set_sheet_cache(sheet)

    # Create response
    response = HttpResponse(content_type='application/pdf')
    filename = f"{sheet.title.replace(' ', '_')}_Commercial_Offer.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Create PDF document
    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=15*mm,
        leftMargin=15*mm,
        topMargin=15*mm,
        bottomMargin=15*mm,
    )

    elements = []
    styles = getSampleStyleSheet()

    # Colors
    GREEN_HEADER = colors.HexColor('#92D050')  # Light green for section headers
    YELLOW_TITLE = colors.HexColor('#FFD966')  # Yellow for title bar
    DARK_GREEN = colors.HexColor('#548235')    # Dark green for main header
    BORDER_COLOR = colors.HexColor('#000000')

    # ─── HEADER SECTION ───
    # Company logo text (since we don't have actual logo file)
    logo_style = ParagraphStyle('Logo', fontSize=18, textColor=colors.HexColor('#C41E3A'), fontName='Helvetica-Bold')

    # Header info table
    project_name = sheet.project.project_name if sheet.project else sheet.title
    region_name = sheet.project.region.name if sheet.project and sheet.project.region else 'N/A'
    region_code = sheet.project.region.code if sheet.project and sheet.project.region else ''
    ln_ref = sheet.project.proposal_reference if sheet.project else ''
    cust_ref = sheet.customer_reference or (sheet.project.client_rfq_reference if sheet.project else '')
    current_date = datetime.now().strftime('%d-%b-%y')

    header_data = [
        [Paragraph('<b><font color="#C41E3A" size="16">LEAP</font></b><br/><font size="8" color="#C41E3A">NETWORKS</font>', styles['Normal']),
         '', '', '', '', ''],
        ['', '', '', '', '', ''],
        [Paragraph(f'<b>Project:</b>', styles['Normal']),
         Paragraph(f'{project_name}', styles['Normal']),
         '',
         Paragraph(f'<b>Sales Office:</b>', styles['Normal']),
         Paragraph(f'LN-{region_name}', styles['Normal']), ''],
        [Paragraph(f'<b>Cust. Ref:</b>', styles['Normal']),
         Paragraph(f'{cust_ref}', styles['Normal']),
         '',
         Paragraph(f'<b>Contact:</b>', styles['Normal']),
         Paragraph(f'{sheet.created_by.get_full_name() if sheet.created_by else ""}', styles['Normal']), ''],
        [Paragraph(f'<b>LN Ref:</b>', styles['Normal']),
         Paragraph(f'{ln_ref}', styles['Normal']),
         '',
         Paragraph(f'<b>Email:</b>', styles['Normal']),
         Paragraph(f'{sheet.created_by.email if sheet.created_by else ""}', styles['Normal']), ''],
        [Paragraph(f'<b>Date:</b>', styles['Normal']),
         Paragraph(f'{current_date}', styles['Normal']),
         '',
         Paragraph(f'<b>Page:</b>', styles['Normal']),
         Paragraph(f'Page 1 of 1', styles['Normal']), ''],
    ]

    header_table = Table(header_data, colWidths=[60, 180, 20, 70, 150, 20])
    header_table.setStyle(TableStyle([
        ('SPAN', (0, 0), (1, 1)),  # Logo spans
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 5*mm))

    # ─── TITLE BAR ───
    title_data = [[Paragraph(f'<b>COMMERCIAL OFFER SUMMARY</b>',
                             ParagraphStyle('Title', fontSize=11, alignment=TA_CENTER, textColor=colors.black))]]
    title_table = Table(title_data, colWidths=[500])
    title_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), YELLOW_TITLE),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
    ]))
    elements.append(title_table)

    # ─── MAIN TABLE ───
    # Headers
    headers = ['Item No', 'Item Description', 'Qty', 'UOM', f'Total Price ({sheet.output_currency})']

    # Build table data
    data = [headers]

    # Main contract price row
    data.append(['A', 'MAIN - TOTAL CONTRACT PRICE', '', '', ''])

    # Scope of Supply section
    data.append(['A.1', 'SCOPE OF SUPPLY', '', '', f'{sheet.grand_total:,.2f}'])

    # Add sections and their line items
    for section in sections:
        # Section header row
        data.append([
            section.section_number,
            section.title,
            '1',
            'LOT',
            ''
        ])
        # Add line items under this section
        for item in section.line_items.all():
            data.append([
                item.item_number,
                item.description[:60] + '...' if len(item.description) > 60 else item.description,
                '',
                '',
                f'{item.final_total_price:,.2f}'
            ])

    # Create table
    col_widths = [50, 280, 40, 40, 90]
    main_table = Table(data, colWidths=col_widths)

    # Build style commands
    style_commands = [
        # Header row - Yellow background
        ('BACKGROUND', (0, 0), (-1, 0), YELLOW_TITLE),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),

        # Row A - Green background (MAIN - TOTAL CONTRACT PRICE)
        ('BACKGROUND', (0, 1), (-1, 1), GREEN_HEADER),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),

        # Row A.1 - Green background (SCOPE OF SUPPLY)
        ('BACKGROUND', (0, 2), (-1, 2), GREEN_HEADER),
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),

        # All data rows
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),   # Item No centered
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),   # Qty centered
        ('ALIGN', (3, 0), (3, -1), 'CENTER'),   # UOM centered
        ('ALIGN', (4, 0), (4, -1), 'RIGHT'),    # Price right-aligned

        # Grid and padding
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]

    # Add green background for section header rows (rows that start with section numbers like 12, 12.1, etc.)
    row_idx = 3  # Start after A, A.1 rows
    for section in sections:
        style_commands.append(('BACKGROUND', (0, row_idx), (-1, row_idx), GREEN_HEADER))
        style_commands.append(('FONTNAME', (0, row_idx), (-1, row_idx), 'Helvetica-Bold'))
        row_idx += 1  # Section row
        row_idx += section.line_items.count()  # Line item rows

    main_table.setStyle(TableStyle(style_commands))
    elements.append(main_table)

    elements.append(Spacer(1, 10*mm))

    # ─── FOOTER ───
    footer_style = ParagraphStyle('Footer', fontSize=8, alignment=TA_LEFT)
    elements.append(Paragraph('Confidential. © Leap Networks. All rights reserved.', footer_style))

    # Build PDF
    doc.build(elements)
    return response


# ─── Excel Import ────────────────────────────────────────────────

def costing_import_excel(request, pk):
    """Import line items from Excel BOQ file"""
    from openpyxl import load_workbook
    from decimal import Decimal, InvalidOperation
    import re

    sheet = get_object_or_404(CostingSheet, pk=pk)

    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')
        if not excel_file:
            messages.error(request, 'Please select an Excel file to import.')
            return redirect('costing:import_excel', pk=pk)

        try:
            wb = load_workbook(excel_file, data_only=True)
            ws = wb.active  # Use first sheet

            # Find header row (look for row with "Vendor", "Part#", "Description")
            header_row = None
            headers = {}
            for row_num in range(1, 10):
                row = [cell.value for cell in ws[row_num]]
                row_lower = [str(c).lower() if c else '' for c in row]
                if 'vendor' in row_lower or 'description' in row_lower:
                    header_row = row_num
                    for i, val in enumerate(row):
                        if val:
                            headers[str(val).lower().strip()] = i
                    break

            if not header_row:
                messages.error(request, 'Could not find header row in Excel file.')
                return redirect('costing:import_excel', pk=pk)

            # Map column indices
            col_map = {
                'vendor': headers.get('vendor', headers.get('vendor name', -1)),
                'rfp_item': headers.get('rfp item #', headers.get('rfp item', headers.get('item #', headers.get('item no', -1)))),
                'part': headers.get('part#', headers.get('part', headers.get('part no', headers.get('model', -1)))),
                'description': headers.get('description', headers.get('desc', -1)),
                'qty': headers.get('qty', headers.get('quantity', -1)),
            }

            # Process rows
            sections_created = 0
            items_created = 0
            current_section = None
            item_order = 0

            for row_num in range(header_row + 1, ws.max_row + 1):
                row = [cell.value for cell in ws[row_num]]
                if not any(row):
                    continue

                # Get values
                vendor = str(row[col_map['vendor']] or '').strip() if col_map['vendor'] >= 0 else ''
                rfp_item = str(row[col_map['rfp_item']] or '').strip() if col_map['rfp_item'] >= 0 else ''
                part = str(row[col_map['part']] or '').strip() if col_map['part'] >= 0 else ''
                description = str(row[col_map['description']] or '').strip() if col_map['description'] >= 0 else ''
                qty_val = row[col_map['qty']] if col_map['qty'] >= 0 else None

                # Skip empty rows
                if not rfp_item and not description:
                    continue

                # Parse quantity
                try:
                    qty = Decimal(str(qty_val)) if qty_val else Decimal('1')
                except (InvalidOperation, ValueError):
                    qty = Decimal('1')

                # Detect if this is a section header (has RFP Item but no Part#, or RFP Item is like "I", "II", "1", etc.)
                is_section = False
                if rfp_item and not part and description:
                    # Check if RFP item looks like a section number (Roman numerals, single digits, or main section)
                    if re.match(r'^[IVX]+$', rfp_item) or re.match(r'^\d+$', rfp_item) or len(rfp_item) <= 3:
                        is_section = True

                if is_section:
                    # Create section
                    current_section = CostingSection.objects.create(
                        costing_sheet=sheet,
                        section_number=rfp_item,
                        title=description[:255],
                        order=sections_created
                    )
                    sections_created += 1
                    item_order = 0
                else:
                    # Create line item
                    if not current_section:
                        # Create a default section if none exists
                        current_section = CostingSection.objects.create(
                            costing_sheet=sheet,
                            section_number='1',
                            title='Imported Items',
                            order=0
                        )
                        sections_created += 1

                    CostingLineItem.objects.create(
                        section=current_section,
                        item_number=rfp_item or f'{current_section.section_number}.{item_order + 1}',
                        description=description[:500] if description else 'No description',
                        model_number=part[:100] if part else '',
                        vendor_name=vendor[:255] if vendor else '',
                        quantity=qty,
                        unit='EA',
                        order=item_order
                    )
                    items_created += 1
                    item_order += 1

            messages.success(request, f'Import successful! Created {sections_created} sections and {items_created} line items.')
            return redirect('costing:detail', pk=pk)

        except Exception as e:
            messages.error(request, f'Error importing file: {str(e)}')
            return redirect('costing:import_excel', pk=pk)

    # GET request - show import form
    return render(request, 'costing/import_excel.html', {
        'sheet': sheet,
    })


# ─── Excel Import (New Sheet) ────────────────────────────────────

def costing_import_new(request):
    """Import Excel BOQ to create a new costing sheet"""
    from openpyxl import load_workbook
    from decimal import Decimal, InvalidOperation
    import re

    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')
        sheet_title = request.POST.get('title', '').strip()

        if not excel_file:
            messages.error(request, 'Please select an Excel file to import.')
            return redirect('costing:import_new')

        if not sheet_title:
            # Use filename as title
            sheet_title = excel_file.name.replace('.xlsx', '').replace('.xls', '').replace('_', ' ')

        try:
            wb = load_workbook(excel_file, data_only=True)
            ws = wb.active  # Use first sheet

            # Find header row (look for row with "Vendor", "Part#", "Description")
            header_row = None
            headers = {}
            for row_num in range(1, 15):
                row = [cell.value for cell in ws[row_num]]
                row_lower = [str(c).lower() if c else '' for c in row]
                if 'vendor' in row_lower or 'description' in row_lower:
                    header_row = row_num
                    for i, val in enumerate(row):
                        if val:
                            headers[str(val).lower().strip()] = i
                    break

            if not header_row:
                messages.error(request, 'Could not find header row in Excel file. Looking for columns: Item No, Description, Make, Model, Qty, Unit, Vendor')
                return redirect('costing:import_new')

            # Map column indices
            col_map = {
                'item_no': headers.get('item no', headers.get('item #', headers.get('rfp item #', headers.get('rfp item', headers.get('#', -1))))),
                'description': headers.get('description', headers.get('desc', -1)),
                'make': headers.get('make', headers.get('manufacturer', -1)),
                'model': headers.get('model', headers.get('part#', headers.get('part', headers.get('part no', -1)))),
                'qty': headers.get('qty', headers.get('quantity', -1)),
                'unit': headers.get('unit', headers.get('uom', -1)),
                'vendor': headers.get('vendor', headers.get('vendor name', -1)),
            }

            # Create new costing sheet
            sheet = CostingSheet.objects.create(
                title=sheet_title,
                created_by=request.user,
                status='draft'
            )

            # Process rows
            sections_created = 0
            items_created = 0
            current_section = None
            item_order = 0

            for row_num in range(header_row + 1, ws.max_row + 1):
                row = [cell.value for cell in ws[row_num]]
                if not any(row):
                    continue

                # Get values
                item_no = str(row[col_map['item_no']] or '').strip() if col_map['item_no'] >= 0 and col_map['item_no'] < len(row) else ''
                description = str(row[col_map['description']] or '').strip() if col_map['description'] >= 0 and col_map['description'] < len(row) else ''
                make = str(row[col_map['make']] or '').strip() if col_map['make'] >= 0 and col_map['make'] < len(row) else ''
                model = str(row[col_map['model']] or '').strip() if col_map['model'] >= 0 and col_map['model'] < len(row) else ''
                qty_val = row[col_map['qty']] if col_map['qty'] >= 0 and col_map['qty'] < len(row) else None
                unit = str(row[col_map['unit']] or '').strip() if col_map['unit'] >= 0 and col_map['unit'] < len(row) else 'EA'
                vendor = str(row[col_map['vendor']] or '').strip() if col_map['vendor'] >= 0 and col_map['vendor'] < len(row) else ''

                # Skip empty rows
                if not item_no and not description:
                    continue

                # Parse quantity
                try:
                    qty = Decimal(str(qty_val)) if qty_val else Decimal('1')
                except (InvalidOperation, ValueError):
                    qty = Decimal('1')

                # Detect if this is a section header (has Item No but no Model)
                is_section = False
                if item_no and not model and description:
                    # Check if Item No looks like a section number (Roman numerals, single digits, or short codes)
                    if re.match(r'^[IVX]+$', item_no) or re.match(r'^\d+$', item_no) or len(item_no) <= 4:
                        is_section = True

                if is_section:
                    # Create section
                    current_section = CostingSection.objects.create(
                        costing_sheet=sheet,
                        section_number=item_no,
                        title=description[:255],
                        order=sections_created
                    )
                    sections_created += 1
                    item_order = 0
                else:
                    # Create line item
                    if not current_section:
                        # Create a default section if none exists
                        current_section = CostingSection.objects.create(
                            costing_sheet=sheet,
                            section_number='1',
                            title='Imported Items',
                            order=0
                        )
                        sections_created += 1

                    CostingLineItem.objects.create(
                        section=current_section,
                        item_number=item_no or f'{current_section.section_number}.{item_order + 1}',
                        description=description[:500] if description else 'No description',
                        make=make[:100] if make else '',
                        model_number=model[:100] if model else '',
                        quantity=qty,
                        unit=unit[:20] if unit else 'EA',
                        vendor_name=vendor[:255] if vendor else '',
                        order=item_order
                    )
                    items_created += 1
                    item_order += 1

            messages.success(request, f'Import successful! Created costing sheet "{sheet.title}" with {sections_created} sections and {items_created} line items.')
            return redirect('costing:detail', pk=sheet.pk)

        except Exception as e:
            messages.error(request, f'Error importing file: {str(e)}')
            return redirect('costing:import_new')

    # GET request - show import form
    return render(request, 'costing/import_new.html', {})
