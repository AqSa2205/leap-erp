"""Reading the milestone sheets out of the projects overview workbook.

Parsing is separated from writing so the whole thing can be planned, shown and
checked before anything is created — the same split as
`accounting/chart_import.py`. A one-off import that silently invents projects
is how five spellings of one project got into the workbook in the first place.

Sheets are matched to projects on the Leap reference (P02195, LNA-2308), never
on the project name. Name matching is exactly what produced the mismatches.
"""
import re
from decimal import Decimal, InvalidOperation

# "P02195-Milestones(JAZAN)", "LNA-2308-Milestones(MASCO) ",
# "LNA-2644-United Beta Industries" — the reference is the leading token, and
# what follows it varies too much to rely on.
SHEET_REFERENCE = re.compile(r'^\s*(P\d+|LNA[-\s]?\d+)', re.IGNORECASE)

# Machinery and lookups, not project data. Sheet3 is 1,000 rows of
# IF(n <= pct*100, 1, 0) feeding gauge charts; it has no place here.
SKIP_SHEETS = {'sheet1', 'sheet2', 'sheet3', 'dashborad', 'dashboard',
               'projects overview', 'budget', 'issue log', 'man power activity',
               'man power status', 'faults & losses prevention',
               'data validation'}

# The row under the header where the activities start is found rather than
# assumed: the sheets do not agree on how many title rows sit above it.
HEADER_MARKER = 's. no'

# Everything from here down is the sheet's own arithmetic, which this import
# replaces with computed values.
STOP_LABELS = {'total', 'pending milestones', 'total weightage of pending activities'}


def normalise_reference(value):
    """Strip a reference to just its letters and digits, uppercased.

    'LNA-2308', 'LNA 2308' and 'lna2308' are the same project written three
    ways, and all three appear.
    """
    return re.sub(r'[^A-Z0-9]', '', (value or '').upper())


def sheet_reference(title):
    """The Leap reference a milestone sheet belongs to, or None."""
    match = SHEET_REFERENCE.match(title or '')
    return normalise_reference(match.group(1)) if match else None


def is_milestone_sheet(title):
    return (title or '').strip().lower() not in SKIP_SHEETS and bool(sheet_reference(title))


def _decimal(value):
    """A weight or a fraction. Excel hands these over as floats.

    Quantising to four places is what makes 0.05 exactly 0.0500 rather than
    the binary approximation a float carries — that, not the str(), is what
    lets the weights sum. The str() only guards values whose binary error is
    large enough to survive the rounding, which weights between 0 and 1 never
    are; it is kept because it costs nothing and stops being true the moment
    this is pointed at a column of larger numbers.
    """
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value)).quantize(Decimal('0.0001'))
    except (InvalidOperation, ValueError):
        return None


def _date(value):
    return getattr(value, 'date', lambda: None)() if hasattr(value, 'date') else None


def _normalise_header(value):
    """Header text flattened for comparison — case, newlines and spacing."""
    return re.sub(r'\s+', ' ', str(value or '')).strip().lower()


# Header text → the field it holds. Matched exactly (after flattening) so
# "Activity" does not also claim "Activity Weightage", and "Completed" does not
# claim "Completed Activity Weightage".
HEADER_FIELDS = {
    's. no': 'serial',
    's.no': 'serial',
    'activity': 'activity',
    'activity weightage': 'weightage',
    'completed': 'completed',
    'completion date': 'completion_date',
}
# The invoice prerequisite column is spelt at least three ways across the
# sheets, including "Invice Pre-req", so it is matched by prefix.
INVOICE_PREFIXES = ('invoice pre', 'invice pre')


def column_map(rows, header_at):
    """Which column holds which field, read from the sheet rather than assumed.

    The sheets do not agree. JAZAN starts at column A and carries an extra
    "Value (SAR)"; MASCO starts at column B and has no such column — so the
    invoice prerequisite is at index 9 on both, and the activity is at 1 on one
    and 2 on the other. Hardcoding either set silently reads the wrong column
    on the other sheets, which is how a whole import ends up plausible and
    wrong.

    The row under the header is read too: where a sheet merges "Completion
    Status" across two columns, "Completed" and "Pending" are written beneath
    it.
    """
    mapping = {}
    for row in rows[header_at:header_at + 2]:
        for index, cell in enumerate(row):
            text = _normalise_header(cell)
            if not text:
                continue
            field = HEADER_FIELDS.get(text)
            if field is None and text.startswith(INVOICE_PREFIXES):
                field = 'invoice_prerequisite'
            # First writer wins: the header row proper takes precedence over
            # the sub-header beneath it.
            if field and field not in mapping:
                mapping[field] = index
    return mapping


