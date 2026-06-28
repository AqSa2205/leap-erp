"""AI extraction of supplier quotation PDFs into a normalized structure.

Vendor quotations arrive in wildly different layouts (column order, currency,
VAT handling, multi-line descriptions, header metadata). Rather than maintain a
per-vendor parser, we extract the PDF text and ask an LLM (Claude) to map any
layout onto one schema via a forced tool call, so the result is always valid
JSON we can pre-fill the PO editor with.
"""
from django.conf import settings


# The schema the model must fill. Forcing a tool call guarantees we get this
# shape back (validated by the API) instead of free-form text we have to parse.
QUOTATION_TOOL = {
    'name': 'record_quotation',
    'description': 'Record the structured contents of a supplier quotation.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'vendor_name': {'type': 'string', 'description': 'The supplier / vendor company issuing the quotation.'},
            'vendor_contact_person': {'type': 'string'},
            'vendor_contact_email': {'type': 'string'},
            'vendor_contact_tel': {'type': 'string'},
            'quotation_reference': {'type': 'string', 'description': 'The vendor quote number / reference, if present.'},
            'project_name': {'type': 'string'},
            'currency': {
                'type': 'string',
                'enum': ['SAR', 'USD', 'EUR', 'AED'],
                'description': 'Currency of the prices. Map SR/Riyal->SAR, $/Dollar->USD, Dirham->AED.',
            },
            'vat_rate': {'type': 'number', 'description': 'VAT percentage as a number, e.g. 15 for 15%. 0 if none.'},
            'notes': {'type': 'string', 'description': 'Any important terms/validity/delivery notes, brief.'},
            'line_items': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'description': {'type': 'string', 'description': 'Full item description / specification.'},
                        'make_model': {'type': 'string', 'description': 'Brand / make / model / part number if distinguishable, else empty.'},
                        'quantity': {'type': 'number'},
                        'uom': {'type': 'string', 'description': 'Unit of measure, e.g. PCS, Each, Nos, m. Default Nos.'},
                        'unit_price': {'type': 'number', 'description': 'Price per unit BEFORE VAT, in the quotation currency.'},
                    },
                    'required': ['description', 'quantity', 'unit_price'],
                },
            },
        },
        'required': ['line_items'],
    },
}

_SYSTEM = (
    'You extract structured data from supplier quotations. The input is raw text '
    'from a PDF whose table columns may be flattened onto separate lines. Identify '
    'each priced line item with its quantity and PRE-VAT unit price. Exclude '
    'subtotal / VAT / grand-total rows and pure section headers. Use the unit '
    'price (per unit), not the line total. If a value is missing, omit it. Always '
    'call the record_quotation tool.'
)

_CURRENCY_MAP = {
    'SR': 'SAR', 'SAR': 'SAR', 'RIYAL': 'SAR', 'SR.': 'SAR',
    'USD': 'USD', '$': 'USD', 'DOLLAR': 'USD',
    'EUR': 'EUR', '€': 'EUR', 'EURO': 'EUR',
    'AED': 'AED', 'DIRHAM': 'AED',
}


def extract_text_from_pdf(file_obj):
    """Return the concatenated text of a PDF file-like object (best effort)."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        from PyPDF2 import PdfReader
    try:
        file_obj.seek(0)
    except Exception:
        pass
    reader = PdfReader(file_obj)
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or '')
        except Exception:
            continue
    return '\n'.join(parts)


def _coerce_currency(value):
    if not value:
        return 'SAR'
    return _CURRENCY_MAP.get(str(value).strip().upper(), value if value in ('SAR', 'USD', 'EUR', 'AED') else 'SAR')


def _normalize(data):
    """Clean the raw tool output into the shape the review form expects."""
    data = data or {}
    items = []
    for raw in data.get('line_items', []):
        desc = (raw.get('description') or '').strip()
        if not desc:
            continue
        try:
            qty = float(raw.get('quantity') or 0)
        except (TypeError, ValueError):
            qty = 0
        try:
            price = float(raw.get('unit_price') or 0)
        except (TypeError, ValueError):
            price = 0
        items.append({
            'description': desc,
            'make_model': (raw.get('make_model') or '').strip(),
            'quantity': qty,
            'uom': (raw.get('uom') or 'Nos').strip() or 'Nos',
            'unit_price': price,
        })
    return {
        'vendor_name': (data.get('vendor_name') or '').strip(),
        'vendor_contact_person': (data.get('vendor_contact_person') or '').strip(),
        'vendor_contact_email': (data.get('vendor_contact_email') or '').strip(),
        'vendor_contact_tel': (data.get('vendor_contact_tel') or '').strip(),
        'quotation_reference': (data.get('quotation_reference') or '').strip(),
        'project_name': (data.get('project_name') or '').strip(),
        'currency': _coerce_currency(data.get('currency')),
        'vat_rate': data.get('vat_rate') if isinstance(data.get('vat_rate'), (int, float)) else 15,
        'notes': (data.get('notes') or '').strip(),
        'line_items': items,
    }


def extract_quotation(text, *, model=None):
    """Call Claude to turn quotation text into the normalized dict.

    Returns (data, model_used). Raises RuntimeError with a readable message on
    misconfiguration / API failure so the caller can mark the import failed.
    """
    text = (text or '').strip()
    if not text:
        raise RuntimeError('No readable text found in the PDF (it may be a scanned image).')

    key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not key:
        raise RuntimeError('AI extraction is not configured — set ANTHROPIC_API_KEY.')

    model = model or getattr(settings, 'PROCUREMENT_AI_MODEL', None) \
        or getattr(settings, 'DEVTRACKING_AI_MODEL', 'claude-sonnet-4-6')

    try:
        import anthropic
        # Bound the call: a slow/hung request must raise a catchable timeout
        # (-> status 'failed' with a reason) rather than run until the web
        # worker is killed, which would strand the import at 'pending'. The
        # client timeout must stay below the gunicorn worker timeout.
        client = anthropic.Anthropic(api_key=key, timeout=100.0, max_retries=1)
        # Cap the text so a huge multi-page quote stays within budget.
        prompt = f'Extract the quotation below.\n\n<quotation>\n{text[:60000]}\n</quotation>'
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            system=_SYSTEM,
            tools=[QUOTATION_TOOL],
            tool_choice={'type': 'tool', 'name': 'record_quotation'},
            messages=[{'role': 'user', 'content': prompt}],
        )
    except Exception as e:  # network / auth / SDK errors
        raise RuntimeError(f'AI extraction failed: {e}') from e

    tool_input = None
    for block in msg.content:
        if getattr(block, 'type', None) == 'tool_use' and getattr(block, 'name', '') == 'record_quotation':
            tool_input = block.input
            break
    if tool_input is None:
        raise RuntimeError('The model did not return structured quotation data.')

    return _normalize(tool_input), model
