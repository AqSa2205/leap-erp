"""PQD export: build a body DOCX (reusing the technical proposal template)
with the 7 PQD sections, convert to PDF, and merge the uploaded attachments
(PDF / Word / PowerPoint / images) into a single final PDF.

Conversion strategy (tried in order):
1. docx2pdf (uses MS Word on Windows — works for local dev)
2. libreoffice --headless (Linux production)
3. Fallback: return a ZIP of the body DOCX + all attachments
"""
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from django.http import HttpResponse, FileResponse
from django.conf import settings
from django.utils.text import slugify

from .docx_export import (
    _find_template, _strip_highlights, _replace_cover_page,
    _replace_textboxes_in_part, _replace_body_sections,
    _inject_images, WNS,
)
from lxml import etree


# ── Build body DOCX using PQD data (mirrors TechnicalProposal export) ──

def _build_pqd_docx(pqd):
    """Generate a DOCX for the PQD body by reusing the template and
    replacing cover page + section content. Returns bytes."""
    template_path = _find_template()
    if template_path is None:
        return None

    with open(template_path, 'rb') as f:
        template_bytes = f.read()

    rev_date = pqd.revision_date
    date_dash = rev_date.strftime('%b-%Y').upper() if rev_date else ''
    date_slash = rev_date.strftime('%b/%Y').upper() if rev_date else ''

    textbox_replacements = {}
    textbox_replacements['LNUK-IRL02125070 TECHNICAL PROP'] = (
        f'{pqd.pqd_reference} {pqd.document_type.upper()[:15]}'
    )
    textbox_replacements['LNUK-IRL02125070'] = pqd.pqd_reference
    textbox_replacements['OCT-2025'] = date_dash
    textbox_replacements['OCT/2025'] = date_slash
    textbox_replacements['MERIDIAN CONSTRUCTION'] = pqd.client_name.upper()
    textbox_replacements['PROVISIONING OF CCTV, IIS, ACS AND FTTH SYSTEM SOLUTION'] = (
        pqd.project_description.upper() if pqd.project_description else ''
    )
    textbox_replacements['PRELIMINARY - TECHNICAL PROPOSAL'] = pqd.document_type.upper()
    textbox_replacements['A00'] = pqd.revision
    textbox_replacements['UNITED KINGDOM'] = pqd.get_region_display_name().upper()

    exact_replacements = {}
    if pqd.prepared_by_initials:
        exact_replacements['AJ'] = pqd.prepared_by_initials.upper()
    if pqd.checked_by_initials:
        exact_replacements['AI'] = pqd.checked_by_initials.upper()
    if pqd.approved_by_initials:
        exact_replacements['AZ'] = pqd.approved_by_initials.upper()

    textbox_replacements = dict(
        sorted(textbox_replacements.items(), key=lambda x: len(x[0]), reverse=True)
    )

    # Fake TechnicalProposal-like attributes so _replace_body_sections /
    # _replace_cover_page work with the PQD's data. Only the attributes they
    # actually read need to exist.
    class ProposalShim:
        pass

    shim = ProposalShim()
    shim.proposal_reference = pqd.pqd_reference
    shim.project_description = pqd.project_description or ''
    shim.client_name = pqd.client_name or ''
    shim.end_user = pqd.client_name or ''
    shim.document_type = pqd.document_type or 'Prequalification'
    # Map PQD text sections to the names the body replacer looks for.
    # The template's Heading1 matcher looks at these field names:
    #   'covering_letter', 'executive_summary', 'company_overview',
    #   'understanding_of_requirements', 'proposed_technical_solution', ...
    # We repurpose three of them for the PQD text sections:
    # Map all 7 PQD text sections onto the technical proposal template's
    # 10 heading slots. Unused slots are left blank.
    shim.covering_letter = ''
    shim.executive_summary = ''
    shim.company_overview = pqd.company_profile or ''
    shim.understanding_of_requirements = pqd.list_of_material or ''
    shim.proposed_technical_solution = pqd.product_catalogues or ''
    shim.delivery_implementation = pqd.government_documents or ''
    shim.risk_management = pqd.iso_certificates or ''
    shim.service_management = pqd.qualifications or ''
    shim.data_protection = ''
    shim.assumptions_constraints = pqd.list_of_projects or ''
    # Engineering Documents — empty for PQD
    class _EmptyMgr:
        def all(self): return []
    shim.engineering_documents = _EmptyMgr()

    image_registry = []
    output = io.BytesIO()
    modified_files = {}
    with zipfile.ZipFile(io.BytesIO(template_bytes), 'r') as zin:
        all_names = set(zin.namelist())
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == 'word/header1.xml':
                root = etree.fromstring(data)
                _strip_highlights(root)
                _replace_textboxes_in_part(root, textbox_replacements, exact_replacements)
                data = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
            elif item.filename == 'word/footer1.xml':
                root = etree.fromstring(data)
                _strip_highlights(root)
                _replace_textboxes_in_part(root, textbox_replacements, exact_replacements)
                data = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
            elif item.filename == 'word/document.xml':
                root = etree.fromstring(data)
                _strip_highlights(root)
                body = root.find(f'.//{{{WNS}}}body')
                _replace_cover_page(body, shim)
                _replace_textboxes_in_part(root, textbox_replacements, exact_replacements)
                data = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
                data = _replace_body_sections(data, shim, image_registry)
            modified_files[item.filename] = data

        if image_registry:
            _inject_images(modified_files, image_registry, all_names)

        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zout:
            written = set()
            for item in zin.infolist():
                zout.writestr(item, modified_files.get(item.filename, zin.read(item.filename)))
                written.add(item.filename)
            for name, data in modified_files.items():
                if name not in written:
                    zout.writestr(name, data)

    output.seek(0)
    return output.getvalue()


