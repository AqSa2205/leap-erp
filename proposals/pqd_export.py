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
    """Generate the PQD body (cover page + text sections) directly as PDF
    using reportlab — no DOCX conversion required. Returns PDF bytes.
    Produces a clean, consistent document regardless of platform."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, PageBreak, Table, TableStyle,
    )
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

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
    GREY = colors.HexColor('#666666')

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=20*mm, rightMargin=20*mm,
                          topMargin=25*mm, bottomMargin=22*mm)
    frame = Frame(20*mm, 22*mm, A4[0]-40*mm, A4[1]-47*mm, showBoundary=0)

    def _on_page(canvas, _doc):
        page_w, page_h = A4
        canvas.saveState()
        # Top banner
        if logo_path and os.path.exists(logo_path):
            try:
                canvas.drawImage(logo_path, 20*mm, page_h - 20*mm,
                                 width=95, height=32, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        canvas.setStrokeColor(LEAP_RED)
        canvas.setLineWidth(2)
        canvas.line(20*mm, page_h - 22*mm, page_w - 20*mm, page_h - 22*mm)
        # Reference in top-right
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(GREY)
        canvas.drawRightString(page_w - 20*mm, page_h - 15*mm, pqd.pqd_reference or '')
        # Footer
        canvas.setFont('Helvetica', 7)
        canvas.drawString(20*mm, 12*mm,
            'Confidential. \u00A9 Leap Networks. All rights reserved.')
        canvas.drawRightString(page_w - 20*mm, 12*mm, f'Page {_doc.page}')
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id='main', frames=[frame], onPage=_on_page)])

    # Styles — Trebuchet falls back to Helvetica if not registered
    title_style = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=22,
                                 leading=26, alignment=TA_CENTER, textColor=LEAP_RED,
                                 spaceAfter=8)
    subtitle_style = ParagraphStyle('Subtitle', fontName='Helvetica', fontSize=14,
                                    leading=18, alignment=TA_CENTER, textColor=colors.black,
                                    spaceAfter=6)
    meta_label = ParagraphStyle('MetaLabel', fontName='Helvetica-Bold', fontSize=10,
                                textColor=GREY, alignment=TA_LEFT)
    meta_value = ParagraphStyle('MetaValue', fontName='Helvetica', fontSize=10,
                                textColor=colors.black, alignment=TA_LEFT)
    section_heading = ParagraphStyle('SectionHeading', fontName='Helvetica-Bold',
                                     fontSize=16, leading=20, textColor=LEAP_RED,
                                     spaceBefore=12, spaceAfter=8)
    body_style = ParagraphStyle('Body', fontName='Helvetica', fontSize=11,
                                leading=16, alignment=TA_JUSTIFY, spaceAfter=6)

    elements = []

    # COVER PAGE
    elements.append(Spacer(1, 30*mm))
    elements.append(Paragraph('PREQUALIFICATION DOCUMENT', title_style))
    elements.append(Spacer(1, 10*mm))
    elements.append(Paragraph(pqd.title or '', subtitle_style))
    elements.append(Spacer(1, 20*mm))

    # Metadata table
    from datetime import datetime
    rev_date = pqd.revision_date.strftime('%d %B %Y') if pqd.revision_date else ''
    meta_rows = [
        [Paragraph('Reference:', meta_label), Paragraph(pqd.pqd_reference or '', meta_value)],
        [Paragraph('Client:', meta_label), Paragraph(pqd.client_name or '', meta_value)],
        [Paragraph('Project Description:', meta_label), Paragraph(pqd.project_description or '', meta_value)],
        [Paragraph('Region:', meta_label), Paragraph(pqd.get_region_display_name() or '', meta_value)],
        [Paragraph('Document Type:', meta_label), Paragraph(pqd.document_type or '', meta_value)],
        [Paragraph('Revision:', meta_label), Paragraph(pqd.revision or '', meta_value)],
        [Paragraph('Revision Date:', meta_label), Paragraph(rev_date, meta_value)],
        [Paragraph('Prepared By:', meta_label), Paragraph(pqd.prepared_by_initials or '', meta_value)],
        [Paragraph('Checked By:', meta_label), Paragraph(pqd.checked_by_initials or '—', meta_value)],
        [Paragraph('Approved By:', meta_label), Paragraph(pqd.approved_by_initials or '—', meta_value)],
    ]
    meta_table = Table(meta_rows, colWidths=[50*mm, (A4[0] - 40*mm) - 50*mm])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, -1), 0.25, colors.HexColor('#dddddd')),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 30*mm))
    elements.append(Paragraph('Submitted by<br/><b>Leap Networks</b>', subtitle_style))
    elements.append(PageBreak())

    # Content sections
    section_number = 1
    for key, label in pqd.TEXT_SECTION_FIELDS:
        content = getattr(pqd, key, '') or ''
        # Only include sections that have text content (uploads are appended later)
        if not content.strip():
            continue
        elements.append(Paragraph(f'{section_number}. {label}', section_heading))
        # Strip outer whitespace, replace empty paragraphs
        html = content.replace('&nbsp;', ' ')
        # reportlab supports a subset of HTML; feed it paragraphs directly
        # For safety, split on </p> boundaries so each paragraph is a separate flowable
        import re
        chunks = re.split(r'(<p[^>]*>.*?</p>|<h[1-6][^>]*>.*?</h[1-6]>|<ul[^>]*>.*?</ul>|<ol[^>]*>.*?</ol>|<table[^>]*>.*?</table>)', html, flags=re.DOTALL)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            # Strip block-level tags for reportlab and treat as paragraphs
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
                    # Fallback: strip all tags
                    plain = re.sub(r'<[^>]+>', '', text)
                    if plain.strip():
                        elements.append(Paragraph(plain, body_style))
        elements.append(Spacer(1, 6))
        section_number += 1

    if not elements or len(elements) < 5:
        # Always have at least the cover page
        pass

    doc.build(elements)
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
