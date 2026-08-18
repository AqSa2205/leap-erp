"""
Parsing of .eml files uploaded through the "Add Emails" flow on the
Commercial Pipeline (see projects/views.py). Pure stdlib — no new
dependency, so it can't break anything already installed.

This module doesn't touch the database or any existing model; it just
turns a raw uploaded file into a plain dict + list of attachments, ready
for a view to turn into a PipelineEmail + Documents.
"""
import re
import email
from email import policy
from email.utils import parseaddr, parsedate_to_datetime


class EmailParseError(Exception):
    """Raised when the uploaded file isn't a readable email."""


def parse_eml_file(file_obj):
    """
    Parse an uploaded .eml file.

    Args:
        file_obj: a file-like object opened in binary mode (e.g. Django's
            UploadedFile — file_obj.read() must return bytes).

    Returns:
        dict with keys:
            subject (str)
            sender_name (str)
            sender_email (str)
            recipients (str)   -- raw "To" header, as-is
            sent_at (datetime or None)
            body_text (str)    -- best-effort plain text of the message
            attachments (list of dict): each with
                filename (str)
                content (bytes)
                content_type (str)
    """
    raw_bytes = file_obj.read()
    if not raw_bytes:
        raise EmailParseError("The uploaded file is empty.")

    try:
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception as exc:
        raise EmailParseError(f"Could not read this as an email file: {exc}")

    subject = msg.get('Subject', '') or ''

    sender_name, sender_email = parseaddr(msg.get('From', '') or '')
    recipients = msg.get('To', '') or ''

    sent_at = None
    date_header = msg.get('Date')
    if date_header:
        try:
            sent_at = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            sent_at = None

    body_text = _extract_body_text(msg)
    attachments = _extract_pdf_attachments(msg)

    return {
        'subject': subject.strip(),
        'sender_name': sender_name.strip(),
        'sender_email': sender_email.strip(),
        'recipients': recipients.strip(),
        'sent_at': sent_at,
        'body_text': body_text,
        'attachments': attachments,
    }


def _extract_body_text(msg):
    """Best-effort plain-text body. Falls back to stripped HTML if there's
    no text/plain part."""
    try:
        body_part = msg.get_body(preferencelist=('plain',))
        if body_part is not None:
            return body_part.get_content().strip()

        html_part = msg.get_body(preferencelist=('html',))
        if html_part is not None:
            html = html_part.get_content()
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()
    except Exception:
        pass
    return ''


def _extract_pdf_attachments(msg):
    """Pull out every attachment that looks like a PDF."""
    attachments = []
    try:
        parts = msg.iter_attachments()
    except Exception:
        parts = []

    for part in parts:
        filename = part.get_filename() or ''
        content_type = part.get_content_type() or ''

        is_pdf = (
            content_type == 'application/pdf'
            or filename.lower().endswith('.pdf')
        )
        if not is_pdf:
            continue

        try:
            content = part.get_content()
        except Exception:
            continue

        if isinstance(content, str):
            content = content.encode('utf-8', errors='ignore')

        if not filename:
            filename = f'attachment_{len(attachments) + 1}.pdf'

        attachments.append({
            'filename': filename,
            'content': content,
            'content_type': content_type or 'application/pdf',
        })

    return attachments