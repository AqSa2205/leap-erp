import io
import os
import copy
import zipfile
from lxml import etree
from django.http import HttpResponse
from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils.text import slugify

WNS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WPS_NS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'
VML_NS = 'urn:schemas-microsoft-com:vml'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def _find_template():
    """Locate the DOCX template file."""
    result = finders.find('docx_templates/technical_proposal_template.docx')
    if result:
        return result
    path = os.path.join(settings.BASE_DIR, 'static', 'docx_templates',
                        'technical_proposal_template.docx')
    if os.path.exists(path):
        return path
    return None


# ── Strip yellow highlights ──────────────────────────────────

def _strip_highlights(root):
    """Remove all w:highlight elements from the XML tree."""
    for highlight in root.findall(f'.//{{{WNS}}}highlight'):
        highlight.getparent().remove(highlight)


# ── Text-box run-merging replacement ─────────────────────────

def _replace_in_textbox_content(txbx_content, replacements, exact_replacements):
    """
    Join all w:t text in a txbxContent element, apply replacements,
    then put the result in the first w:t and clear the rest.

    replacements: dict of substring replacements (applied with str.replace)
    exact_replacements: dict where key must match the ENTIRE joined text
                        (for short values like initials that would otherwise
                         match inside longer words)
    """
    t_elements = txbx_content.findall(f'.//{{{WNS}}}t')
    if not t_elements:
        return

    joined = ''.join((t.text or '') for t in t_elements)
    original = joined

    # Try exact match first (for initials)
    stripped = joined.strip()
    if stripped in exact_replacements:
        joined = exact_replacements[stripped]
    else:
        # Substring replacements
        for old, new in replacements.items():
            joined = joined.replace(old, new)

    if joined == original:
        return  # nothing changed

    # Put all text in first w:t, clear the rest
    t_elements[0].text = joined
    t_elements[0].set(XML_SPACE, 'preserve')
    for t in t_elements[1:]:
        t.text = ''


def _replace_textboxes_in_part(root, replacements, exact_replacements):
    """
    Find all text boxes (WPS and VML) in an XML part and apply
    run-merging replacements to each.
    """
    # WPS text boxes: wps:txbx > w:txbxContent
    for txbx in root.iter(f'{{{WPS_NS}}}txbx'):
        txbx_content = txbx.find(f'{{{WNS}}}txbxContent')
        if txbx_content is not None:
            _replace_in_textbox_content(txbx_content, replacements, exact_replacements)

    # VML fallback text boxes: v:textbox > w:txbxContent
    for textbox in root.iter(f'{{{VML_NS}}}textbox'):
        txbx_content = textbox.find(f'{{{WNS}}}txbxContent')
        if txbx_content is not None:
            _replace_in_textbox_content(txbx_content, replacements, exact_replacements)


# ── Paragraph run-merging replacement ────────────────────────

def _replace_in_paragraph_runs(para, replacements):
    """
    Join all w:t in a paragraph's runs, apply replacements,
    put result in first w:t, clear the rest.
    """
    runs = para.findall(f'{{{WNS}}}r')
    if not runs:
        return

    t_elements = []
    for r in runs:
        t = r.find(f'{{{WNS}}}t')
        if t is not None:
            t_elements.append(t)

    if not t_elements:
        return

    joined = ''.join((t.text or '') for t in t_elements)
    original = joined

    for old, new in replacements.items():
        joined = joined.replace(old, new)

    if joined == original:
        return

    t_elements[0].text = joined
    t_elements[0].set(XML_SPACE, 'preserve')
    for t in t_elements[1:]:
        t.text = ''


# ── Cover page replacement ───────────────────────────────────

def _replace_cover_page(body, proposal):
    """
    Replace cover page text in the first ~25 paragraphs.
    """
    paragraphs = body.findall(f'{{{WNS}}}p')

    cover_replacements = {}

    # P[1]: reference (template has lowercase-L typo "LNUK-IRLl02125070")
    cover_replacements['LNUK-IRLl02125070'] = proposal.proposal_reference
    cover_replacements['LNUK-IRL02125070'] = proposal.proposal_reference

    # P[2]: project description
    cover_replacements['PROVISIONING OF CCTV, IIS, ACS AND FTTH SYSTEM SOLUTION'] = (
        proposal.project_description.upper() if proposal.project_description else ''
    )

    # P[4]: site/location -> client name
    cover_replacements['CANAL SIDE MANCHESTER'] = proposal.client_name.upper()

    # P[6]: document type
    cover_replacements['PRELIMINARY TECHNICAL PROPOSAL'] = proposal.document_type.upper()

    # Client name variants (on cover page "Prepared for:" area)
    cover_replacements['MERIDIAM Construction'] = proposal.client_name

    # Apply longest matches first to avoid partial double-replacement
    sorted_replacements = dict(
        sorted(cover_replacements.items(), key=lambda x: len(x[0]), reverse=True)
    )

    for i in range(min(25, len(paragraphs))):
        _replace_in_paragraph_runs(paragraphs[i], sorted_replacements)


