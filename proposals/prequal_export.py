"""Build the combined Prequalification PDF in the LEAP framed template style
(matching 2870.docx): a cover page, a Table of Contents, and a heading/divider
page before each document — all faithfully replicated in code (no Word /
LibreOffice needed) — then the selected library PDFs merged in order.
"""
import os
from io import BytesIO

from django.conf import settings


def _logo_path():
    try:
        from django.contrib.staticfiles import finders
        p = finders.find('images/leap_logo.jpg') or finders.find('images/leap_logo.png')
        if p:
            return p
    except Exception:
        pass
    candidate = os.path.join(str(settings.BASE_DIR), 'static', 'images', 'leap_logo.jpg')
    return candidate if os.path.exists(candidate) else None


def _fields(submission):
    """Cover/frame fields from the submission, with template-style defaults."""
    region = getattr(getattr(submission, 'project', None), 'region', None)
    region_name = getattr(region, 'name', '') or ''
    company = 'LEAP Networks Arabia' if getattr(region, 'code', '') == 'LNA' else 'LEAP Networks Global Ltd.'
    date = getattr(submission, 'created_at', None)
    d_slash = date.strftime('%b/%Y').upper() if date else ''
    d_short = date.strftime('%b-%Y').upper() if date else ''
    ref = submission.reference or ''
    return {
        'ref': ref,
        'title': (submission.title or '').upper(),
        'client': (submission.client_name or '').upper(),
        'doc_type': 'PREQUALIFICATIONS',
        'region_name': region_name.upper(),
        'company': company,
        # Editable title-block values (fall back to sensible defaults).
        'revision': submission.revision or 'A',
        'desc_month': submission.description_month or d_slash,
        'desc_text': (submission.description_text or (ref + ' PREQUALIFICATIONS')).upper(),
        'cover_date': submission.cover_date or '',
        'report_no': submission.report_no or '',
        'date_short': submission.block_date or d_short,
        'prepared': submission.dep_1 or 'AJ',
        'checked': submission.dep_2 or 'ID',
        'approved': submission.dep_3 or 'AK',
    }


# Geometry (A4) — shared by the frame and the content layout.
def _geometry():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    page_w, page_h = A4
    OUT = 5 * mm
    LEFT_PANEL = page_w * 0.10     # left metadata panel = 10% of page width
    RIGHT_STRIP = 7 * mm     # right vertical confidentiality strip
    BOTTOM_BAR = 8 * mm      # slim bottom bar (no info table on this template)
    content_left = OUT + LEFT_PANEL + 2 * mm
    content_right = page_w - OUT - RIGHT_STRIP - 2 * mm
    content_top = page_h - OUT - 10 * mm
    content_bottom = OUT + BOTTOM_BAR + 2 * mm
    return dict(page_w=page_w, page_h=page_h, OUT=OUT, LEFT_PANEL=LEFT_PANEL,
                RIGHT_STRIP=RIGHT_STRIP, BOTTOM_BAR=BOTTOM_BAR,
                content_left=content_left, content_right=content_right,
                content_top=content_top, content_bottom=content_bottom, mm=mm)


