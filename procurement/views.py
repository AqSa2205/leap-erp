from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Q
from decimal import Decimal

from .models import (
    PurchaseOrder, PurchaseOrderItem,
    ProcurementSummary, ProcurementSummaryItem,
    DeliveryNote, DeliveryNoteItem,
    InventoryReport, InventoryItem,
    FRCReport, FRCEntry, FRCInventory,
)
from .forms import (
    PurchaseOrderForm, POItemFormSet, POFilterForm,
    ProcurementSummaryForm, SummaryItemFormSet,
    DeliveryNoteForm, DNItemFormSet,
    InventoryReportForm, InventoryItemFormSet,
    FRCReportForm, FRCEntryFormSet, FRCInventoryForm,
)
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, F
from django.utils.text import slugify
import openpyxl
from datetime import datetime, timedelta


def _safe_filename(name, prefix='', suffix='', extension=''):
    """Build a safe filename for Content-Disposition headers.

    Strips quotes, newlines, slashes, and any other characters that could
    inject header values or cause path traversal. Falls back to 'export'
    if the result would be empty.
    """
    safe = slugify(str(name or ''))[:80] or 'export'
    parts = [p for p in (prefix, safe, suffix) if p]
    base = '_'.join(parts)
    if extension and not extension.startswith('.'):
        extension = f'.{extension}'
    return f'{base}{extension}'


# ═══════════════════════════════════════════════════════════════
# PROCUREMENT DASHBOARD
# ═══════════════════════════════════════════════════════════════

@login_required
def procurement_dashboard(request):
    """Procurement dashboard with KPIs, recent activity, and stats."""
    user = request.user

    # Procurement roles get full access
    has_full_access = user.is_super_admin_user or user.is_procurement_user

    # PO stats
    po_qs = PurchaseOrder.objects.all()
    if not has_full_access:
        if user.is_admin_user or user.is_manager_user:
            po_qs = po_qs.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            po_qs = po_qs.filter(created_by=user)

    po_total = po_qs.count()
    po_by_status = {}
    for s in ['draft', 'issued', 'acknowledged', 'completed', 'cancelled']:
        po_by_status[s] = po_qs.filter(status=s).count()

    # DN stats
    dn_qs = DeliveryNote.objects.all()
    if not has_full_access:
        if user.is_admin_user or user.is_manager_user:
            dn_qs = dn_qs.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            dn_qs = dn_qs.filter(created_by=user)
    dn_total = dn_qs.count()

    # Summary stats
    summary_qs = ProcurementSummary.objects.all()
    if not has_full_access:
        if user.is_admin_user or user.is_manager_user:
            summary_qs = summary_qs.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            summary_qs = summary_qs.filter(created_by=user)
    summary_total = summary_qs.count()

    # Inventory stats
    inv_qs = InventoryReport.objects.all()
    if not has_full_access:
        if user.is_admin_user or user.is_manager_user:
            inv_qs = inv_qs.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            inv_qs = inv_qs.filter(created_by=user)
    inv_total = inv_qs.count()
    total_inventory_items = InventoryItem.objects.filter(report__in=inv_qs).count()

    # Low stock items - pushed entirely into SQL via the custom queryset.
    # Previously this loaded every InventoryItem and ran the is_low_stock
    # Python property per row (N+1 over potentially thousands of items).
    low_stock_qs = (
        InventoryItem.objects
        .filter(report__in=inv_qs)
        .low_stock()
        .select_related('report')
    )
    low_stock_count = low_stock_qs.count()
    low_stock_items = list(low_stock_qs[:10])  # template only uses first 10

    # Recent activity
    recent_pos = po_qs.order_by('-created_at')[:5]
    recent_dns = dn_qs.order_by('-created_at')[:5]

    context = {
        'po_total': po_total,
        'po_by_status': po_by_status,
        'dn_total': dn_total,
        'summary_total': summary_total,
        'inv_total': inv_total,
        'total_inventory_items': total_inventory_items,
        'low_stock_count': low_stock_count,
        'low_stock_items': low_stock_items,
        'recent_pos': recent_pos,
        'recent_dns': recent_dns,
    }
    return render(request, 'procurement/dashboard.html', context)


class ProcurementPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_queryset(self):
        queryset = PurchaseOrder.objects.select_related('project', 'created_by').all()
        user = self.request.user
        if user.is_super_admin_user or user.is_procurement_user:
            return queryset
        elif user.is_admin_user or user.is_manager_user:
            return queryset.filter(
                Q(created_by=user) |
                Q(project__region=user.region)
            )
        else:
            return queryset.filter(created_by=user)


# ─── PO CRUD ─────────────────────────────────────────────────

class POListView(ProcurementPermissionMixin, ListView):
    model = PurchaseOrder
    template_name = 'procurement/po_list.html'
    context_object_name = 'purchase_orders'
    paginate_by = 25

    def test_func(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        if search:
            queryset = queryset.filter(
                Q(po_number__icontains=search) |
                Q(vendor_name__icontains=search) |
                Q(project_name__icontains=search) |
                Q(end_user__icontains=search)
            )
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = POFilterForm(self.request.GET)
        context['total_count'] = self.get_queryset().count()
        return context


class POCreateView(ProcurementPermissionMixin, CreateView):
    model = PurchaseOrder
    form_class = PurchaseOrderForm
    template_name = 'procurement/po_form.html'

    def test_func(self):
        return True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = POItemFormSet(self.request.POST, prefix='items')
        else:
            context['item_formset'] = POItemFormSet(prefix='items')
        context['title'] = 'Create Purchase Order'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save(commit=False)
            self.object.created_by = self.request.user
            self.object.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, f'Purchase Order {self.object.po_number} created successfully.')
            return redirect(self.get_success_url())
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:po_detail', kwargs={'pk': self.object.pk})


class PODetailView(ProcurementPermissionMixin, DetailView):
    model = PurchaseOrder
    template_name = 'procurement/po_detail.html'
    context_object_name = 'po'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['items'] = self.object.items.all()
        return context


class POUpdateView(ProcurementPermissionMixin, UpdateView):
    model = PurchaseOrder
    form_class = PurchaseOrderForm
    template_name = 'procurement/po_form.html'

    def test_func(self):
        obj = self.get_object()
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return obj.created_by == user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = POItemFormSet(self.request.POST, instance=self.object, prefix='items')
        else:
            context['item_formset'] = POItemFormSet(instance=self.object, prefix='items')
        context['title'] = f'Edit PO: {self.object.po_number}'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, f'Purchase Order {self.object.po_number} updated successfully.')
            return redirect(self.get_success_url())
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:po_detail', kwargs={'pk': self.object.pk})


class PODeleteView(ProcurementPermissionMixin, DeleteView):
    model = PurchaseOrder
    template_name = 'procurement/po_confirm_delete.html'
    success_url = reverse_lazy('procurement:po_list')

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user:
            return True
        obj = self.get_object()
        return obj.created_by == user

    def form_valid(self, form):
        messages.success(self.request, 'Purchase Order deleted successfully.')
        return super().form_valid(form)


# ─── Excel Export ─────────────────────────────────────────────

def po_export_excel(request, pk):
    """Export a Purchase Order to Excel matching the original format."""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    po = get_object_or_404(PurchaseOrder, pk=pk)
    items = po.items.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'PURCHASE ORDER'

    # Styles
    bold = Font(bold=True)
    bold_red = Font(bold=True, color='C41E3A', size=11)
    header_font = Font(bold=True, color='FFFFFF', size=10)
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    wrap = Alignment(wrap_text=True, vertical='top')

    # ── Header Section ──
    headers_left = [
        ('PO Date', po.po_date.strftime('%d-%b-%Y') if po.po_date else ''),
        ('PO S. No.', po.po_number),
        ('C. Center', po.get_cost_center_display()),
        ('Vendor', po.vendor_name),
        ('Contact Person', po.vendor_contact_person),
        ('Contact Email', po.vendor_contact_email),
        ('Contact Tel', po.vendor_contact_tel),
    ]
    headers_right = [
        ('PO issued by', po.po_issued_by),
        ('Contact Email', po.issuer_email),
        ('Project Name', po.project_name),
        ('End User', po.end_user),
        ('MR, Item No.', po.mr_item_number),
        ('Delivery Incoterms', po.get_delivery_incoterms_display() if po.delivery_incoterms else ''),
        ('Delivery Location', po.delivery_location),
    ]

    for i, (label, value) in enumerate(headers_left, 1):
        ws.cell(row=i, column=1, value=label).font = bold
        ws.cell(row=i, column=2, value=str(value))

    for i, (label, value) in enumerate(headers_right, 1):
        ws.cell(row=i, column=5, value=label).font = bold
        ws.cell(row=i, column=6, value=str(value))

    # ── Line Items Table ──
    table_row = 11
    col_headers = ['S No.', 'Make/Model', 'Item Descriptions / Specification',
                    '', '', 'Quantity', 'UOM', 'Rate/unit (SAR)', 'Total Value (SAR)', 'Remarks']
    for col, h in enumerate(col_headers, 1):
        cell = ws.cell(row=table_row, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    # Merge description header across C-E
    ws.merge_cells(start_row=table_row, start_column=3, end_row=table_row, end_column=5)

    row = table_row + 1
    for item in items:
        ws.cell(row=row, column=1, value=item.serial_number).border = thin_border
        ws.cell(row=row, column=2, value=item.make_model).border = thin_border
        desc_cell = ws.cell(row=row, column=3, value=item.description)
        desc_cell.border = thin_border
        desc_cell.alignment = wrap
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=5)
        ws.cell(row=row, column=6, value=float(item.quantity)).border = thin_border
        ws.cell(row=row, column=7, value=item.uom).border = thin_border
        ws.cell(row=row, column=8, value=float(item.rate_per_unit)).border = thin_border
        ws.cell(row=row, column=9, value=float(item.total_value)).border = thin_border
        ws.cell(row=row, column=10, value=item.remarks).border = thin_border
        row += 1

    # ── Totals ──
    row += 1
    totals = [
        ('Base Amount', float(po.base_amount)),
        ('Discount', float(po.discount_amount)),
        ('Gross Value', float(po.gross_value)),
        ('VAT', float(po.vat_amount)),
        ('Total Value in SAR', float(po.total_value)),
    ]
    for label, val in totals:
        ws.cell(row=row, column=3, value=label).font = bold
        if label == 'VAT':
            ws.cell(row=row, column=6, value=float(po.vat_rate / 100))
            ws.cell(row=row, column=6).number_format = '0%'
        if label == 'Discount':
            ws.cell(row=row, column=6, value=float(po.discount_rate / 100))
            ws.cell(row=row, column=6).number_format = '0%'
        val_cell = ws.cell(row=row, column=9, value=val)
        val_cell.font = bold
        val_cell.number_format = '#,##0.00'
        row += 1

    # ── T&C ──
    if po.terms_and_conditions:
        row += 2
        ws.cell(row=row, column=1, value='TERMS AND CONDITIONS').font = bold_red
        row += 1
        for line in po.terms_and_conditions.split('\n'):
            if line.strip():
                ws.cell(row=row, column=2, value=line.strip()).alignment = wrap
                row += 1

    # Column widths
    widths = [8, 18, 15, 15, 15, 10, 8, 14, 14, 18]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = _safe_filename(po.po_number, prefix='PO', extension='xlsx')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ─── PDF Export ───────────────────────────────────────────────