# ── Body section content replacement ─────────────────────────

HEADING_TO_FIELD = {
    'covering letter': 'covering_letter',
    'executive summary': 'executive_summary',
    'company overview': 'company_overview',
    'understanding of requirements': 'understanding_of_requirements',
    'proposed technical solution': 'proposed_technical_solution',
    'delivery and implementation': 'delivery_implementation',
    'delivery': 'delivery_implementation',
    'risk management': 'risk_management',
    'service management': 'service_management',
    'data protection': 'data_protection',
    'assumptions': 'assumptions_constraints',
}

def _get_paragraph_text(para):
    return ''.join(
        (t.text or '') for t in para.findall(f'.//{{{WNS}}}t')
    ).strip()


def _get_paragraph_style(para):
    pPr = para.find(f'{{{WNS}}}pPr')
    if pPr is not None:
        pStyle = pPr.find(f'{{{WNS}}}pStyle')
        if pStyle is not None:
            return pStyle.get(f'{{{WNS}}}val', '')
    return ''


def _extract_content_formatting(source_para):
    """
    Extract paragraph properties (pPr) and run properties (rPr)
    from a content paragraph to use as a template for new paragraphs.
    Returns (pPr_element_or_None, rPr_element_or_None).
    """
    pPr = None
    source_pPr = source_para.find(f'{{{WNS}}}pPr')
    if source_pPr is not None:
        pPr = copy.deepcopy(source_pPr)
        # Remove heading style so new paragraphs aren't styled as headings
        pStyle = pPr.find(f'{{{WNS}}}pStyle')
        if pStyle is not None:
            pPr.remove(pStyle)

    rPr = None
    first_run = source_para.find(f'{{{WNS}}}r')
    if first_run is not None:
        rPr_el = first_run.find(f'{{{WNS}}}rPr')
        if rPr_el is not None:
            rPr = copy.deepcopy(rPr_el)

    return pPr, rPr


def _make_default_pPr():
    """
    Create default paragraph properties matching the template's content style:
    left indent 709 twips, line spacing 276 (1.15), justify both,
    Trebuchet MS 10pt.
    """
    pPr = etree.Element(f'{{{WNS}}}pPr')
    spacing = etree.SubElement(pPr, f'{{{WNS}}}spacing')
    spacing.set(f'{{{WNS}}}line', '276')
    spacing.set(f'{{{WNS}}}lineRule', 'auto')
    ind = etree.SubElement(pPr, f'{{{WNS}}}ind')
    ind.set(f'{{{WNS}}}left', '709')
    jc = etree.SubElement(pPr, f'{{{WNS}}}jc')
    jc.set(f'{{{WNS}}}val', 'both')
    return pPr


def _make_default_rPr():
    """Default run properties: Trebuchet MS 10pt."""
    rPr = etree.Element(f'{{{WNS}}}rPr')
    rFonts = etree.SubElement(rPr, f'{{{WNS}}}rFonts')
    rFonts.set(f'{{{WNS}}}ascii', 'Trebuchet MS')
    rFonts.set(f'{{{WNS}}}hAnsi', 'Trebuchet MS')
    sz = etree.SubElement(rPr, f'{{{WNS}}}sz')
    sz.set(f'{{{WNS}}}val', '20')
    szCs = etree.SubElement(rPr, f'{{{WNS}}}szCs')
    szCs.set(f'{{{WNS}}}val', '20')
    return rPr


def _make_content_paragraph(text, pPr_template=None, rPr_template=None):
    """Create a new w:p element with the given text, paragraph and run formatting."""
    p = etree.Element(f'{{{WNS}}}p')

    # Paragraph properties (indentation, spacing, justification)
    if pPr_template is not None:
        p.insert(0, copy.deepcopy(pPr_template))
    else:
        p.insert(0, _make_default_pPr())

    r = etree.SubElement(p, f'{{{WNS}}}r')

    # Run properties (font, size)
    if rPr_template is not None:
        r.insert(0, copy.deepcopy(rPr_template))
    else:
        r.insert(0, _make_default_rPr())

    t = etree.SubElement(r, f'{{{WNS}}}t')
    t.text = text
    t.set(XML_SPACE, 'preserve')
    return p


