"""Project Costing (Integrated) - the source workbook's sheet of the same name.

Sections A-D in the First Year Maintenance format, but:

- A (manpower) is built from the Grade Structure's Designation-wise Manpower
  roster, not typed again: designation, grade, discipline, nationality,
  monthly salary and headcount;
- A's statutory lines use the project's Employment Cost Assumptions (Iqama,
  medical, air ticket, ESB, GOSI), applied to A's own headcounts and salaries;
- accommodation (expats only) is charged by accommodation group, which comes
  from the grade category: EX, A, E = Staff; T, O = Technician;
- B, C, D are First Year Maintenance's own section B, C, D lines.

Totals cover the contract duration: recurring (monthly, or annual / 12)
amounts x months, one-time amounts once. Nothing is stored - every figure is
worked out from its sources each time.
"""
from decimal import Decimal

from .models import DesignationManpowerLine, FirstYearMaintenanceLine, GradeStructureLine

ZERO = Decimal('0')
TWELVE = Decimal('12')
HUNDRED = Decimal('100')
DAYS_PER_YEAR = Decimal('365')

GROUP_STAFF = 'Staff'
GROUP_TECHNICIAN = 'Technician'
STAFF_CATEGORIES = {
    GradeStructureLine.CATEGORY_EXECUTIVE,
    GradeStructureLine.CATEGORY_ADMIN,
    GradeStructureLine.CATEGORY_ENGINEERING,
}
CATEGORY_ORDER = {code: i for i, (code, _label) in enumerate(GradeStructureLine.CATEGORY_CHOICES)}
SECTION_A_LABEL = dict(FirstYearMaintenanceLine.SECTION_CHOICES)[FirstYearMaintenanceLine.SECTION_A]


def accommodation_group(category):
    """Staff (EX, A, E) or Technician (T, O) - which accommodation rate an
    expat in this category is charged."""
    return GROUP_STAFF if category in STAFF_CATEGORIES else GROUP_TECHNICIAN


def _grade_key(code):
    code = code or ''
    prefix, _sep, tail = code.rpartition('-')
    try:
        return (prefix.upper(), int(tail), code)
    except ValueError:
        return (code.upper(), 0, code)


def split_line(line):
    """A First Year Maintenance line as (monthly recurring, one time): monthly
    as typed, annual / 12, one-time counted once - decided by the line's own
    nature, never by its section."""
    total = Decimal(line.amount or 0) * Decimal(line.qty or 0)
    if line.nature_of_expense == FirstYearMaintenanceLine.NATURE_MONTHLY:
        return total, ZERO
    if line.nature_of_expense == FirstYearMaintenanceLine.NATURE_ANNUAL:
        return total / TWELVE, ZERO
    return ZERO, total