# ── DOCX / PPTX / image → PDF conversion helpers ──

def _docx_to_pdf_via_docx2pdf(docx_bytes):
    """Convert using MS Word (docx2pdf). Windows only, requires Word."""
    try:
        import docx2pdf
        import pythoncom  # noqa: F401
    except ImportError:
        return None

    tmp_dir = tempfile.mkdtemp(prefix='pqd_')
    try:
        in_path = os.path.join(tmp_dir, 'input.docx')
        out_path = os.path.join(tmp_dir, 'input.pdf')
        with open(in_path, 'wb') as f:
            f.write(docx_bytes)
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                docx2pdf.convert(in_path, out_path)
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            print(f'docx2pdf failed: {e}')
            return None
        if not os.path.exists(out_path):
            return None
        with open(out_path, 'rb') as f:
            return f.read()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _docx_to_pdf_via_libreoffice(docx_bytes, original_ext='docx'):
    """Convert using LibreOffice headless. Works on Linux with libreoffice installed."""
    for cmd_name in ('soffice', 'libreoffice'):
        if shutil.which(cmd_name):
            binary = cmd_name
            break
    else:
        return None

    tmp_dir = tempfile.mkdtemp(prefix='pqd_')
    try:
        in_path = os.path.join(tmp_dir, f'input.{original_ext}')
        with open(in_path, 'wb') as f:
            f.write(docx_bytes)
        try:
            result = subprocess.run(
                [binary, '--headless', '--convert-to', 'pdf', '--outdir', tmp_dir, in_path],
                capture_output=True, timeout=120,
            )
        except Exception as e:
            print(f'libreoffice failed: {e}')
            return None
        out_path = os.path.join(tmp_dir, 'input.pdf')
        if not os.path.exists(out_path):
            return None
        with open(out_path, 'rb') as f:
            return f.read()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _convert_to_pdf(file_bytes, ext):
    """Convert a file to PDF based on its extension. Returns PDF bytes or None."""
    ext = ext.lower().lstrip('.')

    if ext == 'pdf':
        return file_bytes

    if ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'):
        return _image_to_pdf(file_bytes)

    if ext in ('doc', 'docx', 'ppt', 'pptx'):
        # Try docx2pdf first (Windows + Word), then libreoffice (Linux)
        pdf = _docx_to_pdf_via_docx2pdf(file_bytes)
        if pdf:
            return pdf
        pdf = _docx_to_pdf_via_libreoffice(file_bytes, original_ext=ext)
        if pdf:
            return pdf
        return None

    return None