def _replace_body_sections(xml_bytes, proposal):
    """Replace body section content between Heading1 paragraphs.

    All Heading1 sections in the template are processed:
    - Matched headings: content replaced with user's text from the form
    - Unmatched headings (SUMMARY, OBJECTIVE, etc.): heading AND its
      content paragraphs are removed entirely so no pre-filled text
      leaks into the exported document.
    """
    root = etree.fromstring(xml_bytes)
    body = root.find(f'.//{{{WNS}}}body')
    if body is None:
        return xml_bytes

    paragraphs = list(body.findall(f'{{{WNS}}}p'))

    # All Heading1 indices
    all_heading1_indices = [
        i for i, p in enumerate(paragraphs) if _get_paragraph_style(p) == 'Heading1'
    ]

    # Classify each Heading1 as matched (has a field) or unmatched
    heading_info = []  # (index, field_name_or_None)
    for i in all_heading1_indices:
        text = _get_paragraph_text(paragraphs[i]).lower()
        matched_field = None
        for key, field_name in HEADING_TO_FIELD.items():
            if key in text:
                matched_field = field_name
                break
        heading_info.append((i, matched_field))

    # Extract formatting from first content paragraph of first heading with content
    pPr_template = None
    rPr_template = None
    for hi_idx, (start_pos, _) in enumerate(heading_info):
        end_pos = heading_info[hi_idx + 1][0] if hi_idx + 1 < len(heading_info) else len(paragraphs)
        for ci in range(start_pos + 1, min(start_pos + 3, end_pos)):
            candidate = paragraphs[ci]
            if _get_paragraph_text(candidate):
                pPr_template, rPr_template = _extract_content_formatting(candidate)
                break
        if pPr_template is not None:
            break

    # Work backwards to preserve indices
    for hi_idx in range(len(heading_info) - 1, -1, -1):
        start_pos, field_name = heading_info[hi_idx]

        # Find end position: next Heading1 or end of paragraphs
        end_pos = heading_info[hi_idx + 1][0] if hi_idx + 1 < len(heading_info) else len(paragraphs)

        # Remove existing content paragraphs (between this heading and next)
        for j in range(end_pos - 1, start_pos, -1):
            body.remove(paragraphs[j])

        if field_name is None:
            # Unmatched heading — remove the heading itself too
            body.remove(paragraphs[start_pos])
            continue

        # Matched heading — insert user content
        content = getattr(proposal, field_name, '')
        if not content:
            continue

        heading_para = paragraphs[start_pos]

        # Check if content is HTML (from TinyMCE)
        if '<p>' in content or '<table' in content or '<ul' in content or '<ol' in content:
            _insert_html_content(body, heading_para, content, pPr_template, rPr_template)
        else:
            for line in content.split('\n'):
                new_para = _make_content_paragraph(line, pPr_template, rPr_template)
                heading_para.addnext(new_para)
                heading_para = new_para

    # Fill engineering documents table
    _fill_engineering_table(body, proposal)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _insert_html_content(body, after_elem, html_content, pPr_template, rPr_template):
    """Convert HTML from TinyMCE to DOCX paragraphs and insert after the given element."""
    import re
    from html import unescape

    def _strip_tags(text):
        """Remove HTML tags and decode entities."""
        return unescape(re.sub(r'<[^>]+>', '', text)).strip()

    current = after_elem

    # Simple HTML parser: split by block-level tags
    # Handle paragraphs, lists, and tables
    html_content = html_content.replace('\r\n', '\n').replace('\r', '\n')

    # Split into blocks by closing block tags
    blocks = re.split(r'(</?(?:p|ul|ol|li|table|tr|td|th|h[1-6]|br\s*/?)(?:\s[^>]*)?>)', html_content)

    in_list = False
    list_type = None  # 'ul' or 'ol'
    list_counter = 0

    full_text = ''
    for block in blocks:
        if not block:
            continue

        tag_match = re.match(r'<(/?)(\w+)', block)
        if tag_match:
            is_closing = tag_match.group(1) == '/'
            tag = tag_match.group(2).lower()

            if tag == 'ul' and not is_closing:
                in_list = True
                list_type = 'ul'
                list_counter = 0
            elif tag == 'ol' and not is_closing:
                in_list = True
                list_type = 'ol'
                list_counter = 0
            elif tag in ('ul', 'ol') and is_closing:
                in_list = False
            elif tag == 'li' and is_closing and full_text.strip():
                list_counter += 1
                prefix = f'{list_counter}. ' if list_type == 'ol' else '\u2022 '
                text = prefix + _strip_tags(full_text)
                new_para = _make_content_paragraph(text, pPr_template, rPr_template)
                current.addnext(new_para)
                current = new_para
                full_text = ''
            elif tag == 'p' and is_closing and full_text.strip():
                text = _strip_tags(full_text)
                new_para = _make_content_paragraph(text, pPr_template, rPr_template)
                current.addnext(new_para)
                current = new_para
                full_text = ''
            elif tag in ('p', 'li') and not is_closing:
                full_text = ''
            elif tag == 'br':
                full_text += '\n'
            continue

        full_text += block

    # Remaining text
    remaining = _strip_tags(full_text)
    if remaining:
        new_para = _make_content_paragraph(remaining, pPr_template, rPr_template)
        current.addnext(new_para)
        current = new_para


