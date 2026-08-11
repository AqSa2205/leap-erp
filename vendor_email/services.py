from django.core.files.base import ContentFile
from django.utils import timezone

from .matching import extract_reference, find_matching_sheet
from .models import VendorEmailMessage


def process_new_messages(scenario='matched'):
    """Pull new messages (demo or live, auto-selected) and run them
    through the matcher, auto-attaching on a confident match."""
    from .mail_sources import get_reader
    from costing.models import VendorQuote

    reader = get_reader(scenario=scenario)
    created = []

    for raw in reader.fetch_new_messages():
        if VendorEmailMessage.objects.filter(message_id=raw['message_id']).exists():
            continue

        reference = extract_reference(raw['subject'], raw.get('body', ''))
        sheet, reason = find_matching_sheet(raw['sender_email'], reference)

        attachments = raw.get('attachments') or []
        first_filename, first_bytes = (attachments[0] if attachments else ('', b''))

        msg = VendorEmailMessage.objects.create(
            message_id=raw['message_id'],
            received_at=raw['received_at'],
            sender_email=raw['sender_email'],
            sender_name=raw.get('sender_name', ''),
            subject=raw['subject'],
            body_preview=(raw.get('body') or '')[:500],
            extracted_reference=reference,
            attachment_filename=first_filename,
            attachment_count=len(attachments),
            matched_sheet=sheet,
            status='matched_auto' if sheet else 'unmatched',
            match_reason=reason,
        )

        if first_bytes and first_filename:
            msg.attachment_file.save(first_filename, ContentFile(first_bytes), save=True)

        for extra_filename, extra_bytes in attachments[1:]:
            from .models import VendorEmailAttachment
            extra = VendorEmailAttachment.objects.create(message=msg, filename=extra_filename)
            extra.file.save(extra_filename, ContentFile(extra_bytes), save=True)

        if sheet:
            vq = VendorQuote.objects.create(
                sheet=sheet,
                vendor_name=raw.get('sender_name') or raw['sender_email'],
                quote_reference=reference,
                source='email_auto',
                notes=f"Auto-added from email — \"{raw['subject']}\" ({raw['sender_email']})",
            )
            msg.matched_vendor_quote = vq
            msg.save()

        created.append(msg)

    return created


def resolve_manually(message, sheet, user):
    """Human picks the right costing sheet for a message the matcher
    couldn't place."""
    from costing.models import VendorQuote

    vq = VendorQuote.objects.create(
        sheet=sheet,
        vendor_name=message.sender_name or message.sender_email,
        quote_reference=message.extracted_reference,
        source='email_manual',
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