from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from projects.models import Project
from costing.models import CostingSheet
from .models import ProjectFinance, PaymentMilestone, CashOutflowRow


def _can_finance(user):
    """Finance workflow access — finance team and super admin only."""
    return bool(getattr(user, 'is_authenticated', False) and (
        user.is_super_admin_user or getattr(user, 'is_finance_team_user', False)))


def _parse_decimal(raw):
    raw = (raw or '').strip()
    if raw == '':
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parse_int(raw):
    raw = (raw or '').strip()
    if raw == '':
        return None
    try:
        return int(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(raw):
    raw = (raw or '').strip()
    if raw == '':
        return None
    from datetime import datetime
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


@login_required
def finance_home(request):
    """Finance landing — Won projects and their payment-schedule status."""
    if not _can_finance(request.user):
        messages.error(request, 'Finance access is limited to the finance team.')
        return redirect('dashboard:index')

    projects = (Project.objects.filter(status__category='won')
                .select_related('status', 'region')
                .order_by('-id'))
    if not request.user.is_super_admin_user:
        projects = projects.filter(region=request.user.region)

    # Region filter. Super admin can pick any region; others are already scoped
    # to their own region (the dropdown then just shows that one).
    from projects.models import Region
    if request.user.is_super_admin_user:
        regions = Region.objects.order_by('name')
    else:
        regions = Region.objects.filter(pk=request.user.region_id)

    selected_region = (request.GET.get('region') or '').strip()
    if selected_region.isdigit():
        projects = projects.filter(region_id=int(selected_region))

    finances = {f.project_id: f for f in ProjectFinance.objects.filter(
        project__in=projects)}
    rows = [{'project': p, 'finance': finances.get(p.id)} for p in projects]
    return render(request, 'finance/home.html', {
        'rows': rows, 'regions': regions, 'selected_region': selected_region,
    })


@login_required
def project_schedule(request, project_pk):
    """Per-project payment / cash-flow schedule (Step 2)."""
    project = get_object_or_404(Project, pk=project_pk)
    if not _can_finance(request.user):
        messages.error(request, 'Finance access is limited to the finance team.')
        return redirect('dashboard:index')

    pf, _created = ProjectFinance.objects.get_or_create(
        project=project, defaults={'created_by': request.user})
    pf.seed_default_milestones()
    # The P.O Value is fetched from the final approved margin (M5). When that's
    # available it is the source of truth and the field is locked.
    po_source_sheet = pf.sync_po_value_from_final_margin()
    po_value_locked = po_source_sheet is not None

    if request.method == 'POST':
        # Delete is a button inside the main form, carrying the row id.
        if request.POST.get('delete_id'):
            pf.milestones.filter(pk=_parse_int(request.POST['delete_id'])).delete()
            messages.success(request, 'Row deleted.')
            return redirect('finance:schedule', project_pk=project.pk)

        action = request.POST.get('action', 'save')
        if action in ('add_milestone', 'add_subrow'):
            is_sub = action == 'add_subrow'
            PaymentMilestone.objects.create(
                project_finance=pf,
                name='Progressive Invoice (Weightage Wise)' if is_sub else 'New milestone',
                is_subrow=is_sub, order=pf.milestones.count())
            messages.success(request, 'Row added.')
            return redirect('finance:schedule', project_pk=project.pk)

        # Save the header + all milestone rows. When the P.O Value is locked to
        # the final approved margin, ignore any posted value (keep the synced one).
        if not po_value_locked:
            pf.po_value = _parse_decimal(request.POST.get('po_value')) or Decimal('0')
        pf.kickoff_date = _parse_date(request.POST.get('kickoff_date'))
        pf.estimated_start_date = _parse_date(request.POST.get('estimated_start_date'))
        pf.estimated_end_date = _parse_date(request.POST.get('estimated_end_date'))
        pf.cert_approval_gap = _parse_int(request.POST.get('cert_approval_gap')) or 0
        pf.invoice_gap = _parse_int(request.POST.get('invoice_gap')) or 0
        pf.payment_gap = _parse_int(request.POST.get('payment_gap')) or 0
        pf.notes = (request.POST.get('notes') or '').strip()
        pf.save()

        for m in pf.milestones.all():
            p = f'm-{m.pk}-'
            m.name = (request.POST.get(p + 'name') or m.name).strip()
            m.invoice_no = (request.POST.get(p + 'invoice_no') or '').strip()
            m.remarks = (request.POST.get(p + 'remarks') or '').strip()
            m.from_kickoff_days = _parse_int(request.POST.get(p + 'from_kickoff_days'))
            m.best_pct = _parse_decimal(request.POST.get(p + 'best_pct'))
            m.average_pct = _parse_decimal(request.POST.get(p + 'average_pct'))
            m.proposed_pct = _parse_decimal(request.POST.get(p + 'proposed_pct'))
            m.submitted_pct = _parse_decimal(request.POST.get(p + 'submitted_pct'))
            m.work_cert_prep_date = _parse_date(request.POST.get(p + 'work_cert_prep_date'))
            m.work_cert_approval_date = _parse_date(request.POST.get(p + 'work_cert_approval_date'))
            m.invoice_submission_date = _parse_date(request.POST.get(p + 'invoice_submission_date'))
            m.payment_receive_date = _parse_date(request.POST.get(p + 'payment_receive_date'))
            m.save()

        if action == 'recompute':
            pf.recompute_dates()
            messages.success(request, 'Dates recalculated from kickoff + offsets.')
        else:
            messages.success(request, 'Schedule saved.')
        return redirect('finance:schedule', project_pk=project.pk)

    # Build display rows with running S.No (sub-rows are unnumbered).
    milestones = list(pf.milestones.all())
    serial = 0
    display = []
    for m in milestones:
        if not m.is_subrow:
            serial += 1
        display.append({'m': m, 'serial': (None if m.is_subrow else serial)})

    # Per-scenario column totals for the footer.
    def _col_total(field):
        return sum((getattr(m, field) or Decimal('0')) for m in milestones)

    totals = {
        'best': _col_total('best_pct'),
        'average': _col_total('average_pct'),
        'proposed': _col_total('proposed_pct'),
        'submitted': _col_total('submitted_pct'),
        'amount': sum((m.amount for m in milestones), Decimal('0')),
    }
    return render(request, 'finance/schedule.html', {
        'project': project, 'pf': pf, 'display': display, 'totals': totals,
        'po_value_locked': po_value_locked, 'po_source_sheet': po_source_sheet,
    })


def _final_costing_sheet(project):
    """The costing sheet the outflow is generated from — the final (M5) sheet
    if one exists, else the project's most recently updated sheet."""
    sheet = (CostingSheet.objects
             .filter(project=project, margin_final__isnull=False)
             .order_by('-updated_at').first())
    if sheet is None:
        sheet = (CostingSheet.objects.filter(project=project)
                 .order_by('-updated_at').first())
    return sheet


def _generate_outflow_rows(project, sheet):
    """Create outflow rows from the sheet's A.1 line items and A.2 SOW items,
    skipping any costing line already listed (matched by source_ref). Amounts
    are the SAR cost we pay out; VAT defaults to 15%. Returns rows added."""
    existing = set(CashOutflowRow.objects.filter(project=project)
                   .exclude(source_ref='').values_list('source_ref', flat=True))
    vat_rate = CashOutflowRow.VAT_RATE
    added = 0

    order = CashOutflowRow.objects.filter(project=project, part='A1').count()
    for section in sheet.sections.filter(is_optional=False).order_by('order'):
        for item in section.line_items.all().order_by('order', 'item_number'):
            ref = f'line:{item.pk}'
            if ref in existing:
                continue
            amount = (item.unit_cost_sar * item.quantity).quantize(Decimal('0.01'))
            vat = (amount * vat_rate).quantize(Decimal('0.01'))
            desc = f'{item.item_number} — {item.description}'.strip(' —')
            CashOutflowRow.objects.create(
                project=project, part='A1', order=order, description=desc,
                amount=amount, vat=vat, total_amount=amount + vat, source_ref=ref)
            order += 1
            added += 1

    order = CashOutflowRow.objects.filter(project=project, part='A2').count()
    for sow in sheet.scope_of_work_items.all().order_by('order', 'serial_number'):
        ref = f'sow:{sow.pk}'
        if ref in existing:
            continue
        amount = (sow.total_price or Decimal('0')).quantize(Decimal('0.01'))
        vat = (amount * vat_rate).quantize(Decimal('0.01'))
        CashOutflowRow.objects.create(
            project=project, part='A2', order=order, description=sow.description,
            amount=amount, vat=vat, total_amount=amount + vat, source_ref=ref)
        order += 1
        added += 1

    return added


@login_required
def project_cash_outflow(request, project_pk):
    """Per-project cash-OUTFLOW schedule — vendor payments split into
    A.1 Scope of Supply and A.2 Scope of Services."""
    project = get_object_or_404(Project, pk=project_pk)
    if not _can_finance(request.user):
        messages.error(request, 'Finance access is limited to the finance team.')
        return redirect('dashboard:index')

    sheet = _final_costing_sheet(project)

    if request.method == 'POST':
        if request.POST.get('delete_id'):
            CashOutflowRow.objects.filter(
                project=project, pk=_parse_int(request.POST['delete_id'])).delete()
            messages.success(request, 'Row deleted.')
            return redirect('finance:cash_outflow', project_pk=project.pk)

        action = request.POST.get('action', 'save')
        if action == 'generate':
            if sheet is None:
                messages.error(request, 'No costing sheet found for this project to generate from.')
            else:
                n = _generate_outflow_rows(project, sheet)
                messages.success(
                    request,
                    f'{n} row(s) generated from the costing sheet.' if n
                    else 'No new rows — every costing line is already listed.')
            return redirect('finance:cash_outflow', project_pk=project.pk)

        if action in ('add_a1', 'add_a2'):
            part = 'A2' if action == 'add_a2' else 'A1'
            CashOutflowRow.objects.create(
                project=project, part=part,
                order=CashOutflowRow.objects.filter(project=project, part=part).count())
            messages.success(request, 'Row added.')
            return redirect('finance:cash_outflow', project_pk=project.pk)

        # Save every row.
        for r in CashOutflowRow.objects.filter(project=project):
            p = f'r-{r.pk}-'
            r.description = (request.POST.get(p + 'description') or '').strip()
            r.po_number = (request.POST.get(p + 'po_number') or '').strip()
            for i in range(1, 7):
                setattr(r, f'date_{i}', _parse_date(request.POST.get(f'{p}date_{i}')))
                setattr(r, f'pct_{i}', _parse_decimal(request.POST.get(f'{p}pct_{i}')))
            r.amount = _parse_decimal(request.POST.get(p + 'amount')) or Decimal('0')
            r.vat = _parse_decimal(request.POST.get(p + 'vat')) or Decimal('0')
            r.total_amount = _parse_decimal(request.POST.get(p + 'total_amount')) or Decimal('0')
            r.remarks = (request.POST.get(p + 'remarks') or '').strip()
            r.save()
        messages.success(request, 'Cash outflow saved.')
        return redirect('finance:cash_outflow', project_pk=project.pk)

    def _display(rows):
        out = []
        for idx, r in enumerate(rows, start=1):
            out.append({
                'r': r, 'serial': idx,
                'dates': [getattr(r, f'date_{i}') for i in range(1, 7)],
                'pcts': [getattr(r, f'pct_{i}') for i in range(1, 7)],
            })
        return out

    all_rows = list(CashOutflowRow.objects.filter(project=project))
    a1 = [r for r in all_rows if r.part == 'A1']
    a2 = [r for r in all_rows if r.part == 'A2']

    def _tot(rows, f):
        return sum((getattr(x, f) or Decimal('0')) for x in rows)

    totals = {
        'a1': {'amount': _tot(a1, 'amount'), 'vat': _tot(a1, 'vat'), 'total': _tot(a1, 'total_amount')},
        'a2': {'amount': _tot(a2, 'amount'), 'vat': _tot(a2, 'vat'), 'total': _tot(a2, 'total_amount')},
    }
    totals['all'] = {k: totals['a1'][k] + totals['a2'][k] for k in ('amount', 'vat', 'total')}

    po_numbers = list(project.purchase_orders.exclude(po_number='')
                      .order_by('po_number').values_list('po_number', flat=True))

    return render(request, 'finance/cash_outflow.html', {
        'project': project, 'sheet': sheet,
        'a1_display': _display(a1), 'a2_display': _display(a2),
        'totals': totals, 'po_numbers': po_numbers,
    })


@login_required
@require_POST
def approve_margin(request, sheet_pk, key):
    """Step 1 → Step 2 hook: approve a margin scenario on a costing sheet and
    carry its price into the project's finance P.O Value."""
    if not _can_finance(request.user):
        messages.error(request, 'Only finance can approve a margin.')
        return redirect('costing:detail', pk=sheet_pk)

    sheet = get_object_or_404(CostingSheet, pk=sheet_pk)
    if not sheet.project:
        messages.error(request, 'This costing sheet has no linked project to bill against.')
        return redirect('costing:margin_analysis', pk=sheet_pk)

    scenario = next((s for s in sheet.margin_scenarios() if s['key'] == key), None)
    if not scenario or not scenario['configured']:
        messages.error(request, 'That margin scenario is not set.')
        return redirect('costing:margin_analysis', pk=sheet_pk)

    # Grand price = supply (with margin) + A.2 services — the full contract value.
    po_value = scenario.get('grand_price') or scenario['total_price']
    pf, _ = ProjectFinance.objects.get_or_create(
        project=sheet.project, defaults={'created_by': request.user})
    pf.po_value = po_value
    pf.approved_margin = key
    pf.source_sheet = sheet
    pf.save(update_fields=['po_value', 'approved_margin', 'source_sheet', 'updated_at'])

    messages.success(
        request,
        f'{scenario["label"]} approved — P.O Value {po_value:,.2f} '
        f'set for {sheet.project.project_name}.')
    return redirect('finance:schedule', project_pk=sheet.project_id)