def parse_sheet(rows):
    """Turn one milestone sheet's cells into a list of activities.

    `rows` is a list of tuples, as `ws.iter_rows(values_only=True)` gives them.

    Returns activities in sheet order, each a dict with `level` ('parent' or
    'child'), so the caller builds the tree from position. The typed numbering
    is used only for that shape, never for position: the MASCO sheet has two
    rows both labelled 1.1, and trusting the column would collapse them.
    """
    header_at = None
    for index, row in enumerate(rows):
        cells = [_normalise_header(c) for c in row]
        if any(c == HEADER_MARKER for c in cells):
            header_at = index
            break
    if header_at is None:
        return []

    columns = column_map(rows, header_at)
    if 'activity' not in columns or 'weightage' not in columns:
        return []

    def cell(row, field):
        index = columns.get(field)
        if index is None or len(row) <= index:
            return None
        return row[index]

    activities = []
    for row in rows[header_at + 1:]:
        activity = cell(row, 'activity')
        if activity is None or not str(activity).strip():
            continue
        label = str(activity).strip()
        if label.lower() in STOP_LABELS:
            break
        # The sub-header row repeats header words in the activity column on
        # some sheets; it is not an activity.
        if _normalise_header(label) in HEADER_FIELDS:
            continue

        serial = cell(row, 'serial')
        serial_text = '' if serial is None else str(serial).strip()
        weightage = _decimal(cell(row, 'weightage'))
        prerequisite = cell(row, 'invoice_prerequisite')
        activities.append({
            'serial': serial_text,
            # A row numbered 1.1 is a child; 1 is a parent.
            'level': 'child' if '.' in serial_text else 'parent',
            'activity': label,
            'weightage': weightage if weightage is not None else Decimal('0'),
            'completed_fraction': _decimal(cell(row, 'completed')) or Decimal('0'),
            'completion_date': _date(cell(row, 'completion_date')),
            'invoice_prerequisite': (str(prerequisite).strip()
                                     if prerequisite is not None else ''),
        })
    return activities


def match_project(reference, projects):
    """The one project a sheet reference belongs to.

    Returns (project, reason). A reference matching nothing, or matching more
    than one project, returns None with the reason — never a guess. The
    workbook's project names were unreliable precisely because somebody
    guessed once.
    """
    if not reference:
        return None, 'the sheet name carries no Leap reference'
    hits = [p for p in projects
            if normalise_reference(p.proposal_reference).startswith(reference)]
    if not hits:
        return None, f'no project has a reference starting {reference}'
    if len(hits) > 1:
        names = ', '.join(p.proposal_reference for p in hits[:4])
        return None, f'{reference} matches {len(hits)} projects ({names})'
    return hits[0], ''


def plan_workbook(workbook, projects):
    """What the import would do, without doing any of it.

    Returns a list of per-sheet results: the matched project (or why not), the
    activities parsed, and the weight problems found. Nothing is written.
    """
    results = []
    for worksheet in workbook.worksheets:
        title = worksheet.title
        if not is_milestone_sheet(title):
            continue
        reference = sheet_reference(title)
        project, reason = match_project(reference, projects)
        rows = list(worksheet.iter_rows(values_only=True))
        activities = parse_sheet(rows)
        results.append({
            'sheet': title,
            'reference': reference,
            'project': project,
            'reason': reason,
            'activities': activities,
            'problems': weight_problems(activities),
        })
    return results


def weight_problems(activities):
    """Weights in a parsed sheet that do not add up.

    The sheets follow two conventions, and both are legitimate: on MASCO the
    parent carries the weight (0.10) and its children divide it (0.05 + 0.05);
    on the ZULF sheets the parent is blank and the children carry it all. The
    one rule that holds either way is that **the leaves sum to 1.00** — the
    leaves are what completion is computed from, so that is the invariant
    worth enforcing.

    A parent that does carry a weight is additionally checked against its
    children, since disagreeing there means one of the two numbers is wrong.
    """
    problems = []
    grouped = {}
    current = None
    for entry in activities:
        if entry['level'] == 'parent':
            current = id(entry)
            grouped[current] = []
        elif current is not None:
            grouped[current].append(entry)

    parents = [a for a in activities if a['level'] == 'parent']
    leaves = []
    for entry in activities:
        if entry['level'] == 'child':
            leaves.append(entry)
        elif not grouped.get(id(entry)):
            # A top-level activity with nothing under it carries its own
            # weight and is a leaf in its own right.
            leaves.append(entry)

    for parent in parents:
        children = grouped.get(id(parent), [])
        if not children or parent['weightage'] == 0:
            continue
        total = sum((c['weightage'] for c in children), Decimal('0'))
        if abs(total - parent['weightage']) > Decimal('0.0001'):
            problems.append(
                f"{parent['activity']!r}: children sum to {total}, "
                f"but the activity carries {parent['weightage']}")

    if leaves:
        total = sum((leaf['weightage'] for leaf in leaves), Decimal('0'))
        if abs(total - Decimal('1')) > Decimal('0.0001'):
            problems.append(
                f'the activities that carry weight sum to {total}, not 1.0000')
    return problems
