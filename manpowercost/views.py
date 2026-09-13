"""Manpower costing views.

Three gates, deliberately separate:

* `manpowercost.access` opens the pages.
* `_user_can_see_pricing` (shared with costing) decides whether the money is
  in the response at all - not hidden with CSS, absent.
* `manpowercost.margin` decides whether the markup is shown. A planner can
  legitimately need the cost sheet without seeing what we add to it.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import require_capability
from costing.models import ResourceCatalogueItem
from costing.views import _user_can_see_pricing

from . import rates
from .models import (
    COST_COMPONENTS, COST_COMPONENT_FIELDS, ChargeRate, CostBasis,
    ManpowerCostLine, ManpowerCostSheet,
)

PREVIEW_SALT = 'manpowercost.import'
PREVIEW_MAX_AGE = 60 * 30  # 30 minutes, same as the PMO milestone import


def _require_pricing(user):
    """Money is not merely hidden from these roles - the view refuses.

    Rendering the page and stripping the figures in the template leaves them
    in the context and one template edit away from leaking, which is why
    costing made this a server-side predicate.
    """
    if not _user_can_see_pricing(user):
        raise PermissionDenied


def _can_edit(user):
    return user.has_capability('manpowercost.edit')


def _can_see_margin(user):
    return user.has_capability('manpowercost.margin')


@login_required
@require_capability('manpowercost.access')
def sheet_list(request):
    _require_pricing(request.user)
    sheets = (ManpowerCostSheet.objects
              .select_related('basis')
              .prefetch_related('lines'))
    rows = [{'sheet': s, 'summary': rates.sheet_summary(s)} for s in sheets]
    return render(request, 'manpowercost/sheet_list.html', {
        'rows': rows,
        'can_edit': _can_edit(request.user),
    })


@login_required
@require_capability('manpowercost.access')
def sheet_detail(request, pk):
    _require_pricing(request.user)
    sheet = get_object_or_404(
        ManpowerCostSheet.objects.select_related('basis'), pk=pk)
    basis = sheet.effective_basis
    lines = (sheet.lines.select_related('employee', 'position')
             .order_by('order', 'pk'))

    rows = []
    for line in lines:
        monthly = line.monthly_cost
        rows.append({
            'line': line,
            'monthly': monthly,
            'yearly': monthly * 12,
            'daily': line.daily_cost(basis),
            'hourly': line.hourly_cost(basis),
            'incomplete': not line.gross_salary,
        })

    return render(request, 'manpowercost/sheet_detail.html', {
        'sheet': sheet,
        'basis': basis,
        'rows': rows,
        'summary': rates.sheet_summary(sheet),
        'components': COST_COMPONENTS,
        'can_edit': _can_edit(request.user),
    })


@login_required
@require_capability('manpowercost.access')
def rate_card(request):
    """Charge-out rates with the whole build-up on show.

    Every intermediate step is rendered because the sheet this replaces
    presented a single hourly number whose three inputs were each labelled
    differently from what they computed.
    """
    _require_pricing(request.user)
    show_margin = _can_see_margin(request.user)

    charge_rates = (ChargeRate.objects
                    .select_related('position', 'basis')
                    .order_by('position__order', 'position__name',
                              'classification'))
    rows = []
    for cr in charge_rates:
        build_up = rates.derive_charge_rate(cr.monthly_cost, cr.basis)
        effective = rates.effective_hourly_rate(cr)
        hourly_cost = rates.hourly_cost_from_monthly(cr.monthly_cost, cr.basis)
        rows.append({
            'rate': cr,
            'build_up': build_up,
            'effective': effective,
            'hourly_cost': hourly_cost,
            'margin': (rates.margin_pct(effective, hourly_cost)
                       if show_margin else None),
        })

    return render(request, 'manpowercost/rate_card.html', {
        'rows': rows,
        'show_margin': show_margin,
        'can_edit': _can_edit(request.user),
        'bases': CostBasis.objects.all(),
    })


@login_required
@require_capability('manpowercost.edit')
@require_POST
def rate_override(request, pk):
    """Set or clear the manual rate on one charge rate."""
    _require_pricing(request.user)
    cr = get_object_or_404(ChargeRate, pk=pk)
    raw = (request.POST.get('manual_rate') or '').strip()
    if raw == '':
        cr.manual_rate = None
        messages.success(request, f'{cr} now follows cost.')
    else:
        try:
            cr.manual_rate = Decimal(raw)
        except (InvalidOperation, ValueError):
            messages.error(request, 'Enter a number, or leave blank to follow cost.')
            return redirect('manpowercost:rate_card')
        messages.success(request, f'{cr} fixed at {cr.manual_rate}/hr.')
    cr.save(update_fields=['manual_rate', 'updated_at'])
    return redirect('manpowercost:rate_card')


# ── Import ───────────────────────────────────────────────────────────────
# Column aliases carried over from the app this replaces, so a workbook that
# imported before still imports. The behaviour that changes is what happens
# to a column that matches nothing: it is reported, not silently zeroed.

COLUMN_ALIASES = {
    'employee_name': ('employees name', 'employee name', 'name', 'full name'),
    'department': ('department', 'dept'),
    'classification': ('classification of staff', 'classification', 'class'),
    'location_project': ('location/project', 'location', 'project', 'site'),
    'designation': ('designation', 'position', 'title'),
    'gross_salary': ('gross salary', 'salary', 'basic salary'),
    'iqama_cost': ('iqama cost', 'iqama'),
    'service_transfer_visa_fee': ('services transfer & visa fee',
                                  'service transfer & visa fee',
                                  'services transfer', 'visa fee'),
    'gosi_cost': ('gosi cost', 'gosi'),
    'vacation_pay': ('vacation pay', 'vacation'),
    'exe_cost': ('exe cost', 'exe'),
    'eosb': ('eosb', 'end of service', 'end of service benefit'),
    'air_ticket': ('air ticket', 'airticket', 'ticket'),
    'insurance_cost': ('insurance cost', 'insurance'),
    'project_allowance': ('project allowance', 'allowance'),
    'ppe': ('ppe',),
    'engineering_council_cost': ('engineering council cost',
                                 'engineering council'),
    'other_expenditures': ('other expenditures', 'other expenses', 'others'),
}

HEADER_HINTS = {
    'employees name', 'employee name', 'name', 'sr. no.', 'sr.no.', 'sr. no',
    's.no', 's.no.', 'gross salary', 'designation', 'department',
}


def _parse_decimal(value):
    if value in (None, ''):
        return Decimal('0')
    try:
        return Decimal(str(value).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _read_workbook(upload):
    """Parse an uploaded workbook into rows, and report what did not map.

    Returns (rows, unmapped_headers, header_row_index).
    """
    import openpyxl

    wb = openpyxl.load_workbook(upload, data_only=True)
    ws = wb.active

    header_row_idx = 1
    for idx, row in enumerate(ws.iter_rows(min_row=1, max_row=5,
                                           values_only=True), start=1):
        cells = {str(c).strip().lower() for c in row if c is not None}
        if cells & HEADER_HINTS:
            header_row_idx = idx
            break

    header_row = next(ws.iter_rows(min_row=header_row_idx,
                                   max_row=header_row_idx, values_only=True))
    header_map = {}
    for col_idx, cell in enumerate(header_row):
        if cell is None:
            continue
        header_map[str(cell).strip().lower()] = col_idx

    known = {alias for aliases in COLUMN_ALIASES.values() for alias in aliases}
    ignorable = {'sr. no.', 'sr.no.', 'sr. no', 's.no', 's.no.', 'costing',
                 'total yearly cost', 'monthly cost', 'daily cost',
                 'hourly cost', 'doj', 'date of joining', 'date of birth',
                 'dob', 'emloyee age', 'employee age',
                 'employees demobilization date', 'demobilization date'}
    unmapped = sorted(h for h in header_map
                      if h and h not in known and h not in ignorable)

    def pick(row, field):
        for alias in COLUMN_ALIASES[field]:
            if alias in header_map:
                idx = header_map[alias]
                if idx < len(row):
                    return row[idx]
        return None

    rows = []
    for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
        name = pick(row, 'employee_name')
        designation = pick(row, 'designation')
        if not name and not designation:
            continue
        entry = {
            'employee_name': str(name).strip() if name else '',
            'department': str(pick(row, 'department') or '').strip(),
            'classification': str(pick(row, 'classification') or '').strip(),
            'location_project': str(pick(row, 'location_project') or '').strip(),
            'designation': str(designation or '').strip(),
        }
        for field in COST_COMPONENT_FIELDS:
            entry[field] = str(_parse_decimal(pick(row, field)))
        rows.append(entry)

    return rows, unmapped, ws.title


@login_required
@require_capability('manpowercost.edit')
def sheet_import(request):
    """Step 1: parse and show what would be created. Nothing is written."""
    _require_pricing(request.user)
    if request.method != 'POST':
        return render(request, 'manpowercost/import.html', {
            'bases': CostBasis.objects.all(),
        })

    upload = request.FILES.get('workbook')
    if not upload:
        messages.error(request, 'Choose a workbook to import.')
        return redirect('manpowercost:sheet_import')

    try:
        rows, unmapped, sheet_name = _read_workbook(upload)
    except Exception as exc:  # openpyxl raises a wide range on bad files
        messages.error(request, f'Could not read that workbook: {exc}')
        return redirect('manpowercost:sheet_import')

    if not rows:
        messages.warning(request, 'No rows with a name or designation were found.')
        return redirect('manpowercost:sheet_import')

    # The whole point of the preview: a row whose salary did not map is
    # called out before anything is saved, instead of being stored as zero
    # and quietly shrinking every total built on it.
    zero_salary = [r for r in rows if Decimal(r['gross_salary']) == 0]

    basis_id = request.POST.get('basis') or None
    payload = {
        'title': request.POST.get('title') or sheet_name or 'Imported sheet',
        'project_reference': request.POST.get('project_reference', ''),
        'basis_id': int(basis_id) if basis_id else None,
        'rows': rows,
    }
    return render(request, 'manpowercost/import_preview.html', {
        'rows': rows,
        'unmapped': unmapped,
        'zero_salary': zero_salary,
        'title': payload['title'],
        'basis_id': payload['basis_id'],
        'payload': signing.dumps(payload, salt=PREVIEW_SALT, compress=True),
    })


@login_required
@require_capability('manpowercost.edit')
@require_POST
def sheet_import_apply(request):
    """Step 2: write the previewed rows, all or nothing."""
    _require_pricing(request.user)
    raw = request.POST.get('payload', '')
    try:
        payload = signing.loads(raw, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except signing.SignatureExpired:
        messages.error(request, 'That preview expired. Upload the file again.')
        return redirect('manpowercost:sheet_import')
    except signing.BadSignature:
        messages.error(request, 'That preview could not be verified. Upload the file again.')
        return redirect('manpowercost:sheet_import')

    basis = None
    if payload.get('basis_id'):
        basis = CostBasis.objects.filter(pk=payload['basis_id']).first()

    # Atomic because the app this replaces created the sheet before parsing
    # and had no transaction: a row that blew up mid-loop left a half-imported
    # sheet behind that looked complete.
    with transaction.atomic():
        sheet = ManpowerCostSheet.objects.create(
            title=payload['title'],
            project_reference=payload.get('project_reference', ''),
            date=timezone.localdate(),
            basis=basis,
            created_by=request.user,
            notes='Imported from a workbook. Cost figures are monthly.',
        )
        for order, row in enumerate(payload['rows']):
            ManpowerCostLine.objects.create(
                sheet=sheet, order=order,
                employee_name=row.get('employee_name', ''),
                department=row.get('department', ''),
                location_project=row.get('location_project', ''),
                designation=row.get('designation', ''),
                **{f: Decimal(row.get(f, '0')) for f in COST_COMPONENT_FIELDS},
            )

    messages.success(request, f'Imported {len(payload["rows"])} lines into "{sheet.title}".')
    return redirect('manpowercost:sheet_detail', pk=sheet.pk)
