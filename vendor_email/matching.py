import re

REFERENCE_PATTERN = re.compile(r'\bCS[-\s]?(\d{3,6})\b', re.IGNORECASE)


def extract_reference(subject, body=''):
    for text in (subject or '', body or ''):
        m = REFERENCE_PATTERN.search(text)
        if m:
            return f'CS-{m.group(1)}'
    return ''


def find_matching_sheet(sender_email, reference):
    from costing.models import CostingSheet

    sheet_by_reference = None
    if reference:
        sheet_by_reference = CostingSheet.objects.filter(customer_reference__iexact=reference).first()

    candidate_sheets_by_sender = list(
        CostingSheet.objects
        .filter(vendor_quotes__vendor_name__icontains=sender_email.split('@')[0])
        .distinct()
    ) if sender_email else []

    if sheet_by_reference:
        if not candidate_sheets_by_sender or sheet_by_reference in candidate_sheets_by_sender:
            return sheet_by_reference, f'Matched on reference "{reference}".'
        return None, (
            f'Reference "{reference}" matches "{sheet_by_reference.title}", but this sender is '
            f'known on a different sheet — needs a human to confirm.'
        )

    if len(candidate_sheets_by_sender) == 1:
        return candidate_sheets_by_sender[0], f'Matched on known sender {sender_email} (no reference number needed).'

    if len(candidate_sheets_by_sender) > 1:
        return None, f'{sender_email} is linked to {len(candidate_sheets_by_sender)} open sheets — needs a human to pick the right one.'

    if not reference:
        return None, 'No reference number found in the subject or body, and sender is not a known vendor.'

    return None, f'Reference "{reference}" does not match any open costing sheet.'