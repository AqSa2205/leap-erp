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