# ── Engineering documents table ───────────────────────────────

def _fill_engineering_table(body, proposal):
    """Find the engineering documents table and replace its data rows."""
    eng_docs = list(proposal.engineering_documents.all())
    if not eng_docs:
        return

    tables = body.findall(f'.//{{{WNS}}}tbl')
    for tbl in tables:
        rows = tbl.findall(f'{{{WNS}}}tr')
        if len(rows) < 2:
            continue

        # Check header row
        header_text = ''.join(
            (t.text or '') for t in rows[0].findall(f'.//{{{WNS}}}t')
        ).lower()
        if 'document' not in header_text:
            continue

        # Copy cell formatting from a data row (row[2], skipping separator)
        cell_templates = []
        if len(rows) > 2:
            data_row = rows[2]
            for tc in data_row.findall(f'{{{WNS}}}tc'):
                tcPr = tc.find(f'{{{WNS}}}tcPr')
                rPr = None
                first_run = tc.find(f'.//{{{WNS}}}r')
                if first_run is not None:
                    rPr_el = first_run.find(f'{{{WNS}}}rPr')
                    if rPr_el is not None:
                        rPr = copy.deepcopy(rPr_el)
                cell_templates.append({
                    'tcPr': copy.deepcopy(tcPr) if tcPr is not None else None,
                    'rPr': rPr,
                })

        # Remove all rows except header
        for row in rows[1:]:
            tbl.remove(row)

        # Add new rows
        for doc in eng_docs:
            row = etree.SubElement(tbl, f'{{{WNS}}}tr')
            for ci, cell_text in enumerate([doc.doc_type, doc.doc_number, doc.doc_title]):
                tc = etree.SubElement(row, f'{{{WNS}}}tc')
                if ci < len(cell_templates) and cell_templates[ci]['tcPr'] is not None:
                    tc.insert(0, copy.deepcopy(cell_templates[ci]['tcPr']))
                p = etree.SubElement(tc, f'{{{WNS}}}p')
                r = etree.SubElement(p, f'{{{WNS}}}r')
                if ci < len(cell_templates) and cell_templates[ci]['rPr'] is not None:
                    r.insert(0, copy.deepcopy(cell_templates[ci]['rPr']))
                t = etree.SubElement(r, f'{{{WNS}}}t')
                t.text = cell_text
                t.set(XML_SPACE, 'preserve')
        break


# ── Main entry point ─────────────────────────────────────────