def _make_frame_drawer(f, logo_path, page_offset, total):
    """Return an onPage(canvas, doc) that draws the 2870.docx framed border:
    outer rectangle, left vertical info panel and the bottom info table. Page
    numbers are absolute across the combined pack via page_offset/total."""
    from reportlab.lib import colors
    g = _geometry()
    mm = g['mm']
    page_w, page_h = g['page_w'], g['page_h']
    OUT, LEFT_PANEL, RIGHT_STRIP, BOTTOM_BAR = g['OUT'], g['LEFT_PANEL'], g['RIGHT_STRIP'], g['BOTTOM_BAR']

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.75)
        # Outer rectangle
        canvas.rect(OUT, OUT, page_w - 2 * OUT, page_h - 2 * OUT, fill=0, stroke=1)
        # Left panel + right strip dividers + bottom bar divider
        canvas.line(OUT + LEFT_PANEL, OUT, OUT + LEFT_PANEL, page_h - OUT)
        rx = page_w - OUT - RIGHT_STRIP
        canvas.line(rx, OUT, rx, page_h - OUT)
        canvas.line(OUT, OUT + BOTTOM_BAR, page_w - OUT, OUT + BOTTOM_BAR)
        # Top banner with company name (light, centred over the content area)
        banner_h = 7 * mm
        canvas.line(OUT + LEFT_PANEL, page_h - OUT - banner_h, rx, page_h - OUT - banner_h)
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(colors.HexColor('#444444'))
        canvas.drawCentredString((OUT + LEFT_PANEL + rx) / 2, page_h - OUT - banner_h + 2 * mm, f['company'])
        canvas.setFillColor(colors.black)

        # ── Left vertical panel ──
        panel_top = page_h - OUT
        panel_bottom = OUT
        px0, px1 = OUT, OUT + LEFT_PANEL
        panel_h = panel_top - panel_bottom
        h_A = panel_h * 0.42
        h_B = 8 * mm * 2
        h_D = panel_h * 0.10
        h_C = panel_h - (h_A + h_B + h_D)
        lc = 5 * mm  # rotated-label column width

        def rot_centered(x0, x1, y0, y1, text, size=6.5, bold=True):
            """Draw text rotated 90°, centered in the box [x0..x1] x [y0..y1]."""
            if not text:
                return
            canvas.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
            canvas.saveState()
            canvas.translate((x0 + x1) / 2, (y0 + y1) / 2)
            canvas.rotate(90)
            canvas.drawCentredString(0, 0, str(text)[:40])
            canvas.restoreState()

        # Section A — PROJECT DEP (3 stacked initials), DESCRIPTION (month + text),
        # DATE, REV, REPORT NO. Every label and value is centred in its cell.
        vx0 = px0 + lc  # left edge of the value area
        bands = [
            ('PROJECT DEP', 'initials', (f['prepared'], f['checked'], f['approved']), 3),
            ('DESCRIPTION', 'desc', (f['desc_month'], f['desc_text']), 3),
            ('DATE', 'value', f['cover_date'], 1),
            ('REV', 'value', f['revision'], 1),
            ('REPORT NO.', 'value', f['report_no'], 2),
        ]
        weight_total = sum(w for *_, w in bands)
        y_cursor = panel_top
        for i, (label, kind, payload, w) in enumerate(bands):
            band_h = h_A * w / weight_total
            y_top = y_cursor
            y_bot = y_cursor - band_h
            y_cursor = y_bot
            if i > 0:
                canvas.line(px0, y_top, px1, y_top)
            canvas.line(vx0, y_bot, vx0, y_top)                     # label / value divider
            rot_centered(px0, vx0, y_bot, y_top, label, size=7)     # centred label
            if kind == 'initials':
                sub_h = (y_top - y_bot) / 3
                for j, val in enumerate(payload):
                    cell_top = y_top - j * sub_h
                    cell_bot = cell_top - sub_h
                    if j > 0:
                        canvas.line(vx0, cell_top, px1, cell_top)
                    rot_centered(vx0, px1, cell_bot, cell_top, val, size=6)
            elif kind == 'desc':
                month, desc = payload
                # Narrow month/year sub-column + wide description sub-column.
                mcol = vx0 + 6 * mm
                canvas.line(mcol, y_bot, mcol, y_top)
                rot_centered(vx0, mcol, y_bot, y_top, month, size=6)
                rot_centered(mcol, px1, y_bot, y_top, desc, size=6)
            else:
                rot_centered(vx0, px1, y_bot, y_top, payload, size=6)

        # Section B — LNA PO / CLIENT PO
        y_A = panel_top - h_A
        canvas.line(px0, y_A, px1, y_A)
        rh = h_B / 2
        canvas.setFont('Helvetica-Bold', 6.5)
        for i, lbl in enumerate(['LNA PO', 'CLIENT PO']):
            y_bot = y_A - (i + 1) * rh
            if i > 0:
                canvas.line(px0, y_bot + rh, px1, y_bot + rh)
            canvas.drawCentredString(px0 + LEFT_PANEL / 2, y_bot + rh / 2 - 1 * mm, lbl)

        # Section C — PREPARED / REVIEWED / APPROVED with DATE sub-labels
        y_B = y_A - h_B
        canvas.line(px0, y_B, px1, y_B)
        blocks = [('PREPARED BY', f['prepared']), ('REVIEWED BY', f['checked']), ('APPROVED BY', f['approved'])]
        bh = h_C / len(blocks)
        for i, (label, initials) in enumerate(blocks):
            y_top = y_B - i * bh
            if i > 0:
                canvas.line(px0, y_top, px1, y_top)
            cx = px0 + LEFT_PANEL / 2
            canvas.setFont('Helvetica-Bold', 6); canvas.drawCentredString(cx, y_top - 2.6 * mm, label)
            canvas.setFont('Helvetica-Bold', 6); canvas.drawCentredString(cx, y_top - 7 * mm, initials)
            canvas.setFont('Helvetica', 6); canvas.drawCentredString(cx, y_top - 10.8 * mm, 'DATE')
            canvas.setFont('Helvetica', 6); canvas.drawCentredString(cx, y_top - 14 * mm, f['date_short'])

        # Section D — bottom label
        y_C = y_B - h_C
        canvas.line(px0, y_C, px1, y_C)
        canvas.setFont('Helvetica-Bold', 5)
        canvas.saveState(); canvas.translate(px0 + lc - 1.2 * mm, panel_bottom + 2 * mm)
        canvas.rotate(90); canvas.drawString(0, 0, 'REVISION CERTIFICATE'); canvas.restoreState()

        # ── Right vertical confidentiality strip ──
        canvas.setFont('Helvetica', 5.4)
        canvas.setFillColor(colors.HexColor('#333333'))
        canvas.saveState()
        canvas.translate(rx + RIGHT_STRIP / 2 + 1.6 * mm, OUT + 4 * mm)
        canvas.rotate(90)
        canvas.drawString(0, 0, _CONFIDENTIALITY_STRIP[:200])
        canvas.restoreState()
        canvas.setFillColor(colors.black)

        # ── Slim bottom bar: page number ──
        abs_no = page_offset + canvas.getPageNumber()
        canvas.setFont('Helvetica', 7)
        canvas.drawCentredString((OUT + LEFT_PANEL + rx) / 2, OUT + BOTTOM_BAR / 2 - 1 * mm, f'{abs_no} / {total}')
        canvas.restoreState()
    return draw


