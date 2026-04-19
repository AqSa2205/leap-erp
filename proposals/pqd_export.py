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
    shim.covering_letter = ''  # no covering letter in PQD
    shim.executive_summary = ''
    shim.company_overview = pqd.company_profile or ''  # Company Profile → company overview slot
    shim.understanding_of_requirements = ''
    shim.proposed_technical_solution = pqd.list_of_material or ''  # List of Material → Proposed Solution slot
    shim.delivery_implementation = ''
    shim.risk_management = ''
    shim.service_management = ''
    shim.data_protection = ''
    shim.assumptions_constraints = pqd.list_of_projects or ''  # List of Projects → Assumptions slot
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

    Returns a tuple (content_bytes, filename, content_type) or
    (zip_bytes, zip_filename, 'application/zip') if PDF conversion
    isn't available (fallback).
    """
    body_docx = _build_pqd_docx(pqd)
    if body_docx is None:
        # Template missing — cannot produce body
        raise RuntimeError('PQD template not found')

    # Try to convert the body to PDF
    body_pdf = _convert_to_pdf(body_docx, 'docx')

    # Collect attachments in section order
    ordered_sections = [
        'product_catalogues',
        'government_documents',
        'iso_certificates',
        'qualifications',
    ]
    attachments = []
    for section in ordered_sections:
        for att in pqd.attachments.filter(section=section).order_by('order', 'pk'):
            attachments.append(att)

    # If body conversion failed, fall back to ZIP
    if body_pdf is None:
        return _fallback_zip(pqd, body_docx, attachments)

    # Convert each attachment to PDF and collect any that can't be converted
    pdf_parts = [body_pdf]
    failed_attachments = []
    for att in attachments:
        try:
            with open(att.file.path, 'rb') as f:
                data = f.read()
        except Exception:
            failed_attachments.append(att)
            continue
        pdf = _convert_to_pdf(data, att.extension)
        if pdf:
            pdf_parts.append(pdf)
        else:
            failed_attachments.append(att)

    merged = _merge_pdfs(pdf_parts)
    safe_ref = slugify(pqd.pqd_reference or '')[:80] or 'pqd'

    if failed_attachments:
        # Return a ZIP: merged PDF + any attachments that couldn't be converted
        return _zip_merged_plus_failed(pqd, merged, safe_ref, failed_attachments)

    filename = f'{safe_ref}.pdf'
    return merged, filename, 'application/pdf'


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
