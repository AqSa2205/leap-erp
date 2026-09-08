"""Building the purchase-order PDF.

Moved out of views.py unchanged. The document is the thing this module knows
about; it takes a PurchaseOrder and returns bytes, and has no idea a request
exists.

The layout rules here were each added because the document came out wrong in a
specific way for a supplier — the totals block splitting across a page break,
the price box separating from the signature, a blank page appearing before the
Terms. procurement/tests_po_pdf_layout.py pins those, and reintroducing any of
them fails that suite.
"""
from .pdf_common import (_amount_in_words, _arabic_font, _make_numbered_canvas,
                         _reportlab_style_for_line, _shape_arabic,
                         _tinymce_html_to_reportlab_lines)


def render_po_pdf(po, unpriced=False):
    """Render a purchase order to PDF bytes.

    When ``unpriced`` is True, all commercial figures are omitted — the
    Rate/Unit and Total columns, the totals block, and the amount-in-words
    line — producing a scope-only copy safe to share without revealing pricing.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.pdfgen.canvas import Canvas
    from io import BytesIO
    from django.contrib.staticfiles.finders import find as find_static

    items = po.items.all()
    is_draft = not po.is_released

    NumberedCanvas = _make_numbered_canvas(
        draft=is_draft,
        footer_left='Leap Networks Arabia',
        footer_left2=('Material Requisition ' + (po.mr_revision or '')).strip(),
        footer_center=str(po.po_number or ''),
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm, leftMargin=15*mm, rightMargin=15*mm)
    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle('POTitle', parent=styles['Heading1'], fontSize=14, textColor=colors.HexColor('#C41E3A'), spaceAfter=6)
    label_style = ParagraphStyle('Label', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
    value_style = ParagraphStyle('Value', parent=styles['Normal'], fontSize=9, fontName='Helvetica-Bold')
    normal_style = ParagraphStyle('Norm', parent=styles['Normal'], fontSize=8)
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=7)
    # Match the Costing PDF's terms size (9pt) — procurement terms were 7pt,
    # which rendered noticeably shrunk next to the sales-side documents.
    tc_style = ParagraphStyle('TC', parent=styles['Normal'], fontSize=9, leading=12)
    right_style = ParagraphStyle('Right', parent=styles['Normal'], fontSize=8, alignment=TA_RIGHT)
    right_bold = ParagraphStyle('RightBold', parent=styles['Normal'], fontSize=8, alignment=TA_RIGHT, fontName='Helvetica-Bold')
    # Qty is a count, not a money column — centred so it scans cleanly and
    # matches the PO detail table and the Excel export.
    center_style = ParagraphStyle('Center', parent=styles['Normal'], fontSize=8, alignment=TA_CENTER)
    small_center = ParagraphStyle('SmallCenter', parent=styles['Normal'], fontSize=7, alignment=TA_CENTER)

    # ── Header: company (left) · title (centre) · logo (right) ──
    company_style = ParagraphStyle('Company', parent=styles['Normal'], fontSize=11,
                                   fontName='Helvetica-Bold', leading=13, textColor=colors.black)
    sub_style = ParagraphStyle('CompanySub', parent=styles['Normal'], fontSize=8,
                               leading=11, textColor=colors.HexColor('#333333'))
    header_title_style = ParagraphStyle('HdrTitle', parent=styles['Normal'], fontSize=14,
                                        alignment=TA_CENTER, fontName='Helvetica-Bold', textColor=colors.black)

    # Left: company name (EN + AR) + address + website, stacked.
    left_cell = [Paragraph('Leap Networks Arabia', company_style)]
    ar_font = _arabic_font()
    if ar_font:
        ar_style = ParagraphStyle('CompanyAr', parent=styles['Normal'], fontSize=12,
                                  fontName=ar_font, leading=16, textColor=colors.black)
        left_cell.append(Paragraph(_shape_arabic('شركة لييب نتوركس أرابيا'), ar_style))
    left_cell.append(Paragraph('Al-Khobar, Saudi Arabia', sub_style))
    left_cell.append(Paragraph('www.leap-arabia.com', sub_style))

    # Centre: "Purchase Order" underlined in black, with any draft/unpriced note beneath.
    center_cell = [Paragraph('<u>Purchase Order</u>', header_title_style)]
    note = ''
    if is_draft:
        pending = po.current_stage['label'] if po.current_stage else 'approval'
        note = f'DRAFT (awaiting {pending})'
    if unpriced:
        note = (note + ' · ' if note else '') + 'UNPRICED'
    if note:
        note_style = ParagraphStyle('HdrNote', parent=styles['Normal'], fontSize=8,
                                    alignment=TA_CENTER, textColor=colors.HexColor('#C41E3A'))
        center_cell.append(Spacer(1, 1.5*mm))
        center_cell.append(Paragraph(note, note_style))

    # Right: logo.
    logo_path = find_static('images/leap_logo.jpg')
    if logo_path:
        from reportlab.platypus import Image
        right_cell = Image(logo_path, width=38*mm, height=11.4*mm, hAlign='RIGHT')
    else:
        right_cell = Paragraph('', normal_style)

    header_table = Table([[left_cell, center_cell, right_cell]], colWidths=[70*mm, 60*mm, 50*mm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_table)
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
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#D0D0D0')),
        # Vertical lines: only between label↔value pairs (left and right groups).
        # Skip the center spacer column so there's no line through the middle.
        ('LINEAFTER', (0, 0), (0, -1), 0.4, colors.HexColor('#E0E0E0')),
        ('LINEAFTER', (3, 0), (3, -1), 0.4, colors.HexColor('#E0E0E0')),
        # Horizontal lines between rows
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, colors.HexColor('#E0E0E0')),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 5*mm))

    # ── Line Items Table ──
    # Unpriced copies drop the Rate/Unit and Total columns; the freed width is
    # redistributed to Description and Remarks so the table still fills the page.
    if unpriced:
        col_widths = [12*mm, 30*mm, 80*mm, 16*mm, 16*mm, 31*mm]
        item_header = [
            Paragraph('<b>S.No.</b>', small_style),
            Paragraph('<b>Make/Model</b>', small_style),
            Paragraph('<b>Item Description</b>', small_style),
            Paragraph('<b>Qty</b>', small_center),
            Paragraph('<b>UOM</b>', small_style),
            Paragraph('<b>Remarks</b>', small_style),
        ]
    else:
        col_widths = [12*mm, 25*mm, 55*mm, 15*mm, 14*mm, 22*mm, 22*mm, 20*mm]
        item_header = [
            Paragraph('<b>S.No.</b>', small_style),
            Paragraph('<b>Make/Model</b>', small_style),
            Paragraph('<b>Item Description</b>', small_style),
            Paragraph('<b>Qty</b>', small_center),
            Paragraph('<b>UOM</b>', small_style),
            Paragraph('<b>Rate/Unit</b>', small_style),
            Paragraph(f'<b>Total ({po.currency})</b>', small_style),
            Paragraph('<b>Remarks</b>', small_style),
        ]
    item_data = [item_header]

    dark_blue = colors.HexColor('#C41E3A')

    for item in items:
        if unpriced:
            item_data.append([
                Paragraph(str(item.serial_number), normal_style),
                Paragraph(item.make_model or '', small_style),
                Paragraph(item.description, small_style),
                Paragraph(f'{item.quantity:,.0f}', center_style),
                Paragraph(item.uom, small_style),
                Paragraph(item.remarks or '', small_style),
            ])
        else:
            item_data.append([
                Paragraph(str(item.serial_number), normal_style),
                Paragraph(item.make_model or '', small_style),
                Paragraph(item.description, small_style),
                Paragraph(f'{item.quantity:,.0f}', center_style),
                Paragraph(item.uom, small_style),
                Paragraph(f'{item.rate_per_unit:,.2f}', right_style),
                Paragraph(f'{item.total_value:,.2f}', right_bold),
                Paragraph(item.remarks or '', small_style),
            ])

    item_table = Table(item_data, colWidths=col_widths, repeatRows=1)
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F5D7DC')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#495057')),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#C41E3A')),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 4*mm))

    # Totals + signature are kept together (one KeepTogether below) so a page
    # break never splits the price box away from the approval/signature block.
    totals_sig_flow = []
    # ── Totals ── (omitted entirely on unpriced copies)
    if not unpriced:
        totals_data = [
            ['', '', 'Base Amount', '', '', '', f'{po.base_amount:,.2f}', ''],
        ]
        if po.discount_rate:
            totals_data.append(['', '', f'Discount ({po.discount_rate:.0f}%)', '', '', '', f'-{po.discount_amount:,.2f}', ''])
        totals_data.append(['', '', 'Gross Value', '', '', '', f'{po.gross_value:,.2f}', ''])
        totals_data.append(['', '', f'VAT ({po.vat_rate:.0f}%)', '', '', '', f'{po.vat_amount:,.2f}', ''])
        totals_data.append(['', '', f'Total Value in {po.currency}', '', '', '', f'{po.total_value:,.2f}', ''])
        total_row_idx = len(totals_data) - 1  # for SPAN/style refs below

        # Amount-in-words row sits inside the same totals table so it aligns
        # to the totals column block (cols 2-6) instead of free-floating.
        amt_words_style = ParagraphStyle(
            'AmtWords', parent=styles['Normal'], fontSize=8, leading=11,
        )
        amt_words_para = Paragraph(
            f'<b>Amount in words:</b> {_amount_in_words(po.total_value, currency=po.currency)}',
            amt_words_style,
        )
        totals_data.append(['', '', amt_words_para, '', '', '', '', ''])
        amt_row_idx = len(totals_data) - 1

        totals_table = Table(totals_data, colWidths=col_widths)
        totals_table.setStyle(TableStyle([
            ('FONTNAME', (2, 0), (2, total_row_idx), 'Helvetica-Bold'),
            ('FONTNAME', (6, 0), (6, total_row_idx), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (6, 0), (6, total_row_idx), 'RIGHT'),
            ('LINEABOVE', (2, total_row_idx), (6, total_row_idx), 1, dark_blue),
            ('LINEBELOW', (2, total_row_idx), (6, total_row_idx), 1.5, dark_blue),
            ('BACKGROUND', (2, total_row_idx), (6, total_row_idx), colors.HexColor('#FBE8EC')),
            # Amount-in-words row — span across the totals column block, left-aligned,
            # vertically padded so it doesn't sit flush against the total row.
            ('SPAN', (2, amt_row_idx), (6, amt_row_idx)),
            ('VALIGN', (2, amt_row_idx), (6, amt_row_idx), 'TOP'),
            ('TOPPADDING', (2, amt_row_idx), (6, amt_row_idx), 4),
            ('BOTTOMPADDING', (2, amt_row_idx), (6, amt_row_idx), 0),
            ('TOPPADDING', (0, 0), (-1, total_row_idx), 2),
            ('BOTTOMPADDING', (0, 0), (-1, total_row_idx), 2),
        ]))
        totals_sig_flow.append(totals_table)

    # ── Approvals — rendered progressively as each stage is signed.
    # Order: SCM → PM → COO → CEO. CEO is omitted entirely for POs under
    # 1M SAR. po.approved_stages excludes unsigned stages, so a draft
    # export renders only the signatures collected so far (or no signature
    # block at all when the PO has not been signed yet).
    from xml.sax.saxutils import escape as _xml_escape

    totals_sig_flow.append(Spacer(1, 8*mm))
    approvals = po.approved_stages
    if approvals:
        from reportlab.platypus import Image as RLImage
        col_w = max(40*mm, 180*mm / max(len(approvals), 1))
        sig_h = 16*mm  # signature image height — width auto-scales

        # Row 1: signature image (or empty cell if none uploaded yet).
        # Read via FieldFile so this works with both local storage and R2.
        from io import BytesIO as _BytesIO
        sig_row = []
        for s in approvals:
            sig = s['signature']
            placed = False
            if sig:
                try:
                    sig.open('rb')
                    data = sig.read()
                    sig.close()
                    img = RLImage(_BytesIO(data),
                                  width=col_w - 6*mm, height=sig_h,
                                  kind='proportional')
                    sig_row.append(img)
                    placed = True
                except Exception:
                    pass
            if not placed:
                sig_row.append('')

        # Row 2: signature line + role label.
        sig_line = '___________________'
        label_row = [
            Paragraph(
                f'<para align="center">{sig_line}<br/><b>{_xml_escape(s["label"])}</b></para>',
                ParagraphStyle('AppRole', parent=styles['Normal'], fontSize=8, leading=10),
            )
            for s in approvals
        ]
        # Row 3: signer name (hardcoded, e.g. Shaker Alkhalifah).
        name_row = [
            Paragraph(
                f'<para align="center">Name: <u>{_xml_escape(s["signer"])}</u></para>',
                ParagraphStyle('AppName', parent=styles['Normal'], fontSize=8, leading=10),
            )
            for s in approvals
        ]
        approval_table = Table(
            [sig_row, label_row, name_row],
            colWidths=[col_w] * len(approvals),
            rowHeights=[sig_h + 2*mm, None, None],
        )
        approval_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, 0), 'BOTTOM'),
            ('VALIGN', (0, 1), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.HexColor('#C41E3A')),
        ]))
        totals_sig_flow.append(approval_table)

    # Emit the price box + signature as one keep-together unit so a page break
    # can never split them apart.
    if totals_sig_flow:
        elements.append(KeepTogether(totals_sig_flow))

    # ── Terms & Conditions ──
    from costing.models import TermsTemplate as _TermsTemplate

    # resolved_terms() applies any per-PO edits over the shared template text.
    resolved = po.resolved_terms()
    selected_terms = [e['template'] for e in resolved]
    term_text = {e['template'].pk: e['content'] for e in resolved}
    legacy_tc = (po.terms_and_conditions or '').strip()
    has_terms = bool(selected_terms or legacy_tc)

    # No spacer after the totals block: nothing follows it in the no-terms case
    # (a trailing spacer that lands at the page bottom spills over into a blank
    # last page), and in the terms case the PageBreak below supplies the gap. A
    # spacer between the totals block and that PageBreak is exactly what turns
    # the following page blank, so it must not be emitted here.
    if has_terms:
        # Terms & Conditions always begin on their own fresh page.
        elements.append(PageBreak())
        elements.append(Paragraph(
            '<b>TERMS AND CONDITIONS</b>',
            ParagraphStyle('TCHead', parent=styles['Heading2'], fontSize=10, textColor=colors.HexColor('#C41E3A'))
        ))
        elements.append(Spacer(1, 2*mm))

        _hdr_pt = po.terms_heading_font_pt or 8
        sub_hdr_style = ParagraphStyle('TCSubHdr', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=_hdr_pt, leading=_hdr_pt + 2, spaceAfter=1)

        # Content-based: each line is printed exactly as the user typed it — no
        # auto numbering. Terms still appear in the order the user selected them
        # (selected_terms is already in selection order via resolved_terms()),
        # so whatever numbering/lettering the user includes in the text is what
        # shows in the PDF.
        for tmpl in selected_terms:
            elements.append(Paragraph(f'<b>{_xml_escape(tmpl.name)}</b>', sub_hdr_style))
            _content = term_text.get(tmpl.pk, tmpl.content)
            for line, max_pt in _tinymce_html_to_reportlab_lines(_content):
                elements.append(Paragraph(line, _reportlab_style_for_line(tc_style, max_pt)))
            elements.append(Spacer(1, 1*mm))

        if legacy_tc:
            for line, max_pt in _tinymce_html_to_reportlab_lines(legacy_tc):
                elements.append(Paragraph(line, _reportlab_style_for_line(tc_style, max_pt)))
            elements.append(Spacer(1, 1*mm))

    try:
        doc.build(elements, canvasmaker=NumberedCanvas)
    except Exception:
        # Fallback build without page numbers if canvas decoration fails
        import logging, traceback
        logging.getLogger(__name__).warning(
            'PO PDF NumberedCanvas build failed:\n%s', traceback.format_exc()
        )
        buf.seek(0)
        buf.truncate()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm, leftMargin=15*mm, rightMargin=15*mm)
        doc.build(elements)
    buf.seek(0)
    return buf.read()