def generate_proposal_docx(proposal):
    """Generate a DOCX by cloning the template and replacing placeholders."""
    template_path = _find_template()
    if template_path is None:
        return _generate_fallback_docx(proposal)

    with open(template_path, 'rb') as f:
        template_bytes = f.read()

    rev_date = proposal.revision_date
    date_dash = rev_date.strftime('%b-%Y').upper() if rev_date else ''
    date_slash = rev_date.strftime('%b/%Y').upper() if rev_date else ''

    # ── Substring replacements (safe for long text boxes) ─────
    textbox_replacements = {}

    # Reference + doc type (header sidebar, joined as one string)
    textbox_replacements['LNUK-IRL02125070 TECHNICAL PROP'] = (
        f'{proposal.proposal_reference} {proposal.document_type.upper()[:15]}'
    )
    textbox_replacements['LNUK-IRL02125070'] = proposal.proposal_reference

    # Dates
    textbox_replacements['OCT-2025'] = date_dash
    textbox_replacements['OCT/2025'] = date_slash

    # Client and project (footer)
    textbox_replacements['MERIDIAN CONSTRUCTION'] = proposal.client_name.upper()
    textbox_replacements['PROVISIONING OF CCTV, IIS, ACS AND FTTH SYSTEM SOLUTION'] = (
        proposal.project_description.upper() if proposal.project_description else ''
    )
    textbox_replacements['PRELIMINARY - TECHNICAL PROPOSAL'] = proposal.document_type.upper()

    # Revision
    textbox_replacements['A00'] = proposal.revision

    # Region
    textbox_replacements['UNITED KINGDOM'] = proposal.get_region_display_name().upper()

    # ── Exact-match replacements (initials — only when the entire
    #    text box content equals the placeholder) ──────────────
    exact_replacements = {}
    if proposal.prepared_by_initials:
        exact_replacements['AJ'] = proposal.prepared_by_initials.upper()
    if proposal.checked_by_initials:
        exact_replacements['AI'] = proposal.checked_by_initials.upper()
    if proposal.approved_by_initials:
        exact_replacements['AZ'] = proposal.approved_by_initials.upper()

    # Sort by key length descending to avoid partial matches
    textbox_replacements = dict(
        sorted(textbox_replacements.items(), key=lambda x: len(x[0]), reverse=True)
    )

    # ── Process ZIP ───────────────────────────────────────────
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(template_bytes), 'r') as zin:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)

                if item.filename == 'word/header1.xml':
                    root = etree.fromstring(data)
                    _strip_highlights(root)
                    _replace_textboxes_in_part(root, textbox_replacements,
                                               exact_replacements)
                    data = etree.tostring(root, xml_declaration=True,
                                          encoding='UTF-8', standalone=True)

                elif item.filename == 'word/footer1.xml':
                    root = etree.fromstring(data)
                    _strip_highlights(root)
                    _replace_textboxes_in_part(root, textbox_replacements,
                                               exact_replacements)
                    data = etree.tostring(root, xml_declaration=True,
                                          encoding='UTF-8', standalone=True)

                elif item.filename == 'word/document.xml':
                    root = etree.fromstring(data)
                    _strip_highlights(root)

                    body = root.find(f'.//{{{WNS}}}body')

                    # 1. Replace cover page text
                    _replace_cover_page(body, proposal)

                    # 2. Replace text boxes in the body (if any)
                    _replace_textboxes_in_part(root, textbox_replacements,
                                               exact_replacements)

                    data = etree.tostring(root, xml_declaration=True,
                                          encoding='UTF-8', standalone=True)

                    # 3. Replace body sections between headings
                    data = _replace_body_sections(data, proposal)

                zout.writestr(item, data)

    output.seek(0)
    safe_ref = slugify(str(proposal.proposal_reference or ''))[:80] or 'proposal'
    filename = f"{safe_ref}.docx"
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Fallback (no template) ───────────────────────────────────

def _generate_fallback_docx(proposal):
    """Generate a simple DOCX when no template is available."""
    try:
        from docx import Document
    except ImportError:
        return HttpResponse(
            'python-docx is required for DOCX export. Install it with: pip install python-docx',
            status=500,
        )

    doc = Document()
    doc.add_heading(proposal.title, level=0)
    doc.add_paragraph(f'Reference: {proposal.proposal_reference}')
    doc.add_paragraph(f'Client: {proposal.client_name}')
    doc.add_paragraph(f'Region: {proposal.get_region_display_name()}')
    doc.add_paragraph(f'Document Type: {proposal.document_type}')
    doc.add_paragraph(f'Revision: {proposal.revision}')
    if proposal.revision_date:
        doc.add_paragraph(f'Date: {proposal.revision_date.strftime("%B %Y")}')
    doc.add_paragraph(f'Prepared by: {proposal.prepared_by_initials}')
    if proposal.checked_by_initials:
        doc.add_paragraph(f'Checked by: {proposal.checked_by_initials}')
    if proposal.approved_by_initials:
        doc.add_paragraph(f'Approved by: {proposal.approved_by_initials}')

    doc.add_page_break()

    for field_name, label in proposal.SECTION_FIELDS:
        content = getattr(proposal, field_name, '')
        if content:
            doc.add_heading(label, level=1)
            for line in content.split('\n'):
                doc.add_paragraph(line)

    eng_docs = list(proposal.engineering_documents.all())
    if eng_docs:
        doc.add_heading('Engineering Documents', level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        hdr = table.rows[0].cells
        hdr[0].text = 'Type'
        hdr[1].text = 'Number'
        hdr[2].text = 'Title'
        for ed in eng_docs:
            row = table.add_row().cells
            row[0].text = ed.doc_type
            row[1].text = ed.doc_number
            row[2].text = ed.doc_title

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    safe_ref = slugify(str(proposal.proposal_reference or ''))[:80] or 'proposal'
    filename = f"{safe_ref}.docx"
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