def build_project_costing(header, assumptions, designations, fym_lines):
    a = assumptions
    months = Decimal(header.contract_duration_months or 12)

    # A: manpower, one row per designation.
    rows = []
    for d in sorted(designations, key=lambda d: (CATEGORY_ORDER.get(d.category, 99),
                                                 _grade_key(d.grade_code), d.order, d.pk or 0)):
        heads = Decimal(d.headcount or 0)
        rate = Decimal(d.monthly_salary or 0)
        rows.append({
            'line': d, 'heads': heads, 'rate': rate, 'monthly': rate * heads,
            'is_expat': d.nationality == DesignationManpowerLine.NATIONALITY_EXPAT,
            'group': accommodation_group(d.category),
        })
    for i, r in enumerate(rows, start=1):
        r['no'] = i

    def total(key, where=lambda r: True):
        return sum((r[key] for r in rows if where(r)), ZERO)

    heads_total = total('heads')
    salaries_total = total('monthly')
    expat_heads = total('heads', lambda r: r['is_expat'])
    expat_salaries = total('monthly', lambda r: r['is_expat'])
    saudi_salaries = total('monthly', lambda r: not r['is_expat'])
    expat_staff_heads = total('heads', lambda r: r['is_expat'] and r['group'] == GROUP_STAFF)
    expat_tech_heads = total('heads', lambda r: r['is_expat'] and r['group'] == GROUP_TECHNICIAN)

    def annual(label, rate, heads, field=None):
        return {'label': label, 'nature': 'Annual', 'rate': rate, 'is_pct': False,
                'heads': heads, 'monthly': rate * heads / TWELVE, 'field': field}

    def pct(label, rate, base):
        return {'label': label, 'nature': 'Monthly', 'rate': rate, 'is_pct': True,
                'heads': None, 'monthly': base * rate / HUNDRED, 'field': None}

    statutory = [
        annual('Residence Permit (Annual) \u2014 Expat only', a.iqama_annual, expat_heads),
        annual('Medical Insurance (Annual)', a.medical_annual, heads_total),
        annual('Air Tickets (Annual) \u2014 Expat only', a.ticket_annual, expat_heads),
        annual('Accommodation / Engineer & Staff (Annual) \u2014 Expat only',
               header.accommodation_staff_annual, expat_staff_heads, 'accommodation_staff_annual'),
        annual('Accommodation / Technician & Helper (Annual) \u2014 Expat only',
               header.accommodation_technician_annual, expat_tech_heads, 'accommodation_technician_annual'),
        pct('ESB (End of Service Benefit) \u2014 % of monthly salaries', a.esb_pct, salaries_total),
        pct('GOSI - Expat (occupational hazard) \u2014 % of expat salaries', a.gosi_expat_pct, expat_salaries),
        pct('GOSI - Saudi (employer share) \u2014 % of Saudi salaries', a.gosi_saudi_pct, saudi_salaries),
    ]
    for i, s in enumerate(statutory, start=len(rows) + 1):
        s['no'] = i
    total_a = salaries_total + sum((s['monthly'] for s in statutory), ZERO)

    # B, C, D: First Year Maintenance's own lines.
    sections = []
    for code, label in FirstYearMaintenanceLine.SECTION_CHOICES:
        if code == FirstYearMaintenanceLine.SECTION_A:
            continue
        lines = []
        for i, line in enumerate(sorted((l for l in fym_lines if l.section == code),
                                        key=lambda l: (l.order, l.pk or 0)), start=1):
            monthly, one_time = split_line(line)
            lines.append({'no': i, 'line': line, 'monthly': monthly, 'one_time': one_time,
                          'amount': monthly if monthly else one_time})
        sections.append({
            'code': code, 'label': label, 'lines': lines,
            'monthly': sum((l['monthly'] for l in lines), ZERO),
            'one_time': sum((l['one_time'] for l in lines), ZERO),
            'total': sum((l['amount'] for l in lines), ZERO),
        })

    # Total costs over the contract period.
    totals = [{'code': FirstYearMaintenanceLine.SECTION_A, 'label': SECTION_A_LABEL,
               'monthly': total_a, 'one_time': ZERO}]
    totals += [{'code': s['code'], 'label': s['label'], 'monthly': s['monthly'],
                'one_time': s['one_time']} for s in sections]
    for row in totals:
        row['contract_total'] = row['monthly'] * months + row['one_time']
    grand_total = sum((row['contract_total'] for row in totals), ZERO)

    margin = header.effective_margin
    contract_value = grand_total / (1 - margin) if margin > 0 else grand_total
    vat = Decimal(header.vat_rate or 0)
    return {
        'months': months,
        'manpower_rows': rows,
        'heads_total': heads_total,
        'salaries_total': salaries_total,
        'statutory': statutory,
        'total_a': total_a,
        'sections': sections,
        'totals': totals,
        'grand_total': grand_total,
        'monthly_cost': grand_total / months,
        # Per calendar day of the contract - the workbook's / 365, kept
        # right for contracts other than 12 months.
        'daily_cost': grand_total / (months * DAYS_PER_YEAR / TWELVE),
        'margin_pct': margin * HUNDRED,
        'margin_set': bool(header.profit_margin_pct),
        'contract_value': contract_value,
        'profit': contract_value - grand_total,
        'vat_pct': vat,
        'contract_value_incl_vat': contract_value * (1 + vat / HUNDRED),
    }