# ── Pure-Python body PDF generator (reportlab) ──

def _generate_pqd_body_pdf(pqd):
    """Generate the PQD body as PDF using reportlab, matching the exact
    layout of the technical proposal template (outer border, vertical
    left panels, bottom info table) so the output visually reproduces
    the reference cover page without requiring MS Word or LibreOffice."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, PageBreak,
    )
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT

    # Locate logo
    try:
        from django.contrib.staticfiles import finders
        logo_path = finders.find('images/leap_logo.jpg') or finders.find('images/leap_logo.png')
    except Exception:
        logo_path = None
    if not logo_path:
        candidate = os.path.join(str(settings.BASE_DIR), 'static', 'images', 'leap_logo.jpg')
        if os.path.exists(candidate):
            logo_path = candidate

    LEAP_RED = colors.HexColor('#C41E3A')
    BORDER = colors.black
    LIGHT = colors.HexColor('#bbbbbb')

    page_w, page_h = A4
    OUT = 5*mm           # outer border inset
    LEFT_PANEL = 24*mm   # width of the left vertical strip
    BOTTOM_BAR = 22*mm   # height of the bottom info table
    content_left = OUT + LEFT_PANEL + 2*mm
    content_right = page_w - OUT - 2*mm
    content_top = page_h - OUT - 10*mm
    content_bottom = OUT + BOTTOM_BAR + 2*mm

    # Date formatting
    rev_date = pqd.revision_date
    date_slash = rev_date.strftime('%b/%Y').upper() if rev_date else ''
    date_short = rev_date.strftime('%b %Y').upper() if rev_date else ''

    ref = pqd.pqd_reference or ''
    doc_type_caps = (pqd.document_type or 'Prequalification').upper()
    # Truncate ref+type to fit the vertical side panel
    side_doc_label = f'{ref} {doc_type_caps[:15]}'.strip()

    prepared = (pqd.prepared_by_initials or '').upper()
    checked = (pqd.checked_by_initials or '').upper()
    approved = (pqd.approved_by_initials or '').upper()

    def _draw_border_frame(canvas):
        """Draw the outer border + left vertical panel + bottom info
        table that wraps every content page."""
        canvas.saveState()
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.75)

        # Outer rectangle
        canvas.rect(OUT, OUT, page_w - 2*OUT, page_h - 2*OUT, fill=0, stroke=1)

        # Left vertical strip divider
        canvas.line(OUT + LEFT_PANEL, OUT + BOTTOM_BAR, OUT + LEFT_PANEL, page_h - OUT)

        # Horizontal line above the bottom bar
        canvas.line(OUT, OUT + BOTTOM_BAR, page_w - OUT, OUT + BOTTOM_BAR)

        # Top banner strip (company name)
        banner_h = 7*mm
        canvas.line(OUT, page_h - OUT - banner_h, page_w - OUT, page_h - OUT - banner_h)
        canvas.setFont('Helvetica-Bold', 9)
        canvas.drawCentredString(page_w / 2, page_h - OUT - banner_h + 2*mm,
                                 'LEAP Networks Global Ltd.')

        # ── Left vertical panel: full layout matching reference template ──
        panel_top = page_h - OUT - banner_h
        panel_bottom = OUT + BOTTOM_BAR
        panel_x_left = OUT
        panel_x_right = OUT + LEFT_PANEL
        panel_h_total = panel_top - panel_bottom

        # Section heights (top → bottom, in mm)
        #   A: rotated info panel (PROJECT DEP / DATE / DESCRIPTION / REV)
        #   B: LNA PO / CLIENT PO (2 narrow rows)
        #   C: PREPARED / REVIEWED / APPROVED blocks
        #   D: REVISION CERTIFICATE text block
        h_A = panel_h_total * 0.38
        h_B = 8*mm * 2  # 2 narrow rows
        h_D = panel_h_total * 0.22
        h_C = panel_h_total - (h_A + h_B + h_D)

        y_A_bottom = panel_top - h_A
        y_B_bottom = y_A_bottom - h_B
        y_C_bottom = y_B_bottom - h_C
        y_D_bottom = panel_bottom  # D sits on the bottom-bar divider

        # ─── Section A: rotated info bands (top → bottom) ───
        # PROJECT DEP (sub-split), DATE, DESCRIPTION, REV
        bands_A = [
            {'label': 'PROJECT DEP', 'split_initials': (prepared, checked, approved)},
            {'label': 'DATE', 'value': date_slash},
            {'label': 'DESCRIPTION', 'value': side_doc_label},
            {'label': 'REV / REPORT NO.', 'value': pqd.revision or 'A'},
        ]
        n_A = len(bands_A)
        band_h_A = h_A / n_A
        for i, band in enumerate(bands_A):
            y_bot = panel_top - (i + 1) * band_h_A
            y_top = y_bot + band_h_A
            if i > 0:
                canvas.line(panel_x_left, y_top, panel_x_right, y_top)
            # Rotated label on the far left (5mm column)
            label_col_w = 5*mm
            canvas.saveState()
            canvas.translate(panel_x_left + label_col_w - 1.2*mm, y_bot + 2*mm)
            canvas.rotate(90)
            canvas.setFont('Helvetica-Bold', 5.5)
            canvas.drawString(0, 0, band['label'])
            canvas.restoreState()
            # Separator between label and value columns
            canvas.line(panel_x_left + label_col_w, y_bot,
                        panel_x_left + label_col_w, y_top)
            # Value area
            if 'split_initials' in band:
                # Split remaining space into 3 sub-columns (prepared / checked / approved)
                rem_w = LEFT_PANEL - label_col_w
                sub_w = rem_w / 3
                canvas.setFont('Helvetica-Bold', 6.5)
                for j, val in enumerate(band['split_initials']):
                    cx = panel_x_left + label_col_w + sub_w * j + sub_w / 2
                    if j > 0:
                        canvas.line(panel_x_left + label_col_w + sub_w * j, y_bot,
                                    panel_x_left + label_col_w + sub_w * j, y_top)
                    canvas.saveState()
                    canvas.translate(cx, y_bot + 2*mm)
                    canvas.rotate(90)
                    canvas.drawString(0, 0, (val or '')[:3])
                    canvas.restoreState()
            else:
                # Single rotated value
                val = band.get('value', '') or ''
                canvas.setFont('Helvetica-Bold', 6.5)
                canvas.saveState()
                canvas.translate(panel_x_left + label_col_w + (LEFT_PANEL - label_col_w) / 2,
                                 y_bot + 2*mm)
                canvas.rotate(90)
                canvas.drawString(0, 0, val[:34])
                canvas.restoreState()

        # ─── Section B: LNA PO / CLIENT PO rows ───
        row_h_B = h_B / 2
        # Top divider
        canvas.line(panel_x_left, y_A_bottom, panel_x_right, y_A_bottom)
        canvas.setFont('Helvetica-Bold', 6)
        for i, lbl in enumerate(['LNA PO', 'CLIENT PO']):
            y_bot = y_A_bottom - (i + 1) * row_h_B
            y_top = y_bot + row_h_B
            if i > 0:
                canvas.line(panel_x_left, y_top, panel_x_right, y_top)
            canvas.drawCentredString(panel_x_left + LEFT_PANEL / 2,
                                     y_bot + row_h_B / 2 - 1*mm, lbl)

        # ─── Section C: PREPARED / REVIEWED / APPROVED blocks ───
        canvas.line(panel_x_left, y_B_bottom, panel_x_right, y_B_bottom)
        blocks_C = [
            ('PREPARED\nBY', prepared or 'AJ'),
            ('REVIEWED\nBY', checked or 'ID'),
            ('APPROVED\nBY', approved or 'AK'),
        ]
        block_h_C = h_C / len(blocks_C)
        for i, (label, initials) in enumerate(blocks_C):
            y_bot = y_B_bottom - (i + 1) * block_h_C
            y_top = y_bot + block_h_C
            if i > 0:
                canvas.line(panel_x_left, y_top, panel_x_right, y_top)
            cx = panel_x_left + LEFT_PANEL / 2
            # Top: label (can be 2 lines)
            canvas.setFont('Helvetica', 5.5)
            lbl_lines = label.split('\n')
            for li, line in enumerate(lbl_lines):
                canvas.drawCentredString(cx, y_top - 2*mm - li * 2.2*mm, line)
            # Initials (bold, larger)
            canvas.setFont('Helvetica-Bold', 8)
            canvas.drawCentredString(cx, y_top - 9*mm, initials[:4])
            # DATE label + value
            canvas.setFont('Helvetica', 5.5)
            canvas.drawCentredString(cx, y_top - 13*mm, 'DATE')
            canvas.setFont('Helvetica', 6)
            canvas.drawCentredString(cx, y_top - 16*mm, date_short)

        # ─── Section D: REVISION CERTIFICATE block ───
        canvas.line(panel_x_left, y_C_bottom, panel_x_right, y_C_bottom)
        # Heading
        canvas.setFont('Helvetica-Bold', 5.5)
        cx = panel_x_left + LEFT_PANEL / 2
        canvas.drawCentredString(cx, y_C_bottom - 3*mm, 'REVISION CERTIFICATE:')
        # Wrapped body text — reportlab Paragraph rendered into a mini-frame
        from reportlab.lib.styles import ParagraphStyle as _PS
        from reportlab.platypus import Paragraph as _P
        from reportlab.lib.enums import TA_CENTER as _TC
        cert_style = _PS('C', fontName='Helvetica', fontSize=4.5, leading=5.5,
                         alignment=_TC, textColor=colors.black)
        cert_text = (
            'THIS INDICATES THAT REV: _______ OF THIS DOCUMENT IS COVERED, FOR '
            'ALL APPROVAL / CERTIFICATION REQUIREMENT BY THE DOCUMENT COMPLETION '
            'CERTIFICATE NO: ________ DATE ________'
        )
        para = _P(cert_text, cert_style)
        avail_w = LEFT_PANEL - 2*mm
        avail_h = y_C_bottom - y_D_bottom - 5*mm
        w, h = para.wrap(avail_w, avail_h)
        para.drawOn(canvas, panel_x_left + 1*mm, y_C_bottom - 4*mm - h)

        # ── Bottom info table ──
        # Columns: description/type | TYPE | PAGE NO. | CONTRACTOR | CLIENT
        y0 = OUT
        y1 = OUT + BOTTOM_BAR
        col_widths = [
            (page_w - 2*OUT) * 0.48,   # description
            (page_w - 2*OUT) * 0.08,   # TYPE
            (page_w - 2*OUT) * 0.10,   # PAGE NO.
            (page_w - 2*OUT) * 0.17,   # CONTRACTOR
            (page_w - 2*OUT) * 0.17,   # CLIENT
        ]
        x_pos = [OUT]
        for w in col_widths:
            x_pos.append(x_pos[-1] + w)
        # Vertical lines
        for x in x_pos[1:-1]:
            canvas.line(x, y0, x, y1)
        # Header row (top ~5mm)
        head_h = 5*mm
        canvas.line(OUT, y1 - head_h, page_w - OUT, y1 - head_h)
        canvas.setFont('Helvetica-Bold', 7)
        headers = [pqd.document_type.upper() if pqd.document_type else 'DOCUMENT',
                   'TYPE', 'PAGE NO.', 'CONTRACTOR', 'CLIENT']
        for i, h in enumerate(headers):
            canvas.drawCentredString(x_pos[i] + col_widths[i] / 2, y1 - head_h + 1.5*mm, h)
        # Second row — data
        canvas.setFont('Helvetica', 7)
        # Description lines
        desc_lines = [
            (pqd.project_description or '').upper()[:70],
            (pqd.client_name or '').upper()[:70],
            pqd.get_region_display_name().upper(),
        ]
        desc_y = y1 - head_h - 4*mm
        for i, line in enumerate(desc_lines):
            canvas.drawCentredString(x_pos[0] + col_widths[0] / 2,
                                     desc_y - i * 3.5*mm, line)
        # TYPE = DOC
        canvas.drawCentredString(x_pos[1] + col_widths[1] / 2, y0 + BOTTOM_BAR/2 - 4*mm, 'DOC')
        # PAGE NO. — total pages is attached on the canvas after build pass
        total = getattr(canvas, '_leap_total_pages', None) or '?'
        canvas.drawCentredString(x_pos[2] + col_widths[2] / 2, y0 + BOTTOM_BAR/2 - 4*mm,
                                 f'{canvas.getPageNumber()} / {total}')
        # Contractor logo
        if logo_path and os.path.exists(logo_path):
            try:
                logo_w, logo_h = 20*mm, 10*mm
                canvas.drawImage(logo_path,
                                 x_pos[3] + col_widths[3] / 2 - logo_w / 2,
                                 y0 + 2*mm,
                                 width=logo_w, height=logo_h,
                                 preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        canvas.restoreState()

    def _on_page(_canvas, _doc):
        # Border frame is drawn in NumberedCanvas.save() so it can include
        # the total page count. Nothing to do here.
        pass

    # Frame for the flowable content (inside the border, to the right of left panel)
    frame = Frame(
        content_left, content_bottom,
        content_right - content_left, content_top - content_bottom,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        showBoundary=0,
    )

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0)
    doc.addPageTemplates([PageTemplate(id='main', frames=[frame], onPage=_on_page)])

    # Custom canvas that stamps the total page count after all pages are laid out
    from reportlab.pdfgen.canvas import Canvas
    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kw):
            Canvas.__init__(self, *args, **kw)
            self._saved = []
        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()
        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._leap_total_pages = total
                _draw_border_frame(self)
                Canvas.showPage(self)
            Canvas.save(self)

    # Styles
    ref_style = ParagraphStyle('Ref', fontName='Helvetica-Bold', fontSize=16,
                               leading=20, alignment=TA_CENTER, spaceAfter=10)
    title_style = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=13,
                                 leading=17, alignment=TA_CENTER, spaceAfter=6)
    for_style = ParagraphStyle('For', fontName='Helvetica', fontSize=12,
                               leading=16, alignment=TA_CENTER, spaceAfter=6)
    client_style = ParagraphStyle('Client', fontName='Helvetica-Bold', fontSize=13,
                                  leading=17, alignment=TA_CENTER, spaceAfter=10)
    doctype_style = ParagraphStyle('DocType', fontName='Helvetica-Bold', fontSize=14,
                                   leading=18, alignment=TA_CENTER,
                                   textColor=colors.black, spaceAfter=10,
                                   underlineWidth=1)
    section_heading = ParagraphStyle('SectionHeading', fontName='Helvetica-Bold',
                                     fontSize=14, leading=18, textColor=LEAP_RED,
                                     spaceBefore=8, spaceAfter=6)
    body_style = ParagraphStyle('Body', fontName='Helvetica', fontSize=10,
                                leading=14, alignment=TA_JUSTIFY, spaceAfter=5)
    small_center = ParagraphStyle('SmallCenter', fontName='Helvetica', fontSize=10,
                                  alignment=TA_CENTER, spaceAfter=4)
    conf_style = ParagraphStyle('Conf', fontName='Helvetica', fontSize=9, leading=12,
                                alignment=TA_CENTER, textColor=LEAP_RED)

    elements = []
    elements.append(Spacer(1, 8*mm))
    elements.append(Paragraph(ref, ref_style))
    elements.append(Paragraph((pqd.project_description or pqd.title or '').upper(), title_style))
    elements.append(Paragraph('FOR', for_style))
    elements.append(Paragraph((pqd.client_name or '').upper(), client_style))
    elements.append(Paragraph(f'<u>{doc_type_caps}</u>', doctype_style))

    elements.append(Spacer(1, 20*mm))
    elements.append(Paragraph('Prepared for:', small_center))
    elements.append(Spacer(1, 30*mm))
    elements.append(Paragraph('Prepared by:', small_center))
    elements.append(Paragraph('<b>LEAP NETWORKS Global Ltd.</b>', small_center))
    elements.append(Spacer(1, 6*mm))
    elements.append(Paragraph(
        'CONFIDENTIALITY NOTICE: The ideas, data, and information contained in this '
        'proposal are the proprietary information of Leap Networks Global Ltd. The '
        'recipient of this proposal agrees to treat it as confidential and shall NOT '
        'disclose any part of it to third parties without the appropriate authorisation.',
        conf_style,
    ))
    elements.append(PageBreak())

    # Content sections
    section_number = 1
    for key, label in pqd.TEXT_SECTION_FIELDS:
        content = getattr(pqd, key, '') or ''
        if not content.strip():
            continue
        elements.append(Paragraph(f'{section_number}. {label}', section_heading))
        html = content.replace('&nbsp;', ' ')
        import re
        chunks = re.split(
            r'(<p[^>]*>.*?</p>|<h[1-6][^>]*>.*?</h[1-6]>|<ul[^>]*>.*?</ul>|<ol[^>]*>.*?</ol>|<table[^>]*>.*?</table>)',
            html, flags=re.DOTALL,
        )
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            text = re.sub(r'<h[1-6][^>]*>', '<b>', chunk)
            text = re.sub(r'</h[1-6]>', '</b>', text)
            text = re.sub(r'<ul[^>]*>|</ul>|<ol[^>]*>|</ol>', '', text)
            text = re.sub(r'<li[^>]*>', '&bull; ', text)
            text = re.sub(r'</li>', '<br/>', text)
            text = re.sub(r'<p[^>]*>|</p>', '', text)
            text = re.sub(r'<table[^>]*>.*?</table>', '[Table — see source document]', text, flags=re.DOTALL)
            if text.strip():
                try:
                    elements.append(Paragraph(text, body_style))
                except Exception:
                    plain = re.sub(r'<[^>]+>', '', text)
                    if plain.strip():
                        elements.append(Paragraph(plain, body_style))
        elements.append(Spacer(1, 6))
        section_number += 1

    doc.build(elements, canvasmaker=NumberedCanvas)
    buf.seek(0)
    return buf.getvalue()


def _image_to_pdf(image_bytes):
    """Convert an image to a single-page PDF using PIL."""
    try:
        from PIL import Image
    except ImportError:
        return None

    buf = io.BytesIO()
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ('RGBA', 'P', 'LA'):
        img = img.convert('RGB')
    img.save(buf, format='PDF', resolution=150)
    buf.seek(0)
    return buf.getvalue()


def _merge_pdfs(pdf_parts):
    """Merge a list of PDF byte strings into a single PDF. Returns bytes."""
    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        from PyPDF2 import PdfWriter, PdfReader

    writer = PdfWriter()
    for part in pdf_parts:
        if not part:
            continue
        try:
            reader = PdfReader(io.BytesIO(part))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:
            print(f'Failed to read PDF part: {e}')
            continue
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf.getvalue()


# ── Main entry point ──

def export_pqd_merged_pdf(pqd):
    """Generate the final merged PDF for a PQD.

    Uses a pure-Python body PDF (reportlab) so the output is ALWAYS a
    PDF, on any platform, with no MS Word / LibreOffice dependency.

    Pipeline:
    1. Generate body PDF via reportlab (cover page + text sections)
    2. For each attachment in section order:
       - PDF: pass through
       - Image: convert to single-page PDF via PIL
       - Word/PPT: try docx2pdf / libreoffice; list on placeholder page if both fail
    3. Merge with pypdf into a single final PDF

    Returns (pdf_bytes, filename, 'application/pdf').
    """
    ordered_sections = [
        'company_profile', 'list_of_material', 'product_catalogues',
        'government_documents', 'iso_certificates', 'qualifications',
        'list_of_projects',
    ]
    attachments = []
    for section in ordered_sections:
        for att in pqd.attachments.filter(section=section).order_by('order', 'pk'):
            attachments.append(att)

    body_pdf = _generate_pqd_body_pdf(pqd)

    pdf_parts = [body_pdf]
    skipped = []
    for att in attachments:
        try:
            with open(att.file.path, 'rb') as f:
                data = f.read()
        except Exception:
            skipped.append(att)
            continue
        pdf = _convert_to_pdf(data, att.extension)
        if pdf:
            pdf_parts.append(pdf)
        else:
            skipped.append(att)

    if skipped:
        placeholder = _generate_skipped_placeholder_pdf(skipped)
        if placeholder:
            pdf_parts.append(placeholder)

    merged = _merge_pdfs(pdf_parts)
    safe_ref = slugify(pqd.pqd_reference or '')[:80] or 'pqd'
    return merged, f'{safe_ref}.pdf', 'application/pdf'


def _generate_skipped_placeholder_pdf(skipped_attachments):
    """Generate a single-page PDF listing attachments that couldn't be converted."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.enums import TA_LEFT

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    heading = ParagraphStyle('H', fontName='Helvetica-Bold', fontSize=14,
                             textColor=colors.HexColor('#C41E3A'), spaceAfter=10)
    body = ParagraphStyle('B', fontName='Helvetica', fontSize=10,
                          leading=14, alignment=TA_LEFT, spaceAfter=4)
    els = [
        Paragraph('Supplementary Attachments', heading),
        Paragraph(
            'The following files were uploaded with this PQD but could not be '
            'embedded directly in this merged PDF (usually Word or PowerPoint '
            'files require MS Office or LibreOffice on the server). Download '
            'them individually from the PQD detail page:',
            body,
        ),
        Spacer(1, 8),
    ]
    for att in skipped_attachments:
        els.append(Paragraph(
            f'&bull; <b>{att.get_section_display()}</b>: {att.original_filename or att.file.name} '
            f'(.{att.extension})',
            body,
        ))
    doc.build(els)
    buf.seek(0)
    return buf.getvalue()


