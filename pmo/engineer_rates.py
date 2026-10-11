"""Engineer Rate Table - the cost and billing-rate build-up per engineer.

Replicates the source workbook's "Engineer Rate Tables" sheet:

    additional   = Iqama + Medical + Air Ticket + ESB + GOSI   (monthly)
    overhead     = base salary x overhead %
    fully loaded = base salary + overhead + additional
    daily cost   = fully loaded / 26,       hourly cost = daily / 8
    billing      = fully loaded x (1 + profit markup %)
    daily billing = billing / 26,           hourly billing = daily billing / 8

Iqama, the air ticket and GOSI-Expat apply only to an expat; GOSI-Saudi only
to a Saudi; medical, ESB and overhead to everyone - the same split as
DesignationManpowerLine. Nothing here is stored: every figure is worked out
from the grade line and the project's assumptions each time, so a changed
salary or rate is right everywhere at once.
"""
from decimal import Decimal

from .models import GradeStructureLine

WORKING_DAYS_PER_MONTH = Decimal('26')
WORKING_HOURS_PER_DAY = Decimal('8')
ZERO = Decimal('0')
TWELVE = Decimal('12')
HUNDRED = Decimal('100')

# The additional-cost components, in the sheet's column order.
COMPONENT_KEYS = ('iqama', 'medical', 'ticket', 'esb', 'gosi_expat', 'gosi_saudi')
# Everything rate_build_up returns that a totals row adds up.
TOTAL_KEYS = COMPONENT_KEYS + (
    'additional', 'overhead', 'loaded', 'daily', 'hourly',
    'bill_monthly', 'bill_daily', 'bill_hourly')


def rate_build_up(salary, is_expat, assumptions):
    """Every figure for one engineer, as a dict of Decimals (full precision;
    templates round for display)."""
    a = assumptions
    salary = Decimal(salary or 0)
    iqama = a.iqama_annual / TWELVE if is_expat else ZERO
    medical = a.medical_annual / TWELVE
    ticket = a.ticket_annual / TWELVE if is_expat else ZERO
    esb = salary * a.esb_pct / HUNDRED
    gosi_expat = salary * a.gosi_expat_pct / HUNDRED if is_expat else ZERO
    gosi_saudi = ZERO if is_expat else salary * a.gosi_saudi_pct / HUNDRED
    additional = iqama + medical + ticket + esb + gosi_expat + gosi_saudi
    overhead = salary * a.overhead_pct / HUNDRED
    loaded = salary + overhead + additional
    bill_monthly = loaded * (1 + a.profit_markup_pct / HUNDRED)
    return {
        'iqama': iqama, 'medical': medical, 'ticket': ticket, 'esb': esb,
        'gosi_expat': gosi_expat, 'gosi_saudi': gosi_saudi,
        'additional': additional, 'overhead': overhead, 'loaded': loaded,
        'daily': loaded / WORKING_DAYS_PER_MONTH,
        'hourly': loaded / WORKING_DAYS_PER_MONTH / WORKING_HOURS_PER_DAY,
        'bill_monthly': bill_monthly,
        'bill_daily': bill_monthly / WORKING_DAYS_PER_MONTH,
        'bill_hourly': bill_monthly / WORKING_DAYS_PER_MONTH / WORKING_HOURS_PER_DAY,
    }


def _grade_key(code):
    """E-1 < E-2 < E-10, not E-1 < E-10 < E-2."""
    code = code or ''
    prefix, _sep, tail = code.rpartition('-')
    try:
        return (prefix.upper(), int(tail), code)
    except ValueError:
        return (code.upper(), 0, code)


def _discipline_key(name):
    """The sheet's own order (Telecom, Security, AI, ...) first, anything else
    typed in after it alphabetically."""
    order = {d.lower(): i for i, d in enumerate(GradeStructureLine.DISCIPLINE_SUGGESTIONS)}
    return (order.get(name.lower(), len(order)), name.lower())


def build_rate_tables(lines, assumptions):
    """Everything the Engineer Rate Table page shows, from the project's
    engineering grade lines:

    - tables: one per discipline, each with its rows (line + figures)
    - rows: every row in table order (the additional-costs table)
    - grade_codes / matrix: the hourly billing rate matrix
    - totals: the additional-costs table's TOTAL row
    - monthly_equivalents: the annual assumptions / 12
    """
    rows = []
    for line in lines:
        rows.append({
            'line': line,
            'discipline': (line.discipline or '').strip() or 'Unassigned',
            'is_expat': line.nationality == GradeStructureLine.NATIONALITY_EXPAT,
            'calc': rate_build_up(
                line.indicative_monthly_salary,
                line.nationality == GradeStructureLine.NATIONALITY_EXPAT,
                assumptions),
        })
    rows.sort(key=lambda r: (_discipline_key(r['discipline']), _grade_key(r['line'].grade_code),
                             r['line'].order, r['line'].pk or 0))

    tables = []
    for r in rows:
        if not tables or tables[-1]['discipline'] != r['discipline']:
            tables.append({'discipline': r['discipline'], 'rows': []})
        tables[-1]['rows'].append(r)

    # One cell per discipline x grade. Should a discipline have two lines on
    # the same grade, the matrix shows the first; the tables show both.
    grade_codes = sorted({r['line'].grade_code for r in rows}, key=_grade_key)
    matrix = []
    for table in tables:
        by_grade = {}
        for r in table['rows']:
            by_grade.setdefault(r['line'].grade_code, r)
        matrix.append({
            'discipline': table['discipline'],
            'cells': [
                {'pk': by_grade[g]['line'].pk, 'value': by_grade[g]['calc']['bill_hourly']}
                if g in by_grade else None
                for g in grade_codes],
        })

    totals = {'salary': sum((Decimal(r['line'].indicative_monthly_salary or 0) for r in rows), ZERO)}
    for key in TOTAL_KEYS:
        totals[key] = sum((r['calc'][key] for r in rows), ZERO)

    a = assumptions
    return {
        'tables': tables,
        'rows': rows,
        'grade_codes': grade_codes,
        'matrix': matrix,
        'totals': totals,
        'monthly_equivalents': {
            'iqama': a.iqama_annual / TWELVE,
            'medical': a.medical_annual / TWELVE,
            'ticket': a.ticket_annual / TWELVE,
        },
    }
