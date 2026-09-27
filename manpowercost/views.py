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
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import require_capability
from costing.models import ResourceCatalogueItem
from costing.views import _user_can_see_pricing

from . import rates
from .models import (
    COMPONENT_LABELS, COST_COMPONENTS, COST_COMPONENT_FIELDS, COST_GROUPS,
    ChargeRate, CostBasis, Classification, ManpowerCostLine,
    ManpowerCostSheet,
)

# Text cells a grid row may set, alongside the numeric cost components.
TEXT_FIELDS = ('employee_name', 'designation', 'department',
               'location_project', 'classification')

SALARY_SPLIT_FIELDS = ('basic_salary', 'housing_allowance',
                       'transport_allowance')


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


def _require_edit(user):
    if not _can_edit(user):
        raise PermissionDenied


def _pk(raw):
    """A primary key from a form field, or None.

    `filter(pk=...)` raises ValueError on anything non-numeric rather than
    returning nothing, so a hand-edited select would 500 instead of being
    refused. None here means "not found", which is what the callers already
    handle.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _decimal_or_error(raw):
    """Parse a grid cell. Blank means zero; nonsense is refused.

    Deliberately not a silent fallback to 0. The importer this module
    replaced turned anything it could not parse into a zero, which is how a
    salary quietly left a total.
    """
    raw = (raw or '').strip().replace(',', '')
    if raw == '':
        return Decimal('0'), None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None, f'"{raw}" is not a number'
    if value < 0:
        return None, 'Cost cannot be negative'
    return value, None


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
@require_capability('manpowercost.edit')
@require_POST
def sheet_create(request):
    _require_pricing(request.user)
    title = (request.POST.get('title') or '').strip()
    if not title:
        messages.error(request, 'Give the sheet a title.')
        return redirect('manpowercost:sheet_list')
    sheet = ManpowerCostSheet.objects.create(
        title=title[:255],
        project_reference=(request.POST.get('project_reference') or '')[:255],
        date=timezone.localdate(),
        basis=CostBasis.get_default(),
        created_by=request.user,
    )
    return redirect('manpowercost:sheet_detail', pk=sheet.pk)


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
            # Built here rather than reached for in the template: a template
            # cannot look a field up by name, and the groups have to stay in
            # the order COST_GROUPS declares.
            'groups': [
                {
                    'name': group_name,
                    'fields': [(f, COMPONENT_LABELS[f], getattr(line, f))
                               for f in fields],
                    'subtotal': sum((getattr(line, f) or Decimal('0')
                                     for f in fields), Decimal('0')),
                }
                for group_name, fields in COST_GROUPS
            ],
            'split': [
                ('basic_salary', 'Basic', line.basic_salary),
                ('housing_allowance', 'Housing', line.housing_allowance),
                ('transport_allowance', 'Transport', line.transport_allowance),
            ],
            'monthly': monthly,
            'yearly': monthly * 12,
            'daily': line.daily_cost(basis),
            'hourly': line.hourly_cost(basis),
            'incomplete': not line.gross_salary,
            'mismatch': line.salary_breakdown_mismatch,
        })

    return render(request, 'manpowercost/sheet_detail.html', {
        'sheet': sheet,
        'basis': basis,
        'rows': rows,
        'summary': rates.sheet_summary(sheet),
        'components': COST_COMPONENTS,
        'classifications': Classification.choices,
        'can_edit': _can_edit(request.user),
    })


# ── The editable grid ─────────────────────────────────────────────────────
# Same shape as costing's A.4 resource grid: one row per person, saved on
# blur, so twenty people can be typed in one sitting without a page reload.

def _line_payload(line, basis):
    monthly = line.monthly_cost
    return {
        'id': line.pk,
        'monthly': f'{monthly:.2f}',
        'yearly': f'{monthly * 12:.2f}',
        'daily': f'{line.daily_cost(basis):.2f}',
        'hourly': f'{line.hourly_cost(basis):.2f}',
        'incomplete': not line.gross_salary,
    }


def _sheet_payload(sheet):
    summary = rates.sheet_summary(sheet)
    return {
        'monthly_total': f'{summary["monthly_total"]:.2f}',
        'yearly_total': f'{summary["yearly_total"]:.2f}',
        'hourly_total': f'{summary["hourly_total"]:.2f}',
        'line_count': summary['line_count'],
        'incomplete_count': summary['incomplete_count'],
    }


@login_required
@require_POST
def line_add(request, pk):
    _require_pricing(request.user)
    _require_edit(request.user)
    sheet = get_object_or_404(ManpowerCostSheet, pk=pk)
    last = (sheet.lines.order_by('-order')
            .values_list('order', flat=True).first() or 0)
    line = ManpowerCostLine.objects.create(sheet=sheet, order=last + 1)
    return JsonResponse({
        'line': _line_payload(line, sheet.effective_basis),
        'sheet': _sheet_payload(sheet),
    })


@login_required
@require_POST
def line_update(request, pk):
    """Update one field on one line.

    One field per request rather than the whole row: the grid saves on blur,
    and sending the untouched cells back would let a stale tab overwrite what
    somebody else just typed into a different column.
    """
    _require_pricing(request.user)
    _require_edit(request.user)
    line = get_object_or_404(
        ManpowerCostLine.objects.select_related('sheet__basis'), pk=pk)

    field = request.POST.get('field', '')
    raw = request.POST.get('value', '')

    if field in COST_COMPONENT_FIELDS or field in SALARY_SPLIT_FIELDS:
        value, error = _decimal_or_error(raw)
        if error:
            return JsonResponse({'error': error}, status=400)
        setattr(line, field, value)
    elif field in TEXT_FIELDS:
        if field == 'classification':
            valid = {c for c, _ in Classification.choices}
            if raw and raw not in valid:
                return JsonResponse({'error': 'Unknown classification'},
                                    status=400)
        setattr(line, field, (raw or '').strip()[:255])
    else:
        # An unknown field name is refused rather than ignored: silently
        # accepting a POST that changed nothing is indistinguishable from a
        # save that worked.
        return JsonResponse({'error': f'Cannot edit "{field}"'}, status=400)

    line.save(update_fields=[field])
    return JsonResponse({
        'line': _line_payload(line, line.sheet.effective_basis),
        'sheet': _sheet_payload(line.sheet),
    })


@login_required
@require_POST
def line_delete(request, pk):
    _require_pricing(request.user)
    _require_edit(request.user)
    line = get_object_or_404(ManpowerCostLine.objects.select_related('sheet'),
                             pk=pk)
    sheet = line.sheet
    line.delete()
    return JsonResponse({'sheet': _sheet_payload(sheet)})


# ── Charge rates ──────────────────────────────────────────────────────────

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
        'bases': CostBasis.objects.annotate(rate_count=Count('charge_rates')),
        'positions': ResourceCatalogueItem.objects.filter(is_active=True),
        'classifications': Classification.choices,
    })


@login_required
@require_capability('manpowercost.edit')
@require_POST
def rate_create(request):
    """Add a charge rate for a role.

    These existed only in the Django admin until now, so A.4's Std rate
    column read as a dash until somebody with admin access went and made
    one - and the people who price work are not the people with admin
    access.
    """
    _require_pricing(request.user)
    position = ResourceCatalogueItem.objects.filter(
        pk=_pk(request.POST.get('position'))).first()
    basis = CostBasis.objects.filter(pk=_pk(request.POST.get('basis'))).first()
    classification = (request.POST.get('classification') or '').strip()

    if position is None or basis is None:
        messages.error(request, 'Pick a role and a cost basis.')
        return redirect('manpowercost:rate_card')
    if classification and classification not in dict(Classification.choices):
        messages.error(request, 'Unknown classification.')
        return redirect('manpowercost:rate_card')

    monthly, error = _decimal_or_error(request.POST.get('monthly_cost'))
    if error:
        messages.error(request, error)
        return redirect('manpowercost:rate_card')

    # One rate per (role, classification, basis) - the same uniqueness the
    # model enforces, caught here so it reads as a sentence rather than as
    # an IntegrityError page.
    if ChargeRate.objects.filter(position=position, basis=basis,
                                 classification=classification).exists():
        messages.error(
            request,
            f'{position.name} already has a rate on {basis.name} for that '
            f'classification. Edit that one instead.')
        return redirect('manpowercost:rate_card')

    ChargeRate.objects.create(
        position=position, basis=basis, classification=classification,
        monthly_cost=monthly)
    messages.success(request, f'Added a charge rate for {position.name}.')
    return redirect('manpowercost:rate_card')


@login_required
@require_capability('manpowercost.edit')
@require_POST
def rate_update(request, pk):
    """Edit one charge rate: its cost, its basis, or its manual override.

    The monthly cost is editable because a rate derived from a stale cost is
    worse than one typed in - it still looks derived.
    """
    _require_pricing(request.user)
    cr = get_object_or_404(ChargeRate, pk=pk)

    if 'monthly_cost' in request.POST:
        monthly, error = _decimal_or_error(request.POST.get('monthly_cost'))
        if error:
            messages.error(request, error)
            return redirect('manpowercost:rate_card')
        cr.monthly_cost = monthly

    if 'basis' in request.POST:
        basis = CostBasis.objects.filter(pk=_pk(request.POST.get('basis'))).first()
        if basis is None:
            messages.error(request, 'Unknown cost basis.')
            return redirect('manpowercost:rate_card')
        cr.basis = basis

    if 'manual_rate' in request.POST:
        raw = (request.POST.get('manual_rate') or '').strip().replace(',', '')
        if raw == '':
            cr.manual_rate = None
        else:
            try:
                cr.manual_rate = Decimal(raw)
            except (InvalidOperation, ValueError):
                messages.error(request, 'Enter a number, or leave blank to follow cost.')
                return redirect('manpowercost:rate_card')
            if cr.manual_rate < 0:
                messages.error(request, 'A rate cannot be negative.')
                return redirect('manpowercost:rate_card')

    try:
        # The unique key spans (position, classification, basis), so a basis
        # change can collide with an existing row. It is a Meta constraint,
        # which validate_unique() does NOT cover - that sees only unique=True
        # fields and unique_together, and would let this through to an
        # IntegrityError 500.
        cr.validate_constraints()
    except ValidationError:
        messages.error(
            request,
            f'{cr.position.name} already has a rate on that basis for this '
            f'classification.')
        return redirect('manpowercost:rate_card')
    cr.save()
    messages.success(request, f'{cr} updated.')
    return redirect('manpowercost:rate_card')


@login_required
@require_capability('manpowercost.edit')
@require_POST
def rate_delete(request, pk):
    _require_pricing(request.user)
    cr = get_object_or_404(ChargeRate, pk=pk)
    label = str(cr)
    cr.delete()
    # A.4 goes back to a dash for that role, which is the honest answer once
    # no rate exists - better than leaving the last known one in place.
    messages.success(request, f'Removed the charge rate for {label}.')
    return redirect('manpowercost:rate_card')


@login_required
@require_capability('manpowercost.edit')
@require_POST
def basis_update(request, pk):
    """Edit a cost basis: overhead, profit, billable months, hours, days.

    Every rate built on it moves, which is the point - the basis is the one
    place those numbers are meant to live. Nothing derived is stored, so
    there is no recalculation step and no rate can be left stale.
    """
    _require_pricing(request.user)
    basis = get_object_or_404(CostBasis, pk=pk)

    for field in ('overhead_pct', 'profit_pct', 'billable_months',
                  'hours_per_month', 'working_days_per_month'):
        if field not in request.POST:
            continue
        value, error = _decimal_or_error(request.POST.get(field))
        if error:
            messages.error(request, f'{field.replace("_", " ")}: {error}')
            return redirect('manpowercost:rate_card')
        setattr(basis, field, value)

    if 'name' in request.POST:
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'A basis needs a name.')
            return redirect('manpowercost:rate_card')
        basis.name = name[:120]

    try:
        basis.full_clean()
    except ValidationError as exc:
        messages.error(request, '; '.join(
            m for msgs in exc.message_dict.values() for m in msgs))
        return redirect('manpowercost:rate_card')
    basis.save()
    affected = basis.charge_rates.count()
    messages.success(
        request,
        f'{basis.name} updated - {affected} charge rate'
        f'{"" if affected == 1 else "s"} rebuilt from it.')
    return redirect('manpowercost:rate_card')