def _fallback_zip(pqd, body_docx, attachments):
    """Build a ZIP with the body DOCX + all original attachments in order."""
    safe_ref = slugify(pqd.pqd_reference or '')[:80] or 'pqd'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{safe_ref}_body.docx', body_docx)
        for i, att in enumerate(attachments, 1):
            try:
                with open(att.file.path, 'rb') as f:
                    data = f.read()
            except Exception:
                continue
            name = f'{i:03d}_{att.section}_{att.original_filename or "file"}'
            zf.writestr(name, data)
    buf.seek(0)
    return buf.getvalue(), f'{safe_ref}_package.zip', 'application/zip'


def _zip_merged_plus_failed(pqd, merged_pdf, safe_ref, failed_attachments):
    """Merged PDF + any attachments we couldn't convert, bundled as ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{safe_ref}.pdf', merged_pdf)
        zf.writestr(
            'README.txt',
            'The following attachments could not be converted to PDF and are included separately:\n\n' +
            '\n'.join(f'- {a.section}: {a.original_filename}' for a in failed_attachments) +
            '\n\nPlease review and insert them into the main PDF as needed.\n'
        )
        for i, att in enumerate(failed_attachments, 1):
            try:
                with open(att.file.path, 'rb') as f:
                    data = f.read()
            except Exception:
                continue
            name = f'attachments/{i:03d}_{att.section}_{att.original_filename or "file"}'
            zf.writestr(name, data)
    buf.seek(0)
    return buf.getvalue(), f'{safe_ref}_package.zip', 'application/zip'
