import re

# Matches things like "CS-2091", "Ref# CS-2091", "REF: CS 2091".
REFERENCE_PATTERN = re.compile(r'\bCS[-\s]?(\d{3,6})\b', re.IGNORECASE)


def extract_reference(subject, body=''):
    """Pull the first costing-sheet reference number out of subject/body,
    normalized to 'CS-####'. Returns '' if none found."""
    for text in (subject or '', body or ''):
        m = REFERENCE_PATTERN.search(text)
        if m:
            return f'CS-{m.group(1)}'
    return ''


def find_matching_sheet(sender_email, reference):
    """Return (sheet_or_None, reason_string).

    Two checks, either can confirm a match:
      1. Reference number in the email matches an open costing sheet's
         customer_reference, exactly.
      2. The sender's email is already on file as a vendor contact on an
         open costing sheet (from a previous VendorQuote), and that sheet
         is the ONLY open sheet that vendor is tied to right now — if
         they're on multiple open sheets, it's too ambiguous to guess.

    If both checks agree on the same sheet, or either one confidently
    finds exactly one sheet, we auto-attach. Anything else goes to review.
    """
    from costing.models import CostingSheet, VendorQuote

    sheet_by_reference = None
    if reference:
        sheet_by_reference = CostingSheet.objects.filter(customer_reference__iexact=reference).first()

    candidate_sheets_by_sender = list(
        CostingSheet.objects
        .filter(vendor_quotes__vendor_name__icontains=sender_email.split('@')[0])
        .distinct()
    ) if sender_email else []

    # Case 1: reference matches a sheet, and sender either agrees or is unknown.
    if sheet_by_reference:
        if not candidate_sheets_by_sender or sheet_by_reference in candidate_sheets_by_sender:
            return sheet_by_reference, f'Matched on reference "{reference}".'
        return None, (
            f'Reference "{reference}" matches "{sheet_by_reference.title}", but this sender is '
            f'known on a different sheet — needs a human to confirm.'
        )

    # Case 2: no usable reference, but sender is uniquely tied to one open sheet.
    if len(candidate_sheets_by_sender) == 1:
        return candidate_sheets_by_sender[0], f'Matched on known sender {sender_email} (no reference number needed).'

    if len(candidate_sheets_by_sender) > 1:
        return None, f'{sender_email} is linked to {len(candidate_sheets_by_sender)} open sheets — needs a human to pick the right one.'

    if not reference:
        return None, 'No reference number found in the subject or body, and sender is not a known vendor.'

    return None, f'Reference "{reference}" does not match any open costing sheet.'