@login_required
def po_export_pdf(request, pk):
    """Export a Purchase Order to PDF matching the original format."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from io import BytesIO
    from django.contrib.staticfiles.finders import find as find_static

    po = get_object_or_404(PurchaseOrder, pk=pk)
    items = po.items.all()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm, leftMargin=15*mm, rightMargin=15*mm)
    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle('POTitle', parent=styles['Heading1'], fontSize=14, textColor=colors.HexColor('#1F4E79'), spaceAfter=6)
    label_style = ParagraphStyle('Label', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
    value_style = ParagraphStyle('Value', parent=styles['Normal'], fontSize=9, fontName='Helvetica-Bold')
    normal_style = ParagraphStyle('Norm', parent=styles['Normal'], fontSize=8)
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=7)
    tc_style = ParagraphStyle('TC', parent=styles['Normal'], fontSize=7, leading=9)
    right_style = ParagraphStyle('Right', parent=styles['Normal'], fontSize=8, alignment=TA_RIGHT)
    right_bold = ParagraphStyle('RightBold', parent=styles['Normal'], fontSize=8, alignment=TA_RIGHT, fontName='Helvetica-Bold')

    # ── Logo + Title ──
    logo_path = find_static('images/leap_logo.jpg')
    if logo_path:
        from reportlab.platypus import Image
        logo = Image(logo_path, width=50*mm, height=15*mm)
        title_table = Table([[logo, Paragraph('PURCHASE ORDER', title_style)]], colWidths=[55*mm, 120*mm])
        title_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        elements.append(title_table)
    else:
        elements.append(Paragraph('PURCHASE ORDER', title_style))
    elements.append(Spacer(1, 4*mm))

    # ── Header Info ──
    def lv(label, value):
        return [Paragraph(label, label_style), Paragraph(str(value or '-'), value_style)]

    header_data = [
        lv('PO Date', po.po_date.strftime('%d %b %Y') if po.po_date else '-') +
        [''] +
        lv('PO Issued By', po.po_issued_by),

        lv('PO Number', po.po_number) +
        [''] +
        lv('Contact Email', po.issuer_email),

        lv('Cost Center', po.get_cost_center_display()) +
        [''] +
        lv('Project Name', po.project_name),

        lv('Vendor', po.vendor_name) +
        [''] +
        lv('End User', po.end_user),

        lv('Contact Person', po.vendor_contact_person) +
        [''] +
        lv('MR / Item No.', po.mr_item_number),

        lv('Contact Email', po.vendor_contact_email) +
        [''] +
        lv('Delivery Incoterms', po.get_delivery_incoterms_display() if po.delivery_incoterms else '-'),

        lv('Contact Tel', po.vendor_contact_tel) +
        [''] +
        lv('Delivery Location', po.delivery_location),
    ]
    header_table = Table(header_data, colWidths=[22*mm, 60*mm, 5*mm, 22*mm, 60*mm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 5*mm))

    # ── Line Items Table ──
    col_widths = [12*mm, 25*mm, 55*mm, 15*mm, 14*mm, 22*mm, 22*mm, 20*mm]
    item_header = [
        Paragraph('<b>S.No.</b>', small_style),
        Paragraph('<b>Make/Model</b>', small_style),
        Paragraph('<b>Item Description</b>', small_style),
        Paragraph('<b>Qty</b>', small_style),
        Paragraph('<b>UOM</b>', small_style),
        Paragraph('<b>Rate/Unit</b>', small_style),
        Paragraph('<b>Total (SAR)</b>', small_style),
        Paragraph('<b>Remarks</b>', small_style),
    ]
    item_data = [item_header]

    dark_blue = colors.HexColor('#1F4E79')

    for item in items:
        item_data.append([
            Paragraph(str(item.serial_number), normal_style),
            Paragraph(item.make_model or '', small_style),
            Paragraph(item.description, small_style),
            Paragraph(f'{item.quantity:,.0f}', right_style),
            Paragraph(item.uom, small_style),
            Paragraph(f'{item.rate_per_unit:,.2f}', right_style),
            Paragraph(f'{item.total_value:,.2f}', right_bold),
            Paragraph(item.remarks or '', small_style),
        ])

    item_table = Table(item_data, colWidths=col_widths, repeatRows=1)
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), dark_blue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 4*mm))

    # ── Totals ──
    totals_data = [
        ['', '', 'Base Amount', '', '', '', f'{po.base_amount:,.2f}', ''],
    ]
    if po.discount_rate:
        totals_data.append(['', '', f'Discount ({po.discount_rate:.0f}%)', '', '', '', f'-{po.discount_amount:,.2f}', ''])
    totals_data.append(['', '', 'Gross Value', '', '', '', f'{po.gross_value:,.2f}', ''])
    totals_data.append(['', '', f'VAT ({po.vat_rate:.0f}%)', '', '', '', f'{po.vat_amount:,.2f}', ''])
    totals_data.append(['', '', 'Total Value in SAR', '', '', '', f'{po.total_value:,.2f}', ''])

    totals_table = Table(totals_data, colWidths=col_widths)
    totals_table.setStyle(TableStyle([
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('FONTNAME', (6, 0), (6, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (6, 0), (6, -1), 'RIGHT'),
        ('LINEABOVE', (2, -1), (6, -1), 1, dark_blue),
        ('LINEBELOW', (2, -1), (6, -1), 1.5, dark_blue),
        ('BACKGROUND', (2, -1), (6, -1), colors.HexColor('#E8EEF4')),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(totals_table)

    # ── Terms & Conditions ──
    if po.terms_and_conditions:
        elements.append(Spacer(1, 6*mm))
        elements.append(Paragraph('<b>TERMS AND CONDITIONS</b>', ParagraphStyle('TCHead', parent=styles['Heading2'], fontSize=10, textColor=colors.HexColor('#C41E3A'))))
        elements.append(Spacer(1, 2*mm))
        for line in po.terms_and_conditions.split('\n'):
            if line.strip():
                elements.append(Paragraph(line.strip(), tc_style))
                elements.append(Spacer(1, 1*mm))

    # ── Ratification / Signatures ──
    elements.append(Spacer(1, 8*mm))
    sig_data = [
        ['Prepared By: _______________', '', 'Approved By: _______________', '', 'Vendor Acknowledgment: _______________'],
        ['Date: _______________', '', 'Date: _______________', '', 'Date: _______________'],
    ]
    sig_table = Table(sig_data, colWidths=[55*mm, 5*mm, 55*mm, 5*mm, 55*mm])
    sig_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(sig_table)

    doc.build(elements)
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type='application/pdf')
    filename = _safe_filename(po.po_number, prefix='PO', extension='pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─── Import from Excel ────────────────────────────────────────

@login_required
def po_import_excel(request):
    """Import a Purchase Order from an Excel file matching the PO template format."""
    if request.method != 'POST':
        return redirect('procurement:po_list')

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        messages.error(request, 'Please select an Excel file.')
        return redirect('procurement:po_list')

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        ws = wb.active

        # Helper to read cell value
        def cell(row, col):
            v = ws.cell(row=row, column=col).value
            return str(v).strip() if v is not None else ''

        def cell_raw(row, col):
            return ws.cell(row=row, column=col).value

        # ── Parse header (rows 1-7) ──
        po_date_raw = cell_raw(1, 2)
        if isinstance(po_date_raw, datetime):
            po_date = po_date_raw.date()
        else:
            po_date = None

        po_number = cell(2, 2)
        if not po_number:
            messages.error(request, 'PO Number not found in cell B2.')
            return redirect('procurement:po_list')

        # Check for duplicate
        if PurchaseOrder.objects.filter(po_number=po_number).exists():
            messages.error(request, f'PO {po_number} already exists.')
            return redirect('procurement:po_list')

        cost_center_raw = cell(3, 2).lower()
        cost_center = 'projects'
        for code, label in PurchaseOrder.COST_CENTER_CHOICES:
            if cost_center_raw in (code, label.lower()):
                cost_center = code
                break

        vendor_name = cell(4, 2)
        vendor_contact_person = cell(5, 2)
        vendor_contact_email = cell(6, 2).rstrip("'")
        vendor_contact_tel = cell(7, 2)

        po_issued_by = cell(1, 6)
        issuer_email = cell(2, 6)
        project_name = cell(3, 6)
        end_user = cell(4, 6)
        mr_item_number = cell(5, 6)
        delivery_incoterms_raw = cell(6, 6).upper().split(' ')[0] if cell(6, 6) else ''
        delivery_location = cell(7, 6)

        # Match incoterm
        delivery_incoterms = ''
        for code, label in PurchaseOrder.INCOTERM_CHOICES:
            if delivery_incoterms_raw == code:
                delivery_incoterms = code
                break

        # ── Parse discount & VAT from totals area ──
        discount_rate = Decimal('0')
        vat_rate = Decimal('15')

        # Scan rows 15-25 for discount/vat values
        for r in range(15, 30):
            label = cell(r, 3).lower()
            if 'discount' in label:
                val = cell_raw(r, 6)
                if val and isinstance(val, (int, float)):
                    discount_rate = Decimal(str(val * 100)) if val < 1 else Decimal(str(val))
            if 'vat' in label:
                val = cell_raw(r, 6)
                if val and isinstance(val, (int, float)):
                    vat_rate = Decimal(str(val * 100)) if val < 1 else Decimal(str(val))

        # ── Parse T&C ──
        terms_lines = []
        tc_started = False
        for r in range(30, ws.max_row + 1):
            a_val = cell(r, 1)
            b_val = cell(r, 2)
            if 'TERMS AND CONDITIONS' in a_val.upper():
                tc_started = True
                continue
            if tc_started and (a_val or b_val):
                line = f"{a_val} {b_val}".strip() if a_val else b_val
                if line and 'vendor acknowledgment' not in line.lower():
                    terms_lines.append(line)
                if 'vendor acknowledgment' in (a_val + b_val).lower():
                    break

        # ── Create PO ──
        po = PurchaseOrder.objects.create(
            po_date=po_date or datetime.now().date(),
            po_number=po_number,
            cost_center=cost_center,
            vendor_name=vendor_name,
            vendor_contact_person=vendor_contact_person,
            vendor_contact_email=vendor_contact_email,
            vendor_contact_tel=vendor_contact_tel,
            po_issued_by=po_issued_by,
            issuer_email=issuer_email,
            project_name=project_name,
            end_user=end_user,
            mr_item_number=mr_item_number,
            delivery_incoterms=delivery_incoterms,
            delivery_location=delivery_location,
            discount_rate=discount_rate,
            vat_rate=vat_rate,
            terms_and_conditions='\n'.join(terms_lines),
            status='draft',
            created_by=request.user,
        )

        # ── Parse line items (starting at row 12 until blank/totals) ──
        item_count = 0
        for r in range(12, 30):
            sn = cell_raw(r, 1)
            desc = cell(r, 3)
            if not desc or (isinstance(sn, str) and not sn.isdigit() and sn != ''):
                # Check if we hit totals section
                if 'base amount' in desc.lower() or 'discount' in desc.lower():
                    break
                if not desc:
                    continue

            qty = cell_raw(r, 6)
            rate = cell_raw(r, 8)

            if qty is None and rate is None and not desc:
                continue

            PurchaseOrderItem.objects.create(
                purchase_order=po,
                serial_number=int(sn) if isinstance(sn, (int, float)) else item_count + 1,
                make_model=cell(r, 2),
                description=desc,
                quantity=Decimal(str(qty)) if qty else Decimal('0'),
                uom=cell(r, 7) or 'Nos',
                rate_per_unit=Decimal(str(rate)) if rate else Decimal('0'),
                remarks=cell(r, 10),
                order=item_count,
            )
            item_count += 1

        messages.success(request, f'Imported PO {po.po_number} with {item_count} line items.')
        return redirect('procurement:po_detail', pk=po.pk)

    except Exception as e:
        messages.error(request, f'Error importing file: {str(e)}')
        return redirect('procurement:po_list')


# ═══════════════════════════════════════════════════════════════
# PROCUREMENT SUMMARY
# ═══════════════════════════════════════════════════════════════

class SummaryPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_queryset(self):
        queryset = ProcurementSummary.objects.select_related('project', 'created_by').all()
        user = self.request.user
        if user.is_super_admin_user:
            return queryset
        elif user.is_admin_user or user.is_manager_user:
            return queryset.filter(
                Q(created_by=user) | Q(project__region=user.region)
            )
        else:
            return queryset.filter(created_by=user)


class SummaryListView(SummaryPermissionMixin, ListView):
    model = ProcurementSummary
    template_name = 'procurement/summary_list.html'
    context_object_name = 'summaries'
    paginate_by = 25

    def test_func(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(project_name__icontains=search) |
                Q(package_name__icontains=search)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_count'] = self.get_queryset().count()
        return context


class SummaryCreateView(SummaryPermissionMixin, CreateView):
    model = ProcurementSummary
    form_class = ProcurementSummaryForm
    template_name = 'procurement/summary_form.html'

    def test_func(self):
        return True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = SummaryItemFormSet(self.request.POST, prefix='items')
        else:
            context['item_formset'] = SummaryItemFormSet(prefix='items')
        context['title'] = 'Create Procurement Summary'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save(commit=False)
            self.object.created_by = self.request.user
            self.object.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, 'Procurement Summary created successfully.')
            return redirect(self.get_success_url())
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:summary_detail', kwargs={'pk': self.object.pk})


class SummaryDetailView(SummaryPermissionMixin, DetailView):
    model = ProcurementSummary
    template_name = 'procurement/summary_detail.html'
    context_object_name = 'summary'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['items'] = self.object.items.all()
        return context


class SummaryUpdateView(SummaryPermissionMixin, UpdateView):
    model = ProcurementSummary
    form_class = ProcurementSummaryForm
    template_name = 'procurement/summary_form.html'

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = SummaryItemFormSet(self.request.POST, instance=self.object, prefix='items')
        else:
            context['item_formset'] = SummaryItemFormSet(instance=self.object, prefix='items')
        context['title'] = f'Edit: {self.object}'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, 'Procurement Summary updated.')
            return redirect(self.get_success_url())
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:summary_detail', kwargs={'pk': self.object.pk})


class SummaryDeleteView(SummaryPermissionMixin, DeleteView):
    model = ProcurementSummary
    template_name = 'procurement/summary_confirm_delete.html'
    success_url = reverse_lazy('procurement:summary_list')

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def form_valid(self, form):
        messages.success(self.request, 'Procurement Summary deleted.')
        return super().form_valid(form)


# ─── Summary Export (Excel) ───────────────────────────────────

@login_required
def summary_export_excel(request, pk):
    summary = get_object_or_404(ProcurementSummary, pk=pk)
    items = summary.items.all()

    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = summary.package_name or 'Summary'

    bold = Font(bold=True)
    header_font = Font(bold=True, color='FFFFFF', size=9)
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    wrap = Alignment(wrap_text=True, vertical='top')

    # Project header
    ws.cell(row=1, column=2, value='Project:').font = bold
    ws.cell(row=1, column=3, value=summary.project_name).font = bold

    # Column headers (row 3)
    headers = ['Sr.', 'System / Item', 'PO Number', 'PO Status', 'PO QTY',
               'Supplier', 'Lead Time', 'Incoterm', 'Payment Terms', 'Warranty',
               'PO Value (SAR)', 'PO Value (USD/EUR)', 'Advance Payment',
               'Delivery Status', 'SCM']
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    # Data rows
    for row_num, item in enumerate(items, 4):
        data = [
            item.serial_number, item.system_item, item.po_number,
            item.get_po_status_display() if item.po_status else '',
            item.po_qty, item.supplier, item.lead_time, item.incoterm,
            item.payment_terms, item.warranty,
            float(item.po_value_sar) if item.po_value_sar else '',
            float(item.po_value_usd) if item.po_value_usd else '',
            float(item.advance_payment) if item.advance_payment else '',
            item.delivery_status, item.scm,
        ]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col, value=val)
            cell.border = thin_border
            cell.alignment = wrap

    # Totals row
    total_row = 4 + items.count() + 1
    ws.cell(row=total_row, column=9, value='Total').font = bold
    ws.cell(row=total_row, column=11, value=float(summary.total_po_value_sar)).font = bold
    ws.cell(row=total_row, column=11).number_format = '#,##0.00'
    ws.cell(row=total_row, column=12, value=float(summary.total_po_value_usd)).font = bold
    ws.cell(row=total_row, column=12).number_format = '#,##0.00'
    ws.cell(row=total_row, column=13, value=float(summary.total_advance_payment)).font = bold
    ws.cell(row=total_row, column=13).number_format = '#,##0.00'

    # Column widths
    widths = [6, 28, 18, 12, 12, 18, 16, 10, 18, 14, 14, 14, 14, 22, 8]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    raw_name = summary.package_name or summary.project_name
    filename = _safe_filename(raw_name, prefix='Summary', extension='xlsx')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ─── Summary Export (PDF) ─────────────────────────────────────

@login_required
def summary_export_pdf(request, pk):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_RIGHT
    from io import BytesIO
    from django.contrib.staticfiles.finders import find as find_static

    summary = get_object_or_404(ProcurementSummary, pk=pk)
    items = summary.items.all()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=12*mm, bottomMargin=12*mm, leftMargin=10*mm, rightMargin=10*mm)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=13, textColor=colors.HexColor('#1F4E79'), spaceAfter=2)
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, spaceAfter=4)
    small = ParagraphStyle('Sm', parent=styles['Normal'], fontSize=6, leading=7)
    small_r = ParagraphStyle('SmR', parent=styles['Normal'], fontSize=6, leading=7, alignment=TA_RIGHT)
    small_b = ParagraphStyle('SmB', parent=styles['Normal'], fontSize=6, leading=7, fontName='Helvetica-Bold')

    # Logo + Title
    logo_path = find_static('images/leap_logo.jpg')
    if logo_path:
        from reportlab.platypus import Image
        logo = Image(logo_path, width=45*mm, height=13*mm)
        t = Table([[logo, Paragraph('PROCUREMENT SUMMARY REPORT', title_style)]], colWidths=[50*mm, 220*mm])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        elements.append(t)
    else:
        elements.append(Paragraph('PROCUREMENT SUMMARY REPORT', title_style))

    pkg = f' - {summary.package_name}' if summary.package_name else ''
    elements.append(Paragraph(f'<b>Project:</b> {summary.project_name}{pkg}', sub_style))
    elements.append(Spacer(1, 3*mm))

    # Table
    dark_blue = colors.HexColor('#1F4E79')
    col_widths = [8*mm, 35*mm, 22*mm, 14*mm, 16*mm, 25*mm, 20*mm, 14*mm,
                  25*mm, 18*mm, 18*mm, 18*mm, 18*mm, 28*mm, 10*mm]

    header = [
        Paragraph('<b>Sr.</b>', small), Paragraph('<b>System / Item</b>', small),
        Paragraph('<b>PO Number</b>', small), Paragraph('<b>Status</b>', small),
        Paragraph('<b>PO QTY</b>', small), Paragraph('<b>Supplier</b>', small),
        Paragraph('<b>Lead Time</b>', small), Paragraph('<b>Incoterm</b>', small),
        Paragraph('<b>Payment Terms</b>', small), Paragraph('<b>Warranty</b>', small),
        Paragraph('<b>PO Value SAR</b>', small), Paragraph('<b>PO Value USD</b>', small),
        Paragraph('<b>Advance Pmt</b>', small), Paragraph('<b>Delivery Status</b>', small),
        Paragraph('<b>SCM</b>', small),
    ]
    data = [header]

    for item in items:
        data.append([
            Paragraph(str(item.serial_number), small),
            Paragraph(item.system_item, small),
            Paragraph(item.po_number, small),
            Paragraph(item.get_po_status_display() if item.po_status else '', small),
            Paragraph(item.po_qty, small),
            Paragraph(item.supplier, small),
            Paragraph(item.lead_time, small),
            Paragraph(item.incoterm, small),
            Paragraph(item.payment_terms, small),
            Paragraph(item.warranty, small),
            Paragraph(f'{item.po_value_sar:,.2f}' if item.po_value_sar else '-', small_r),
            Paragraph(f'{item.po_value_usd:,.2f}' if item.po_value_usd else '-', small_r),
            Paragraph(f'{item.advance_payment:,.2f}' if item.advance_payment else '-', small_r),
            Paragraph(item.delivery_status, small),
            Paragraph(item.scm, small),
        ])

    # Totals row
    data.append([
        '', '', '', '', '', '', '', '',
        Paragraph('<b>Total</b>', small_b), '',
        Paragraph(f'<b>{summary.total_po_value_sar:,.2f}</b>', small_r),
        Paragraph(f'<b>{summary.total_po_value_usd:,.2f}</b>', small_r),
        Paragraph(f'<b>{summary.total_advance_payment:,.2f}</b>', small_r),
        '', '',
    ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), dark_blue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F5F5F5')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E8EEF4')),
        ('LINEABOVE', (0, -1), (-1, -1), 1, dark_blue),
    ]))
    elements.append(table)

    doc.build(elements)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type='application/pdf')
    raw_name = summary.package_name or summary.project_name
    filename = _safe_filename(raw_name, prefix='Summary', extension='pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─── Summary Import (Excel) ──────────────────────────────────

@login_required
def summary_import_excel(request):
    """Import a Procurement Summary from an Excel file (multi-sheet supported)."""
    if request.method != 'POST':
        return redirect('procurement:summary_list')

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        messages.error(request, 'Please select an Excel file.')
        return redirect('procurement:summary_list')

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        created_count = 0

        for ws in wb.worksheets:
            # Skip sheets that don't look like summary data
            if ws.max_row < 4:
                continue

            # Parse project name from row 1
            project_name = ''
            for col in range(1, 10):
                v = ws.cell(row=1, column=col).value
                if v and 'project' not in str(v).lower().strip().rstrip(':'):
                    project_name = str(v).strip()
                    break

            if not project_name:
                project_name = ws.title

            summary = ProcurementSummary.objects.create(
                project_name=project_name,
                package_name=ws.title,
                created_by=request.user,
            )

            # Parse items starting from row 4
            item_count = 0
            for r in range(4, ws.max_row + 1):
                cell_b = ws.cell(row=r, column=2).value
                if not cell_b:
                    continue
                b_str = str(cell_b).strip()
                if not b_str or b_str.lower() == 'total':
                    continue

                def cv(col):
                    v = ws.cell(row=r, column=col).value
                    return str(v).strip() if v is not None else ''

                def nv(col):
                    v = ws.cell(row=r, column=col).value
                    if isinstance(v, (int, float)):
                        return Decimal(str(v))
                    return None

                sn = ws.cell(row=r, column=1).value
                ProcurementSummaryItem.objects.create(
                    summary=summary,
                    serial_number=int(sn) if isinstance(sn, (int, float)) else item_count + 1,
                    system_item=b_str,
                    po_number=cv(3),
                    po_status='issued' if 'issued' in cv(4).lower() else '',
                    po_qty=cv(5),
                    supplier=cv(6),
                    lead_time=cv(7),
                    incoterm=cv(8),
                    payment_terms=cv(9),
                    warranty=cv(10),
                    po_value_sar=nv(11),
                    po_value_usd=nv(12),
                    advance_payment=nv(13),
                    delivery_status=cv(14),
                    scm=cv(15),
                    order=item_count,
                )
                item_count += 1

            if item_count == 0:
                summary.delete()
            else:
                created_count += 1

        messages.success(request, f'Imported {created_count} summary sheet(s).')
        return redirect('procurement:summary_list')

    except Exception as e:
        messages.error(request, f'Error importing file: {str(e)}')
        return redirect('procurement:summary_list')


# ═══════════════════════════════════════════════════════════════
# DELIVERY NOTE
# ═══════════════════════════════════════════════════════════════

class DNPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_queryset(self):
        queryset = DeliveryNote.objects.select_related('project', 'created_by', 'purchase_order').all()
        user = self.request.user
        if user.is_super_admin_user or user.is_procurement_user:
            return queryset
        elif user.is_admin_user or user.is_manager_user:
            return queryset.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            return queryset.filter(created_by=user)


class DNListView(DNPermissionMixin, ListView):
    model = DeliveryNote
    template_name = 'procurement/dn_list.html'
    context_object_name = 'delivery_notes'
    paginate_by = 25

    def test_func(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(dn_number__icontains=search) |
                Q(sold_to_company__icontains=search) |
                Q(project_title__icontains=search) |
                Q(leap_po_number__icontains=search)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_count'] = self.get_queryset().count()
        return context


class DNCreateView(DNPermissionMixin, CreateView):
    model = DeliveryNote
    form_class = DeliveryNoteForm
    template_name = 'procurement/dn_form.html'

    def test_func(self):
        return True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = DNItemFormSet(self.request.POST, prefix='items')
        else:
            context['item_formset'] = DNItemFormSet(prefix='items')
        context['title'] = 'Create Delivery Note'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save(commit=False)
            self.object.created_by = self.request.user
            self.object.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, 'Delivery Note created.')
            return redirect(self.get_success_url())
        return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:dn_detail', kwargs={'pk': self.object.pk})


class DNDetailView(DNPermissionMixin, DetailView):
    model = DeliveryNote
    template_name = 'procurement/dn_detail.html'
    context_object_name = 'dn'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['items'] = self.object.items.all()
        return context


class DNUpdateView(DNPermissionMixin, UpdateView):
    model = DeliveryNote
    form_class = DeliveryNoteForm
    template_name = 'procurement/dn_form.html'

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = DNItemFormSet(self.request.POST, instance=self.object, prefix='items')
        else:
            context['item_formset'] = DNItemFormSet(instance=self.object, prefix='items')
        context['title'] = f'Edit DN: {self.object.dn_number or self.object.pk}'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, 'Delivery Note updated.')
            return redirect(self.get_success_url())
        return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:dn_detail', kwargs={'pk': self.object.pk})


class DNDeleteView(DNPermissionMixin, DeleteView):
    model = DeliveryNote
    template_name = 'procurement/dn_confirm_delete.html'
    success_url = reverse_lazy('procurement:dn_list')

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def form_valid(self, form):
        messages.success(self.request, 'Delivery Note deleted.')
        return super().form_valid(form)


# ─── DN Export Excel ──────────────────────────────────────────

@login_required
def dn_export_excel(request, pk):
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    dn = get_object_or_404(DeliveryNote, pk=pk)
    items = dn.items.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'DELIVERY NOTE'

    bold = Font(bold=True)
    header_font = Font(bold=True, color='FFFFFF', size=10)
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    wrap = Alignment(wrap_text=True, vertical='top')

    # Title
    ws.merge_cells('A1:F1')
    ws.cell(row=1, column=1, value='DELIVERY NOTE').font = Font(bold=True, size=14, color='1F4E79')

    # Header info
    info = [
        (3, 'Sold To:', dn.sold_to_company, 'Project Title:', dn.project_title),
        (4, 'Address:', dn.sold_to_address, 'LEAP PO No:', dn.leap_po_number),
        (5, 'Delivery Address:', dn.delivery_address, 'Client PO No:', dn.client_po_number),
        (6, 'Attention:', dn.attention, 'DN Number:', dn.dn_number),
        (7, 'Mobile:', dn.mobile, '', ''),
        (8, 'Email:', dn.email, '', ''),
        (9, 'Date:', dn.date.strftime('%d-%b-%Y') if dn.date else '', '', ''),
    ]
    for r, l1, v1, l2, v2 in info:
        ws.cell(row=r, column=1, value=l1).font = bold
        c = ws.cell(row=r, column=2, value=str(v1))
        c.alignment = wrap
        if l2:
            ws.cell(row=r, column=4, value=l2).font = bold
            ws.cell(row=r, column=5, value=str(v2))

    # Items table
    row = 11
    headers = ['S No.', 'Make/Part Number', 'Description', 'Quantity', 'UOM', 'Remarks / LNA PO# ref']
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    for item in items:
        row += 1
        data = [item.serial_number, item.make_part_number, item.description,
                float(item.quantity), item.uom, item.remarks]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.border = thin_border
            cell.alignment = wrap

    # Footer
    row += 2
    ws.cell(row=row, column=1, value='ALL CLAIMS FOR DAMAGES AND SHORTAGES OF GOODS MUST BE MADE UPON RECEIPT OF GOODS.').font = Font(bold=True, size=8)
    row += 2
    ws.cell(row=row, column=1, value='GOODS RECEIVED IN GOOD ORDER BY:').font = bold
    ws.cell(row=row, column=4, value='ITEM VERIFIED/AUTHORIZED BY').font = bold
    row += 3
    ws.cell(row=row, column=1, value='____________________________________________')
    ws.cell(row=row, column=4, value='___________________________________')
    row += 1
    ws.cell(row=row, column=1, value='(SIGNATURE OF RECEIVER)')
    ws.cell(row=row, column=4, value='SIGNATURE / DATE')

    widths = [8, 25, 40, 10, 8, 25]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    raw = dn.dn_number or f'DN-{dn.pk}'
    filename = _safe_filename(raw, extension='xlsx')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ─── DN Export PDF ────────────────────────────────────────────

@login_required
def dn_export_pdf(request, pk):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_CENTER
    from io import BytesIO
    from django.contrib.staticfiles.finders import find as find_static

    dn = get_object_or_404(DeliveryNote, pk=pk)
    items = dn.items.all()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm, leftMargin=15*mm, rightMargin=15*mm)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('DNTitle', parent=styles['Heading1'], fontSize=16, textColor=colors.HexColor('#1F4E79'), alignment=TA_CENTER, spaceAfter=4)
    label_style = ParagraphStyle('Lbl', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
    value_style = ParagraphStyle('Val', parent=styles['Normal'], fontSize=9, fontName='Helvetica-Bold')
    small = ParagraphStyle('Sm', parent=styles['Normal'], fontSize=8)
    small_bold = ParagraphStyle('SmB', parent=styles['Normal'], fontSize=8, fontName='Helvetica-Bold')

    # Logo
    logo_path = find_static('images/leap_logo.jpg')
    if logo_path:
        from reportlab.platypus import Image
        logo = Image(logo_path, width=50*mm, height=15*mm)
        elements.append(logo)

    elements.append(Paragraph('DELIVERY NOTE', title_style))
    elements.append(Spacer(1, 4*mm))

    # Header info table
    def lv(label, value):
        return [Paragraph(label, label_style), Paragraph(str(value or '-'), value_style)]

    header_data = [
        lv('Sold To:', dn.sold_to_company) + [''] + lv('Project Title:', dn.project_title),
        lv('Address:', dn.sold_to_address) + [''] + lv('LEAP PO No:', dn.leap_po_number),
        lv('Delivery Address:', dn.delivery_address) + [''] + lv('Client PO No:', dn.client_po_number),
        lv('Attention:', dn.attention) + [''] + lv('DN Number:', dn.dn_number),
        lv('Mobile:', dn.mobile) + [''] + ['', ''],
        lv('Email:', dn.email) + [''] + ['', ''],
        lv('Date:', dn.date.strftime('%d %b %Y') if dn.date else '-') + [''] + ['', ''],
    ]
    ht = Table(header_data, colWidths=[22*mm, 60*mm, 5*mm, 22*mm, 60*mm])
    ht.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(ht)
    elements.append(Spacer(1, 5*mm))

    # Items table
    dark_blue = colors.HexColor('#1F4E79')
    col_widths = [12*mm, 35*mm, 60*mm, 18*mm, 15*mm, 35*mm]
    item_header = [
        Paragraph('<b>S.No.</b>', small), Paragraph('<b>Make/Part No.</b>', small),
        Paragraph('<b>Description</b>', small), Paragraph('<b>Qty</b>', small),
        Paragraph('<b>UOM</b>', small), Paragraph('<b>Remarks</b>', small),
    ]
    data = [item_header]
    for item in items:
        data.append([
            Paragraph(str(item.serial_number), small),
            Paragraph(item.make_part_number or '', small),
            Paragraph(item.description, small),
            Paragraph(f'{item.quantity:,.0f}', small),
            Paragraph(item.uom, small),
            Paragraph(item.remarks or '', small),
        ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), dark_blue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 6*mm))

    # Footer
    elements.append(Paragraph(
        '<b>ALL CLAIMS FOR DAMAGES AND SHORTAGES OF GOODS MUST BE MADE UPON RECEIPT OF GOODS.</b>',
        ParagraphStyle('Notice', parent=styles['Normal'], fontSize=7, leading=9)
    ))
    elements.append(Spacer(1, 8*mm))

    sig_data = [
        ['GOODS RECEIVED IN GOOD ORDER BY:', '', 'ITEM VERIFIED/AUTHORIZED BY:'],
        ['', '', 'LEAP NETWORKS'],
        ['', '', ''],
        ['____________________________________________', '', '___________________________________'],
        ['(SIGNATURE OF RECEIVER)', '', 'SIGNATURE / DATE'],
        ['DATE:', '', ''],
        ['Name:', '', ''],
    ]
    sig_table = Table(sig_data, colWidths=[75*mm, 20*mm, 75*mm])
    sig_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(sig_table)

    doc.build(elements)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type='application/pdf')
    raw = dn.dn_number or f'DN-{dn.pk}'
    filename = _safe_filename(raw, extension='pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─── DN Import Excel ──────────────────────────────────────────

@login_required
def dn_import_excel(request):
    if request.method != 'POST':
        return redirect('procurement:dn_list')

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        messages.error(request, 'Please select an Excel file.')
        return redirect('procurement:dn_list')

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        ws = wb.active

        def cv(r, c):
            v = ws.cell(row=r, column=c).value
            return str(v).strip() if v is not None else ''

        def cv_raw(r, c):
            return ws.cell(row=r, column=c).value

        # Parse header
        sold_to = cv(7, 2) or cv(7, 3)
        sold_to_address = cv(8, 2)
        delivery_address = cv(9, 2)
        attention = cv(11, 2)
        mobile = cv(12, 2)
        email = cv(13, 2)
        date_raw = cv_raw(14, 2)
        dn_date = date_raw.date() if isinstance(date_raw, datetime) else None

        project_title = cv(11, 5) or cv(9, 5)
        leap_po = cv(13, 5)
        client_po = cv(14, 5)
        dn_number = cv(15, 5)

        dn_obj = DeliveryNote.objects.create(
            sold_to_company=sold_to,
            sold_to_address=sold_to_address,
            delivery_address=delivery_address,
            attention=attention,
            mobile=mobile,
            email=email,
            date=dn_date or datetime.now().date(),
            project_title=project_title,
            leap_po_number=leap_po,
            client_po_number=client_po,
            dn_number=dn_number,
            created_by=request.user,
        )

        # Parse items (find header row first)
        header_row = None
        for r in range(1, ws.max_row + 1):
            val = cv(r, 1).lower()
            if 's no' in val or 'sr' in val:
                header_row = r
                break

        item_count = 0
        if header_row:
            for r in range(header_row + 1, ws.max_row + 1):
                sn = cv_raw(r, 1)
                desc = cv(r, 3)
                if not desc:
                    if cv(r, 1).upper().startswith('ALL CLAIMS'):
                        break
                    continue
                if not isinstance(sn, (int, float)):
                    continue

                qty_raw = cv_raw(r, 4)
                DeliveryNoteItem.objects.create(
                    delivery_note=dn_obj,
                    serial_number=int(sn),
                    make_part_number=cv(r, 2),
                    description=desc,
                    quantity=Decimal(str(qty_raw)) if isinstance(qty_raw, (int, float)) else Decimal('1'),
                    uom=cv(r, 5) or 'Each',
                    remarks=cv(r, 6),
                    order=item_count,
                )
                item_count += 1

        messages.success(request, f'Imported Delivery Note with {item_count} items.')
        return redirect('procurement:dn_detail', pk=dn_obj.pk)

    except Exception as e:
        messages.error(request, f'Error importing: {str(e)}')
        return redirect('procurement:dn_list')


# ═══════════════════════════════════════════════════════════════
# INVENTORY REPORT
# ═══════════════════════════════════════════════════════════════

class InventoryPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_queryset(self):
        queryset = InventoryReport.objects.select_related('project', 'created_by').all()
        user = self.request.user
        if user.is_super_admin_user or user.is_procurement_user:
            return queryset
        elif user.is_admin_user or user.is_manager_user:
            return queryset.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            return queryset.filter(created_by=user)


class InventoryListView(InventoryPermissionMixin, ListView):
    model = InventoryReport
    template_name = 'procurement/inventory_list.html'
    context_object_name = 'reports'
    paginate_by = 25

    def test_func(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(warehouse_location__icontains=search)
            )
        # Annotate item count, low-stock count, and total stock value in a
        # single SQL query so the list page does not run several per-row
        # queries (was N+1 across items.count, low_stock_count property,
        # and total_stock_value property).
        # Note: annotation names cannot start with underscore - Django
        # templates reject variables that begin with one.
        from django.db.models import Q as _Q, DecimalField, ExpressionWrapper
        return queryset.annotate(
            annotated_item_count=Count('items', distinct=True),
            annotated_low_stock_count=Count(
                'items',
                filter=_Q(
                    items__min_stock_level__isnull=False,
                    items__min_stock_level__gt=0,
                    items__balance_qty__isnull=False,
                    items__balance_qty__lte=F('items__min_stock_level'),
                ),
                distinct=True,
            ),
            annotated_stock_value=Sum(
                ExpressionWrapper(
                    F('items__unit_cost') * F('items__balance_qty'),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                ),
            ),
        ).order_by('-updated_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_count'] = self.get_queryset().count()
        return context


class InventoryCreateView(InventoryPermissionMixin, CreateView):
    model = InventoryReport
    form_class = InventoryReportForm
    template_name = 'procurement/inventory_form.html'

    def test_func(self):
        return True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = InventoryItemFormSet(self.request.POST, prefix='items')
        else:
            context['item_formset'] = InventoryItemFormSet(prefix='items')
        context['title'] = 'Create Inventory Report'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save(commit=False)
            self.object.created_by = self.request.user
            self.object.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, 'Inventory Report created.')
            return redirect(self.get_success_url())
        return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:inventory_detail', kwargs={'pk': self.object.pk})


class InventoryDetailView(InventoryPermissionMixin, DetailView):
    model = InventoryReport
    template_name = 'procurement/inventory_detail.html'
    context_object_name = 'report'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['items'] = self.object.items.all()
        return context


class InventoryUpdateView(InventoryPermissionMixin, UpdateView):
    model = InventoryReport
    form_class = InventoryReportForm
    template_name = 'procurement/inventory_form.html'

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['item_formset'] = InventoryItemFormSet(self.request.POST, instance=self.object, prefix='items')
        else:
            context['item_formset'] = InventoryItemFormSet(instance=self.object, prefix='items')
        context['title'] = f'Edit: {self.object.title}'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        item_formset = context['item_formset']
        if item_formset.is_valid():
            self.object = form.save()
            item_formset.instance = self.object
            item_formset.save()
            messages.success(self.request, 'Inventory Report updated.')
            return redirect(self.get_success_url())
        return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:inventory_detail', kwargs={'pk': self.object.pk})


class InventoryDeleteView(InventoryPermissionMixin, DeleteView):
    model = InventoryReport
    template_name = 'procurement/inventory_confirm_delete.html'
    success_url = reverse_lazy('procurement:inventory_list')

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def form_valid(self, form):
        messages.success(self.request, 'Inventory Report deleted.')
        return super().form_valid(form)


# ─── Inventory Export Excel ───────────────────────────────────

@login_required
def inventory_export_excel(request, pk):
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    report = get_object_or_404(InventoryReport, pk=pk)
    items = report.items.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Inventory'

    bold = Font(bold=True)
    header_font = Font(bold=True, color='FFFFFF', size=9)
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    red_fill = PatternFill(start_color='FFE0E0', end_color='FFE0E0', fill_type='solid')
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    wrap = Alignment(wrap_text=True, vertical='top')

    # Title
    ws.cell(row=1, column=1, value=report.title).font = Font(bold=True, size=13, color='1F4E79')
    if report.warehouse_location:
        ws.cell(row=2, column=1, value=f'Warehouse: {report.warehouse_location}').font = bold

    # Headers
    headers = [
        'S.No', 'Item Code', 'Name', 'Model', 'Make', 'Category', 'Status', 'Unit',
        'Qty Received', 'Qty Issued', 'Balance', 'Min Stock', 'Unit Cost', 'Total Value',
        'TAG', 'Rack', 'Received Date', 'Handover To', 'Handover Date', 'Project', 'PO Ref', 'Remarks'
    ]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    for row_num, item in enumerate(items, 5):
        data = [
            item.serial_number, item.item_code, item.name, item.model_number, item.make,
            item.get_category_display(), item.get_status_display(), item.unit,
            float(item.quantity_received), float(item.quantity_issued),
            float(item.balance_qty) if item.balance_qty else 0,
            float(item.min_stock_level) if item.min_stock_level else '',
            float(item.unit_cost) if item.unit_cost else '',
            float(item.total_value),
            item.tag, item.rack_location,
            item.received_date.strftime('%d-%b-%Y') if item.received_date else '',
            item.handover_to,
            item.handover_date.strftime('%d-%b-%Y') if item.handover_date else '',
            item.project_ref, item.po_reference, item.remarks,
        ]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col, value=val)
            cell.border = thin_border
            cell.alignment = wrap
        if item.is_low_stock:
            for col in range(1, len(data) + 1):
                ws.cell(row=row_num, column=col).fill = red_fill

    widths = [6, 10, 28, 14, 14, 16, 10, 8, 10, 10, 10, 10, 10, 12, 10, 8, 12, 14, 12, 14, 10, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = _safe_filename(report.title, prefix='Inventory', extension='xlsx')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ─── Inventory Export PDF ─────────────────────────────────────

@login_required
def inventory_export_pdf(request, pk):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_RIGHT
    from io import BytesIO
    from django.contrib.staticfiles.finders import find as find_static

    report = get_object_or_404(InventoryReport, pk=pk)
    items = report.items.all()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=10*mm, bottomMargin=10*mm, leftMargin=8*mm, rightMargin=8*mm)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('InvTitle', parent=styles['Heading1'], fontSize=13, textColor=colors.HexColor('#1F4E79'), spaceAfter=2)
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=9, spaceAfter=4)
    s = ParagraphStyle('S', parent=styles['Normal'], fontSize=5.5, leading=6.5)
    sr = ParagraphStyle('SR', parent=styles['Normal'], fontSize=5.5, leading=6.5, alignment=TA_RIGHT)
    sb = ParagraphStyle('SB', parent=styles['Normal'], fontSize=5.5, leading=6.5, fontName='Helvetica-Bold')

    # Logo + Title
    logo_path = find_static('images/leap_logo.jpg')
    if logo_path:
        from reportlab.platypus import Image
        logo = Image(logo_path, width=40*mm, height=12*mm)
        t = Table([[logo, Paragraph(f'INVENTORY REPORT: {report.title}', title_style)]], colWidths=[45*mm, 230*mm])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        elements.append(t)
    else:
        elements.append(Paragraph(f'INVENTORY REPORT: {report.title}', title_style))

    if report.warehouse_location:
        elements.append(Paragraph(f'<b>Warehouse:</b> {report.warehouse_location}', sub_style))
    elements.append(Paragraph(f'<b>Items:</b> {report.total_items} | <b>Low Stock:</b> {report.low_stock_count} | <b>Total Value:</b> SAR {report.total_stock_value:,.2f}', sub_style))
    elements.append(Spacer(1, 2*mm))

    dark_blue = colors.HexColor('#1F4E79')
    col_widths = [7*mm, 12*mm, 30*mm, 12*mm, 12*mm, 14*mm, 11*mm, 8*mm,
                  10*mm, 10*mm, 10*mm, 10*mm, 10*mm, 12*mm,
                  10*mm, 8*mm, 14*mm, 16*mm, 14*mm, 18*mm, 14*mm]

    header = [
        Paragraph('<b>S.No</b>', s), Paragraph('<b>Code</b>', s), Paragraph('<b>Name</b>', s),
        Paragraph('<b>Model</b>', s), Paragraph('<b>Make</b>', s), Paragraph('<b>Category</b>', s),
        Paragraph('<b>Status</b>', s), Paragraph('<b>Unit</b>', s),
        Paragraph('<b>Rcvd</b>', s), Paragraph('<b>Issued</b>', s), Paragraph('<b>Balance</b>', s),
        Paragraph('<b>Min</b>', s), Paragraph('<b>Cost</b>', s), Paragraph('<b>Value</b>', s),
        Paragraph('<b>TAG</b>', s), Paragraph('<b>Rack</b>', s),
        Paragraph('<b>Rcvd Date</b>', s), Paragraph('<b>Handover</b>', s),
        Paragraph('<b>H.Date</b>', s), Paragraph('<b>Project</b>', s), Paragraph('<b>Remarks</b>', s),
    ]
    data = [header]
    low_stock_rows = []

    for idx, item in enumerate(items):
        row_data = [
            Paragraph(str(item.serial_number), s),
            Paragraph(item.item_code or '', s),
            Paragraph(item.name, s),
            Paragraph(item.model_number or '', s),
            Paragraph(item.make or '', s),
            Paragraph(item.get_category_display(), s),
            Paragraph(item.get_status_display(), s),
            Paragraph(item.unit, s),
            Paragraph(f'{item.quantity_received:,.0f}', sr),
            Paragraph(f'{item.quantity_issued:,.0f}', sr),
            Paragraph(f'{item.balance_qty:,.0f}' if item.balance_qty is not None else '-', sr),
            Paragraph(f'{item.min_stock_level:,.0f}' if item.min_stock_level else '-', sr),
            Paragraph(f'{item.unit_cost:,.2f}' if item.unit_cost else '-', sr),
            Paragraph(f'{item.total_value:,.2f}', sr),
            Paragraph(item.tag or '', s),
            Paragraph(item.rack_location or '', s),
            Paragraph(item.received_date.strftime('%d-%m-%y') if item.received_date else '', s),
            Paragraph(item.handover_to or '', s),
            Paragraph(item.handover_date.strftime('%d-%m-%y') if item.handover_date else '', s),
            Paragraph(item.project_ref or '', s),
            Paragraph(item.remarks or '', s),
        ]
        data.append(row_data)
        if item.is_low_stock:
            low_stock_rows.append(idx + 1)

    table = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), dark_blue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]
    for r in low_stock_rows:
        style_cmds.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#FFE0E0')))
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)

    doc.build(elements)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type='application/pdf')
    filename = _safe_filename(report.title, prefix='Inventory', extension='pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─── Inventory Import Excel ───────────────────────────────────

@login_required
def inventory_import_excel(request):
    if request.method != 'POST':
        return redirect('procurement:inventory_list')

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        messages.error(request, 'Please select an Excel file.')
        return redirect('procurement:inventory_list')

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        created_count = 0

        for ws in wb.worksheets:
            if ws.max_row < 3:
                continue

            # Create report per sheet
            report = InventoryReport.objects.create(
                title=ws.title,
                created_by=request.user,
            )

            # Find header row
            header_row = None
            for r in range(1, min(5, ws.max_row + 1)):
                val = str(ws.cell(row=r, column=1).value or '').lower().strip()
                if val in ('no', 'sr.no.', 'sr.no', 's.no', 's.no.', 'sr'):
                    header_row = r
                    break
            if not header_row:
                header_row = 1

            item_count = 0
            for r in range(header_row + 1, ws.max_row + 1):
                sn = ws.cell(row=r, column=1).value
                name = ws.cell(row=r, column=2).value
                if not name:
                    continue
                name_str = str(name).strip()
                if not name_str:
                    continue

                def cv(col):
                    v = ws.cell(row=r, column=col).value
                    return str(v).strip() if v is not None else ''

                def nv(col):
                    v = ws.cell(row=r, column=col).value
                    if isinstance(v, (int, float)):
                        return Decimal(str(v))
                    return None

                def dv(col):
                    v = ws.cell(row=r, column=col).value
                    if isinstance(v, datetime):
                        return v.date()
                    return None

                qty_received = nv(6) or Decimal('0')
                qty_issued = nv(11) or Decimal('0')
                balance = nv(12)

                InventoryItem.objects.create(
                    report=report,
                    serial_number=int(sn) if isinstance(sn, (int, float)) else item_count + 1,
                    name=name_str,
                    model_number=cv(3),
                    make=cv(4),
                    unit=cv(5) or 'Each',
                    quantity_received=qty_received,
                    quantity_issued=qty_issued,
                    balance_qty=balance if balance is not None else qty_received - qty_issued,
                    tag=cv(7),
                    rack_location=cv(8),
                    received_date=dv(9),
                    handover_to=cv(10),
                    handover_date=dv(13),
                    project_ref=cv(14),
                    remarks=cv(15),
                    order=item_count,
                )
                item_count += 1

            if item_count == 0:
                report.delete()
            else:
                created_count += 1

        messages.success(request, f'Imported {created_count} inventory sheet(s).')
        return redirect('procurement:inventory_list')

    except Exception as e:
        messages.error(request, f'Error importing: {str(e)}')
        return redirect('procurement:inventory_list')


# ═══════════════════════════════════════════════════════════════
# FRC / PPE UNIFORM TRACKER
# ═══════════════════════════════════════════════════════════════

class FRCPermissionMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_queryset(self):
        queryset = FRCReport.objects.select_related('project', 'created_by').all()
        user = self.request.user
        if user.is_super_admin_user or user.is_procurement_user:
            return queryset
        elif user.is_admin_user or user.is_manager_user:
            return queryset.filter(Q(created_by=user) | Q(project__region=user.region))
        else:
            return queryset.filter(created_by=user)


class FRCListView(FRCPermissionMixin, ListView):
    model = FRCReport
    template_name = 'procurement/frc_list.html'
    context_object_name = 'reports'
    paginate_by = 25

    def test_func(self):
        return True

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(Q(title__icontains=search))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_count'] = self.get_queryset().count()
        return context


class FRCCreateView(FRCPermissionMixin, CreateView):
    model = FRCReport
    form_class = FRCReportForm
    template_name = 'procurement/frc_form.html'

    def test_func(self):
        return True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['entry_formset'] = FRCEntryFormSet(self.request.POST, prefix='entries')
        else:
            context['entry_formset'] = FRCEntryFormSet(prefix='entries')
        context['title'] = 'Create FRC / PPE Report'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        formset = context['entry_formset']
        if formset.is_valid():
            self.object = form.save(commit=False)
            self.object.created_by = self.request.user
            self.object.save()
            formset.instance = self.object
            formset.save()
            messages.success(self.request, 'FRC Report created.')
            return redirect(self.get_success_url())
        return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:frc_detail', kwargs={'pk': self.object.pk})


class FRCDetailView(FRCPermissionMixin, DetailView):
    model = FRCReport
    template_name = 'procurement/frc_detail.html'
    context_object_name = 'report'

    def test_func(self):
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = self.object.entries.all()
        context['entries'] = entries

        # Collect unique projects and sizes for filters
        projects = set()
        shirt_sizes = set()
        pants_sizes = set()
        shoe_sizes = set()
        for e in entries:
            if e.project_name:
                projects.add(e.project_name.strip())
            if e.shirt_size:
                shirt_sizes.add(e.shirt_size.strip())
            if e.pants_size:
                pants_sizes.add(e.pants_size.strip())
            if e.safety_shoes and e.safety_shoes not in ('N/A', 'n/a', '-', ''):
                shoe_sizes.add(e.safety_shoes.strip())

        context['projects'] = sorted(projects)
        context['shirt_sizes'] = sorted(shirt_sizes)
        context['pants_sizes'] = sorted(pants_sizes)
        context['shoe_sizes'] = sorted(shoe_sizes)
        return context


class FRCUpdateView(FRCPermissionMixin, UpdateView):
    model = FRCReport
    form_class = FRCReportForm
    template_name = 'procurement/frc_form.html'

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['entry_formset'] = FRCEntryFormSet(self.request.POST, instance=self.object, prefix='entries')
        else:
            context['entry_formset'] = FRCEntryFormSet(instance=self.object, prefix='entries')
        context['title'] = f'Edit: {self.object.title}'
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        formset = context['entry_formset']
        if formset.is_valid():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            messages.success(self.request, 'FRC Report updated.')
            return redirect(self.get_success_url())
        return self.form_invalid(form)

    def get_success_url(self):
        return reverse('procurement:frc_detail', kwargs={'pk': self.object.pk})


class FRCDeleteView(FRCPermissionMixin, DeleteView):
    model = FRCReport
    template_name = 'procurement/frc_confirm_delete.html'
    success_url = reverse_lazy('procurement:frc_list')

    def test_func(self):
        user = self.request.user
        if user.is_super_admin_user or user.is_admin_user or user.is_procurement_user:
            return True
        return self.get_object().created_by == user

    def form_valid(self, form):
        messages.success(self.request, 'FRC Report deleted.')
        return super().form_valid(form)


# ─── FRC Export Excel ─────────────────────────────────────────

@login_required
def frc_export_excel(request, pk):
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    report = get_object_or_404(FRCReport, pk=pk)
    entries = report.entries.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'FRC Report'

    bold = Font(bold=True)
    header_font = Font(bold=True, color='FFFFFF', size=9)
    header_fill = PatternFill(start_color='C41E3A', end_color='C41E3A', fill_type='solid')
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    ws.cell(row=1, column=1, value=report.title).font = Font(bold=True, size=13, color='C41E3A')

    headers = ['Sr.', 'Name', 'Designation', 'Project', 'Shirt Size', 'Shirts', 'Pants Size', 'Pants',
               'Safety Glasses', 'Safety Shoes', 'Shoes Qty', 'Safety Helmet', 'Receiving Date', 'Handed To / Remarks']
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    for row_num, e in enumerate(entries, 4):
        data = [e.serial_number, e.employee_name, e.designation, e.project_name,
                e.shirt_size, e.shirt_qty, e.pants_size, e.pants_qty,
                e.safety_glasses, e.safety_shoes, e.shoes_qty, e.safety_helmet,
                e.receiving_date, e.handed_to]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col, value=val)
            cell.border = thin_border

    widths = [6, 22, 20, 16, 12, 8, 12, 8, 12, 12, 8, 12, 18, 25]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = _safe_filename(report.title, prefix='FRC', extension='xlsx')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ─── FRC Export PDF ───────────────────────────────────────────

@login_required
def frc_export_pdf(request, pk):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_CENTER
    from io import BytesIO
    from django.contrib.staticfiles.finders import find as find_static

    report = get_object_or_404(FRCReport, pk=pk)
    entries = report.entries.all()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=10*mm, bottomMargin=10*mm, leftMargin=10*mm, rightMargin=10*mm)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('FRCTitle', parent=styles['Heading1'], fontSize=14, textColor=colors.HexColor('#C41E3A'), spaceAfter=2)
    s = ParagraphStyle('S', parent=styles['Normal'], fontSize=7, leading=8)

    logo_path = find_static('images/leap_logo.jpg')
    if logo_path:
        from reportlab.platypus import Image
        logo = Image(logo_path, width=40*mm, height=12*mm)
        t = Table([[logo, Paragraph(report.title, title_style)]], colWidths=[45*mm, 230*mm])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        elements.append(t)
    else:
        elements.append(Paragraph(report.title, title_style))
    elements.append(Spacer(1, 4*mm))

    leap_red = colors.HexColor('#C41E3A')
    col_widths = [8*mm, 32*mm, 28*mm, 22*mm, 16*mm, 10*mm, 16*mm, 10*mm,
                  14*mm, 16*mm, 10*mm, 14*mm, 24*mm, 40*mm]
    header = [Paragraph(f'<b>{h}</b>', s) for h in [
        'Sr.', 'Name', 'Designation', 'Project', 'Shirt Size', 'Shirts', 'Pants Size', 'Pants',
        'Glasses', 'Shoes', 'Shoe Qty', 'Helmet', 'Recv Date', 'Handed To / Remarks'
    ]]
    data = [header]
    for e in entries:
        data.append([
            Paragraph(str(e.serial_number), s), Paragraph(e.employee_name, s),
            Paragraph(e.designation, s), Paragraph(e.project_name, s),
            Paragraph(e.shirt_size, s), Paragraph(str(e.shirt_qty), s),
            Paragraph(e.pants_size, s), Paragraph(str(e.pants_qty), s),
            Paragraph(e.safety_glasses, s), Paragraph(e.safety_shoes, s),
            Paragraph(str(e.shoes_qty), s), Paragraph(e.safety_helmet, s),
            Paragraph(e.receiving_date, s), Paragraph(e.handed_to, s),
        ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), leap_red),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FFF5F5')]),
    ]))
    elements.append(table)

    doc.build(elements)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type='application/pdf')
    filename = _safe_filename(report.title, prefix='FRC', extension='pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─── FRC Import Excel ─────────────────────────────────────────

@login_required
def frc_import_excel(request):
    if request.method != 'POST':
        return redirect('procurement:frc_list')

    excel_file = request.FILES.get('excel_file')
    if not excel_file:
        messages.error(request, 'Please select an Excel file.')
        return redirect('procurement:frc_list')

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        created_count = 0

        for ws in wb.worksheets:
            if ws.max_row < 3:
                continue

            title = str(ws.cell(row=1, column=1).value or ws.title).strip()
            report = FRCReport.objects.create(title=title or ws.title, created_by=request.user)

            # Find header row
            header_row = None
            for r in range(1, min(5, ws.max_row + 1)):
                val = str(ws.cell(row=r, column=1).value or '').lower().strip()
                if val in ('sr.', 'sr', 'sr.no', 's.no', 'sr. no'):
                    header_row = r
                    break
                val2 = str(ws.cell(row=r, column=2).value or '').lower().strip()
                if val2 == 'name':
                    header_row = r
                    break
            if not header_row:
                header_row = 2

            entry_count = 0
            for r in range(header_row + 1, ws.max_row + 1):
                name = ws.cell(row=r, column=2).value
                if not name:
                    continue
                name_str = str(name).strip()
                if not name_str:
                    continue

                def cv(col):
                    v = ws.cell(row=r, column=col).value
                    return str(v).strip() if v is not None else ''

                def iv(col):
                    """Extract quantity from values like '2', '2+1(XL)', '1(M)', '2(32)'."""
                    import re
                    v = ws.cell(row=r, column=col).value
                    if v is None:
                        return 0
                    if isinstance(v, (int, float)):
                        return int(v)
                    s = str(v).strip()
                    if not s or s in ('N/A', 'n/a', '-'):
                        return 0
                    # Split by + and extract the leading number from each part
                    # "2+1(XL)" → ["2", "1(XL)"] → 2 + 1 = 3
                    # "2(32)" → ["2(32)"] → 2
                    # "1 N+1 H" → ["1 N", "1 H"] → 1 + 1 = 2
                    total = 0
                    for part in s.split('+'):
                        part = part.strip()
                        m = re.match(r'(\d+)', part)
                        if m:
                            total += int(m.group(1))
                    return total

                sn = ws.cell(row=r, column=1).value

                # Format receiving date properly
                recv_raw = ws.cell(row=r, column=10).value
                if isinstance(recv_raw, datetime):
                    recv_date = recv_raw.strftime('%d-%b-%Y')
                else:
                    recv_date = str(recv_raw).strip() if recv_raw else ''

                FRCEntry.objects.create(
                    report=report,
                    serial_number=int(sn) if isinstance(sn, (int, float)) else entry_count + 1,
                    employee_name=name_str,
                    designation=cv(3),
                    project_name=cv(4),
                    shirt_size=cv(5),
                    shirt_qty=iv(5),
                    pants_size=cv(6),
                    pants_qty=iv(6),
                    safety_glasses=cv(7),
                    safety_shoes=cv(8),
                    shoes_qty=iv(8),
                    safety_helmet=cv(9),
                    receiving_date=recv_date,
                    handed_to=cv(11),
                    order=entry_count,
                )
                entry_count += 1

            if entry_count == 0:
                report.delete()
            else:
                created_count += 1

        messages.success(request, f'Imported {created_count} FRC report(s).')
        return redirect('procurement:frc_list')

    except Exception as e:
        messages.error(request, f'Error importing: {str(e)}')
        return redirect('procurement:frc_list')


# ═══════════════════════════════════════════════════════════════
# FRC INVENTORY (PPE STOCK)
# ═══════════════════════════════════════════════════════════════

@login_required
def frc_inventory_list(request):
    """FRC Inventory dashboard — PPE stock by type and size."""
    items = FRCInventory.objects.all()

    # Filter
    item_type = request.GET.get('type', '')
    if item_type:
        items = items.filter(item_type=item_type)

    # Group by type for dashboard cards
    type_summary = {}
    for choice_val, choice_label in FRCInventory.ITEM_TYPE_CHOICES:
        type_items = FRCInventory.objects.filter(item_type=choice_val)
        if type_items.exists():
            total_avail = sum(i.available_stock for i in type_items)
            total_purchased = sum(i.total_purchased for i in type_items)
            total_issued = sum(i.total_issued for i in type_items)
            low_count = sum(1 for i in type_items if i.is_low_stock)
            type_summary[choice_val] = {
                'label': choice_label,
                'count': type_items.count(),
                'available': total_avail,
                'purchased': total_purchased,
                'issued': total_issued,
                'low_stock': low_count,
            }

    total_value = sum(i.stock_value for i in FRCInventory.objects.all())
    total_low = sum(1 for i in FRCInventory.objects.all() if i.is_low_stock)

    context = {
        'items': items,
        'type_summary': type_summary,
        'active_type': item_type,
        'item_type_choices': FRCInventory.ITEM_TYPE_CHOICES,
        'total_value': total_value,
        'total_low': total_low,
        'total_items': FRCInventory.objects.count(),
    }
    return render(request, 'procurement/frc_inventory.html', context)


class FRCInventoryCreateView(LoginRequiredMixin, CreateView):
    model = FRCInventory
    form_class = FRCInventoryForm
    template_name = 'procurement/frc_inventory_form.html'
    success_url = reverse_lazy('procurement:frc_inventory')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add PPE Stock Item'
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'PPE stock item added.')
        return super().form_valid(form)


class FRCInventoryUpdateView(LoginRequiredMixin, UpdateView):
    model = FRCInventory
    form_class = FRCInventoryForm
    template_name = 'procurement/frc_inventory_form.html'
    success_url = reverse_lazy('procurement:frc_inventory')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit: {self.object}'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'PPE stock item updated.')
        return super().form_valid(form)


class FRCInventoryDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = FRCInventory
    template_name = 'procurement/frc_inventory_confirm_delete.html'
    success_url = reverse_lazy('procurement:frc_inventory')

    def test_func(self):
        return self.request.user.is_super_admin_user or self.request.user.is_admin_user

    def form_valid(self, form):
        messages.success(self.request, 'PPE stock item deleted.')
        return super().form_valid(form)
