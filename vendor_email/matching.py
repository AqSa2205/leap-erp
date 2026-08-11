import re

REFERENCE_PATTERN = re.compile(r'\bCS[-\s]?(\d{3,6})\b', re.IGNORECASE)


def extract_reference(subject, body=''):
    for text in (subject or '', body or ''):
        m = REFERENCE_PATTERN.search(text)
        if m:
            return f'CS-{m.group(1)}'
    return ''


def find_matching_sheet(sender_email, reference):
    """Auto-matching is intentionally disabled — every email goes to the
    manual review queue, no matter how clean the reference number looks.
    A person always makes the final call on which costing sheet it belongs
    to. `reference` is still extracted and shown to them as a hint, it's
    just never used to auto-decide."""
    if reference:
        return None, f'Reference "{reference}" found — pick the matching costing sheet below.'
    return None, 'No reference number found in the subject or body — pick the matching costing sheet below.'

def suggest_matching_sheet(sender_email, reference=''):
    """Best-guess costing sheet for a human to review — never auto-attaches
    anything. Learns from history: if this exact sender's email has been
    resolved to a costing sheet before (by a person, or auto-matched), we
    suggest the same sheet again — this is the realistic signal, since most
    vendors never include a reference number. First contact with any given
    vendor has no history to go on, so no suggestion is possible yet; a
    person picks once, and the system remembers from then on.
    """
    from costing.models import CostingSheet

    sheet_by_reference = None
    if reference:
        sheet_by_reference = CostingSheet.objects.filter(customer_reference__iexact=reference).first()

    past_sheets = list(
        CostingSheet.objects.filter(
            matched_emails__sender_email__iexact=sender_email,
            matched_emails__status__in=['matched_auto', 'resolved_manual'],
        ).distinct()
    ) if sender_email else []

    if sheet_by_reference and (not past_sheets or sheet_by_reference in past_sheets):
        return sheet_by_reference, f'Reference "{reference}" matches this sheet.'

    if len(past_sheets) == 1:
        return past_sheets[0], f'{sender_email} was matched to this sheet before.'

    if len(past_sheets) > 1:
        return None, f'{sender_email} has been matched to {len(past_sheets)} different sheets before — needs a closer look.'

    if sheet_by_reference:
        return sheet_by_reference, f'Reference "{reference}" matches this sheet.'

    return None, 'First time seeing this sender — pick manually once; the system will remember it next time.'