_CONFIDENTIALITY_STRIP = (
    'THE INFORMATION AND DESIGNS DISCLOSED HEREIN ARE THE SOLE PROPERTY OF LEAP '
    'NETWORKS GLOBAL. NO REPRODUCTION IN FULL OR IN PART SHALL BE OBTAINED FROM '
    'THIS DOCUMENT WITHOUT THE WRITTEN CONSENT OF ITS OWNER.'
)


def _framed_doc(buf, f, logo_path, page_offset, total):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame
    g = _geometry()
    frame = Frame(g['content_left'], g['content_bottom'],
                  g['content_right'] - g['content_left'],
                  g['content_top'] - g['content_bottom'],
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0)
    doc.addPageTemplates([PageTemplate(id='m', frames=[frame],
                                       onPage=_make_frame_drawer(f, logo_path, page_offset, total))])
    return doc


def _front_matter_pdf(submission, items, total):
    """Cover (page 1) + Table of Contents (page 2) → PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import Paragraph, Spacer, PageBreak, Table, TableStyle, Image
    g = _geometry(); mm = g['mm']
    f = _fields(submission)
    LEAP_RED = colors.HexColor('#C41E3A')
    logo = _logo_path()

    buf = BytesIO()
    doc = _framed_doc(buf, f, logo, page_offset=0, total=total)

    ref_s = ParagraphStyle('r', fontName='Helvetica-Bold', fontSize=22, leading=26, alignment=TA_CENTER, spaceAfter=14)
    title_s = ParagraphStyle('t', fontName='Helvetica-Bold', fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=6)
    for_s = ParagraphStyle('f', fontName='Helvetica', fontSize=13, alignment=TA_CENTER, spaceAfter=4)
    client_s = ParagraphStyle('c', fontName='Helvetica-Bold', fontSize=16, leading=20, alignment=TA_CENTER, spaceAfter=12)
    dt_s = ParagraphStyle('d', fontName='Helvetica-Bold', fontSize=16, alignment=TA_CENTER, spaceAfter=10)
    sc = ParagraphStyle('s', fontName='Helvetica', fontSize=11, alignment=TA_CENTER, spaceAfter=4)

    el = [Spacer(1, 12 * mm), Paragraph(f['ref'], ref_s)]
    if f['title']:
        el.append(Paragraph(f['title'], title_s))
    if f['client']:
        el += [Paragraph('FOR', for_s), Paragraph(f['client'], client_s)]
    el.append(Paragraph(f'<u>{f["doc_type"].title()}</u>', dt_s))
    el += [Spacer(1, 24 * mm), Paragraph('Prepared for:', sc), Spacer(1, 34 * mm),
           Paragraph('Prepared by:', sc), Paragraph(f'<b>{f["company"]}</b>', sc)]
    if logo:
        img = Image(logo, width=46 * mm, height=14 * mm)
        img.hAlign = 'CENTER'
        el += [Spacer(1, 4 * mm), img]
    el.append(PageBreak())

    # Table of Contents
    toc_head = ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=16, textColor=LEAP_RED, spaceAfter=10, alignment=TA_LEFT)
    el.append(Paragraph('Table of Contents', toc_head))
    rows = []
    rs = ParagraphStyle('rs', fontName='Helvetica', fontSize=11, leading=16)
    ns = ParagraphStyle('ns', fontName='Helvetica-Bold', fontSize=11, leading=16, textColor=LEAP_RED)
    for i, it in enumerate(items, 1):
        rows.append([Paragraph(str(i), ns), Paragraph(it.heading, rs)])
    if not rows:
        rows = [['', Paragraph('No documents selected.', rs)]]
    toc = Table(rows, colWidths=[14 * mm, None])
    toc.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                             ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                             ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#E0E0E0'))]))
    el.append(toc)
    doc.build(el)
    return buf.getvalue()


def _divider_pdf(submission, number, heading, page_offset, total):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import Paragraph, Spacer
    g = _geometry(); mm = g['mm']
    f = _fields(submission)
    buf = BytesIO()
    doc = _framed_doc(buf, f, _logo_path(), page_offset=page_offset, total=total)
    num_s = ParagraphStyle('n', fontName='Helvetica-Bold', fontSize=52, leading=56, alignment=TA_CENTER, textColor=colors.HexColor('#E7C3CB'))
    head_s = ParagraphStyle('h', fontName='Helvetica-Bold', fontSize=22, leading=28, alignment=TA_CENTER, textColor=colors.HexColor('#C41E3A'))
    el = [Spacer(1, 60 * mm), Paragraph(f'SECTION {number}', num_s), Spacer(1, 6 * mm), Paragraph(heading.upper(), head_s)]
    doc.build(el)
    return buf.getvalue()


def build_prequal_combined_pdf(submission):
    """Cover + TOC + (divider + document) per selected item, in the framed
    template style → one merged PDF. Returns (bytes, skipped_headings)."""
    from pypdf import PdfWriter, PdfReader

    # Read each selected document up front so we know page counts (for numbering).
    entries = []  # (item, reader, page_count)
    skipped = []
    for item in submission.selected_in_order():
        try:
            item.pdf.open('rb')
            data = item.pdf.read()
            item.pdf.close()
            reader = PdfReader(BytesIO(data))
            entries.append((item, reader, len(reader.pages)))
        except Exception:
            skipped.append(item.heading)

    total = 2 + sum(1 + pc for _, _, pc in entries)  # cover + toc + (divider + doc pages)
    writer = PdfWriter()

    front = PdfReader(BytesIO(_front_matter_pdf(submission, [e[0] for e in entries], total)))
    for page in front.pages:
        writer.add_page(page)
    running = 2

    for i, (item, reader, pc) in enumerate(entries, 1):
        div = PdfReader(BytesIO(_divider_pdf(submission, i, item.heading, running, total)))
        for page in div.pages:
            writer.add_page(page)
        running += 1
        for page in reader.pages:
            writer.add_page(page)
        running += pc

    out = BytesIO()
    writer.write(out)
    return out.getvalue(), skipped
