from django.core.files.base import ContentFile
from django.utils import timezone


def resolve_manually(message, sheet, user):
    """Human picks the right costing sheet for a message the matcher
    couldn't place. Creates a VendorQuote just like a manual upload would,
    but tagged so it's clear it came from an email."""
    from costing.models import VendorQuote

    vq = VendorQuote.objects.create(
        sheet=sheet,
        vendor_name=message.sender_name or message.sender_email,
        quote_reference=message.extracted_reference,
        source='email',
        uploaded_by=user,
        notes=f'Manually matched from email — "{message.subject}" ({message.sender_email})',
    )
    if message.attachment_file:
        message.attachment_file.seek(0)
        vq.file.save(message.attachment_filename, ContentFile(message.attachment_file.read()), save=False)
        vq.original_filename = message.attachment_filename
    vq.save()

    message.matched_sheet = sheet
    message.matched_vendor_quote = vq
    message.status = 'resolved_manual'
    message.resolved_by = user
    message.resolved_at = timezone.now()
    message.save()
    return vq


def process_new_messages(scenario='matched'):
    """Pull one simulated message and run it through the matcher."""
    from .mail_sources import DemoMailReader
    from .matching import extract_reference, find_matching_sheet
    from .models import VendorEmailMessage

    reader = DemoMailReader(scenario=scenario)
    created = []

    for raw in reader.fetch_new_messages():
        reference = extract_reference(raw['subject'], raw.get('body', ''))
        sheet, reason = find_matching_sheet(raw['sender_email'], reference)

        msg = VendorEmailMessage.objects.create(
            message_id=raw['message_id'],
            received_at=raw['received_at'],
            sender_email=raw['sender_email'],
            sender_name=raw.get('sender_name', ''),
            subject=raw['subject'],
            body_preview=(raw.get('body') or '')[:500],
            extracted_reference=reference,
            attachment_filename=raw.get('attachment_filename', ''),
            matched_sheet=sheet,
            status='matched_auto' if sheet else 'unmatched',
            match_reason=reason,
        )
        created.append(msg)

    return created