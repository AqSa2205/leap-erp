"""
Parsing of .eml files uploaded through the "Add Emails" flow on the
Commercial Pipeline (see projects/views.py). Pure stdlib — no new
dependency, so it can't break anything already installed.

This module doesn't touch the database or any existing model; it just
turns a raw uploaded file into a plain dict + list of attachments, ready
for a view to turn into a PipelineEmail + Documents.
"""
import mimetypes
import re
import email
from email import policy
from email.utils import parseaddr, parsedate_to_datetime

from django.utils import timezone


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
            cc (str)           -- raw "Cc" header, as-is
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
    cc = msg.get('Cc', '') or ''

    sent_at = None
    date_header = msg.get('Date')
    if date_header:
        try:
            sent_at = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            sent_at = None
        else:
            # parsedate_to_datetime() returns a NAIVE datetime for a Date
            # header ending in "-0000" (vs. a genuinely tz-aware "+0000") —
            # Django then interprets that naive value using the local
            # server timezone (Asia/Riyadh here) instead of UTC, which is
            # what "-0000" actually means, so the stored time is off by
            # however far Riyadh is from UTC. Treat a naive result as UTC.
            if sent_at is not None and timezone.is_naive(sent_at):
                sent_at = timezone.make_aware(sent_at, timezone.utc)

    body_text = _extract_body_text(msg)
    attachments = _extract_attachments(msg)

    return {
        'subject': subject.strip(),
        'sender_name': sender_name.strip(),
        'sender_email': sender_email.strip(),
        'recipients': recipients.strip(),
        'cc': cc.strip(),
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


def _is_inline_part(part):
    """True for embedded content (signature logos, images pasted into the
    body) as opposed to a real attachment.

    This MUST agree with graph_mail._real_attachments(), which drops
    anything Graph flags `isInline`. The two are independent parsers over
    the same message — Graph builds the list shown in the attachment
    picker, this builds the list that actually becomes Documents — and
    _attach_email_to_project() pairs the user's per-document type choices
    to that list *by position*. If one side skips a signature logo and the
    other keeps it, every index after the logo is off by one: the logo is
    filed under the type the user picked for the first real attachment,
    and the real attachment silently falls back to the default. Outlook
    puts an inline logo in essentially every reply, so a mismatch here
    fires on the common case, not an edge case.

    Graph sets isInline from the part's disposition and its Content-ID, so
    both are checked. An explicit `attachment` disposition always wins —
    a genuine attachment is sometimes given a Content-ID as well, and that
    must not be mistaken for embedded content."""
    disposition = (part.get_content_disposition() or '').lower()
    if disposition == 'attachment':
        return False
    return disposition == 'inline' or bool(part.get('Content-ID'))


def _extract_attachments(msg):
    """Pull out every real attachment (any file type — PDF, Word, Excel,
    etc.), skipping inline content like signature logos."""
    attachments = []
    try:
        parts = msg.iter_attachments()
    except Exception:
        parts = []

    for part in parts:
        # iter_attachments() yields embedded content too, so this is what
        # actually keeps the list aligned with the picker's.
        if _is_inline_part(part):
            continue

        filename = part.get_filename() or ''
        content_type = part.get_content_type() or ''

        try:
            content = part.get_content()
        except Exception:
            continue

        if isinstance(content, str):
            content = content.encode('utf-8', errors='ignore')

        if not filename:
            ext = mimetypes.guess_extension(content_type) or ''
            filename = f'attachment_{len(attachments) + 1}{ext}'

        attachments.append({
            'filename': filename,
            'content': content,
            'content_type': content_type or 'application/octet-stream',
        })

    return attachments