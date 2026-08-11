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

        periods = pf.timeline_periods()
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
            # Manual dates. Approval / submission / payment-receive are always
            # re-derived below, so posted values for them are ignored.
            m.work_cert_prep_date = _parse_date(request.POST.get(p + 'work_cert_prep_date'))
            m.actual_payment_receive_date = _parse_date(request.POST.get(p + 'actual_payment_receive_date'))
            m.apply_derived_dates()
            # Timeline month amounts — keep only non-empty cells.
            amounts = {}
            for pr in periods:
                raw = _parse_decimal(request.POST.get(f'{p}period-{pr["key"]}'))
                if raw is not None:
                    amounts[pr['key']] = str(raw)
            m.period_amounts = amounts
            m.save()

        if action == 'recompute':
            pf.recompute_dates()
            messages.success(request, 'Dates recalculated from the prep dates + gaps.')
        else:
            messages.success(request, 'Schedule saved.')
        return redirect('finance:schedule', project_pk=project.pk)

    # Timeline month columns (after the Amount column).
    periods = pf.timeline_periods()

    # Build display rows with running S.No (sub-rows are unnumbered).
    milestones = list(pf.milestones.all())
    serial = 0
    display = []
    for m in milestones:
        if not m.is_subrow:
            serial += 1
        display.append({
            'm': m, 'serial': (None if m.is_subrow else serial),
            'period_cells': [{'key': pr['key'], 'value': m.period_amount(pr['key'])}
                             for pr in periods],
        })

    # Per-scenario column totals for the footer.
    def _col_total(field):
        return sum((getattr(m, field) or Decimal('0')) for m in milestones)

    def _period_total(key):
        tot = Decimal('0')
        for m in milestones:
            raw = (m.period_amounts or {}).get(key)
            if raw not in (None, ''):
                try:
                    tot += Decimal(str(raw))
                except (InvalidOperation, ValueError):
                    pass
        return tot

    totals = {
        'best': _col_total('best_pct'),
        'average': _col_total('average_pct'),
        'proposed': _col_total('proposed_pct'),
        'submitted': _col_total('submitted_pct'),
        'amount': sum((m.amount for m in milestones), Decimal('0')),
        'periods': [{'key': pr['key'], 'total': _period_total(pr['key'])} for pr in periods],
    }
    return render(request, 'finance/schedule.html', {
        'project': project, 'pf': pf, 'display': display, 'totals': totals,
        'periods': periods,
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


def _by_system_breakdown(project):
    """Read-only 'By System' grouping for the cash-outflow page.

    Every line item on this project's purchase orders, grouped by its `system`
    (the free-text field on PurchaseOrderItem — Siren / PAGA / CCTV / …), then
    by PO, rolled up to a per-PO amount = sum of item values (qty × rate, SAR)
    plus 15% VAT. Mirrors the procurement Internal Summary's system grouping,
    but scoped to a single project and summed to PO totals for finance.

    All POs are included regardless of status. Returns (systems, grand) where
    `systems` is a list of {system, pos, po_count, amount, vat, total} and
    `grand` is the {amount, vat, total} across everything. Subtotals are sums
    of the rounded PO rows so the displayed figures always reconcile."""
    from procurement.models import PurchaseOrderItem
    vat_rate = CashOutflowRow.VAT_RATE
    items = (PurchaseOrderItem.objects
             .filter(purchase_order__project=project)
             .select_related('purchase_order')
             .order_by('system', 'purchase_order__po_number', 'serial_number'))

    # system_key -> {po_pk -> {po_number, vendor, item_count, amount}}
    grouped = {}
    for it in items:
        sys_key = (it.system or '').strip() or '— Unassigned —'
        po = it.purchase_order
        pos = grouped.setdefault(sys_key, {})
        row = pos.setdefault(po.pk, {
            'po_pk': po.pk, 'po_number': po.po_number, 'vendor': po.vendor_name,
            'payment_terms': po.payment_terms_text,
            'item_count': 0, 'amount': Decimal('0'),
        })
        row['item_count'] += 1
        row['amount'] += it.total_value

    systems = []
    grand = {'amount': Decimal('0'), 'vat': Decimal('0'), 'total': Decimal('0')}
    for sys_key, pos in grouped.items():
        po_rows = []
        s_amount = s_vat = s_total = Decimal('0')
        for row in pos.values():
            amount = row['amount'].quantize(Decimal('0.01'))
            vat = (amount * vat_rate).quantize(Decimal('0.01'))
            total = amount + vat
            po_rows.append({
                'po_pk': row['po_pk'],
                'po_number': row['po_number'], 'vendor': row['vendor'],
                'payment_terms': row['payment_terms'],
                'item_count': row['item_count'],
                'amount': amount, 'vat': vat, 'total': total,
            })
            s_amount += amount
            s_vat += vat
            s_total += total
        systems.append({
            'system': sys_key, 'pos': po_rows, 'po_count': len(po_rows),
            'amount': s_amount, 'vat': s_vat, 'total': s_total,
        })
        grand['amount'] += s_amount
        grand['vat'] += s_vat
        grand['total'] += s_total
    return systems, grand


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

    by_system, by_system_total = _by_system_breakdown(project)

    return render(request, 'finance/cash_outflow.html', {
        'project': project, 'sheet': sheet,
        'a1_display': _display(a1), 'a2_display': _display(a2),
        'totals': totals, 'po_numbers': po_numbers,
        'by_system': by_system, 'by_system_total': by_system_total,
    })


@login_required
def margin_analysis_list(request):
    """Finance landing for margin analysis — costed sheets in the finance user's
    region (finalized or in the finance stages), each linking to its M1–M5
    margin analysis where finance sets the scenarios and approves a margin."""
    if not _can_finance(request.user):
        messages.error(request, 'Finance access is limited to the finance team.')
        return redirect('dashboard:index')

    sheets = (CostingSheet.objects
              .filter(workflow_stage__in=['finalized', 'finance_review', 'finance_approved'])
              .select_related('project', 'project__region', 'project__status')
              .order_by('-updated_at'))
    if not request.user.is_super_admin_user:
        sheets = sheets.filter(project__region=request.user.region)

    from projects.models import Region
    if request.user.is_super_admin_user:
        regions = Region.objects.order_by('name')
    else:
        regions = Region.objects.filter(pk=request.user.region_id)
    selected_region = (request.GET.get('region') or '').strip()
    if selected_region.isdigit():
        sheets = sheets.filter(project__region_id=int(selected_region))

    return render(request, 'finance/margin_analysis_list.html', {
        'sheets': sheets, 'regions': regions, 'selected_region': selected_region,
    })


@login_required
def budgeting_list(request):
    """Finance budgeting landing — costing sheets ready for / under finance
    budgeting (finalized by sales, in review, or already approved)."""
    if not _can_finance(request.user):
        messages.error(request, 'Finance access is limited to the finance team.')
        return redirect('dashboard:index')

    # Only sheets that have been sent to finance for budgeting (in review) or
    # already approved — not ones sales merely finalized but hasn't handed over.
    sheets = (CostingSheet.objects
              .filter(workflow_stage__in=['finance_review', 'finance_approved'])
              .select_related('project', 'project__region', 'project__status')
              .order_by('-updated_at'))
    if not request.user.is_super_admin_user:
        sheets = sheets.filter(project__region=request.user.region)

    from projects.models import Region
    if request.user.is_super_admin_user:
        regions = Region.objects.order_by('name')
    else:
        regions = Region.objects.filter(pk=request.user.region_id)
    selected_region = (request.GET.get('region') or '').strip()
    if selected_region.isdigit():
        sheets = sheets.filter(project__region_id=int(selected_region))

    return render(request, 'finance/budgeting_list.html', {
        'sheets': sheets, 'regions': regions, 'selected_region': selected_region,
    })


def _can_edit_budget(user, sheet):
    """Finance may edit the budget until the sheet is finance-approved; after
    that only a super admin can touch it."""
    if user.is_super_admin_user:
        return True
    return sheet.workflow_stage != 'finance_approved'


@login_required
def sheet_budget(request, sheet_pk):
    """Line-item cost budget for a costing sheet — finance sets a budgeted cost
    per A.1 supply line and A.2 service, then approves & releases to procurement."""
    from costing.models import ExchangeRate
    sheet = get_object_or_404(
        CostingSheet.objects.select_related('project', 'project__region'), pk=sheet_pk)
    if not _can_finance(request.user):
        messages.error(request, 'Finance access is limited to the finance team.')
        return redirect('dashboard:index')
    # Region scope (super admin sees all).
    if (not request.user.is_super_admin_user and sheet.project
            and sheet.project.region_id != request.user.region_id):
        messages.error(request, 'That costing sheet is outside your region.')
        return redirect('finance:budgeting_list')

    editable = _can_edit_budget(request.user, sheet)

    if request.method == 'POST':
        if not editable:
            messages.error(request, 'This budget is approved and locked.')
            return redirect('finance:sheet_budget', sheet_pk=sheet.pk)
        # Sheet-level budget defaults (budget-only — never touch sheet.margin /
        # sheet.discount_rate that drive the real costing).
        sheet.budget_margin = _parse_decimal(request.POST.get('budget_margin'))
        sheet.budget_discount_rate = _parse_decimal(request.POST.get('budget_discount_rate'))
        sheet.save(update_fields=['budget_margin', 'budget_discount_rate', 'updated_at'])
        def _price_override(prefix, pk):
            # Store a manual price only when the row's price field was actually
            # overridden (flag set by JS); otherwise it stays formula-driven.
            if (request.POST.get(f'{prefix}-{pk}-price_manual') or '0') == '1':
                return _parse_decimal(request.POST.get(f'{prefix}-{pk}-price'))
            return None
        for section in sheet.sections.filter(is_optional=False):
            for item in section.line_items.all():
                item.budgeted_cost = _parse_decimal(request.POST.get(f'line-{item.pk}-budget'))
                item.budget_discount = _parse_decimal(request.POST.get(f'line-{item.pk}-disc'))
                item.budget_margin = _parse_decimal(request.POST.get(f'line-{item.pk}-margin'))
                item.budget_price = _price_override('line', item.pk)
                item.budget_remarks = (request.POST.get(f'line-{item.pk}-remarks') or '').strip()
                item.save(update_fields=['budgeted_cost', 'budget_discount', 'budget_margin',
                                         'budget_price', 'budget_remarks'])
        for sow in sheet.scope_of_work_items.all():
            sow.budgeted_cost = _parse_decimal(request.POST.get(f'sow-{sow.pk}-budget'))
            sow.budget_discount = _parse_decimal(request.POST.get(f'sow-{sow.pk}-disc'))
            sow.budget_margin = _parse_decimal(request.POST.get(f'sow-{sow.pk}-margin'))
            sow.budget_price = _price_override('sow', sow.pk)
            sow.budget_remarks = (request.POST.get(f'sow-{sow.pk}-remarks') or '').strip()
            sow.save(update_fields=['budgeted_cost', 'budget_discount', 'budget_margin',
                                    'budget_price', 'budget_remarks'])
        messages.success(request, 'Budget saved.')
        return redirect('finance:sheet_budget', sheet_pk=sheet.pk)

    rates = {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}

    # Sheet-level budget default boxes (shown at the top). They fall back to the
    # costing sheet's own margin/discount. When finance sets a box it becomes a
    # global override applied to every line; when unset, each line uses its own
    # costing margin/discount so the budgeted price starts equal to the sales
    # price (variance 0).
    box_margin = sheet.budget_margin if sheet.budget_margin is not None else (sheet.margin or Decimal('0'))
    box_discount = sheet.budget_discount_rate if sheet.budget_discount_rate is not None else (sheet.discount_rate or Decimal('0'))
    global_margin = sheet.budget_margin        # None until finance sets the box
    global_discount = sheet.budget_discount_rate

    def _resolve(override, glob, costing_default):
        if override is not None:
            return override
        if glob is not None:
            return glob
        return costing_default

    def _factor(disc, margin):
        return (Decimal('1') - disc / Decimal('100')) / (Decimal('1') - min(margin, Decimal('99')) / Decimal('100'))

    def _price(cost, disc, margin):
        """Plain budgeted price = cost × (1 − disc%) / (1 − margin%)."""
        return (cost * _factor(disc, margin)).quantize(Decimal('0.01'))

    def _scaled_price(sales_price, base_cost, cost, disc, margin, cost_disc, cost_margin):
        """Budgeted price anchored to the costing sales price: scale it by the
        cost change and the margin/discount change. At the costing defaults this
        returns the sales price exactly (variance 0, no rounding drift)."""
        f_cost = _factor(cost_disc, cost_margin)
        if base_cost and f_cost:
            return (sales_price * (cost / base_cost)
                    * (_factor(disc, margin) / f_cost)).quantize(Decimal('0.01'))
        return _price(cost, disc, margin)

    cost_total = price_total = sales_total = Decimal('0')

    def _row(base_cost, budgeted_cost, ov_disc, ov_margin, item, sales_price, cost_disc, cost_margin):
        # Sales price = the line's actual price on the costing sheet (fixed).
        # Effective margin/discount: per-line override → global box → the line's
        # own costing value. The budgeted price is anchored to the sales price so
        # it starts equal to it and moves only when finance changes cost /
        # margin / discount; a manual price override wins.
        disc = _resolve(ov_disc, global_discount, cost_disc)
        margin = _resolve(ov_margin, global_margin, cost_margin)
        manual = item.budget_price is not None
        price = (item.budget_price if manual else
                 _scaled_price(sales_price, base_cost, budgeted_cost, disc, margin, cost_disc, cost_margin))
        return {
            'item': item, 'cost': budgeted_cost, 'disc': ov_disc, 'margin': ov_margin,
            'eff_disc': disc, 'eff_margin': margin, 'price': price, 'base_cost': base_cost,
            'cost_disc': cost_disc, 'cost_margin': cost_margin, 'price_manual': manual,
            'sales_price': sales_price, 'variance': price - sales_price,
        }

    sections = []
    for section in sheet.sections.filter(is_optional=False).order_by('order', 'section_number'):
        lines, sec_cost, sec_price, sec_sales = [], Decimal('0'), Decimal('0'), Decimal('0')
        for item in section.line_items.all().order_by('order', 'item_number'):
            item.set_exchange_rates_cache(rates)
            item.set_sheet_cache(sheet)
            base_cost = item.base_total_cost_sar
            budgeted_cost = item.budgeted_cost if item.budgeted_cost is not None else base_cost
            # Sales Price = the line's actual costing price (before add-ons), in SAR;
            # the line's own effective margin/discount are the per-line defaults.
            row = _row(base_cost, budgeted_cost, item.budget_discount, item.budget_margin,
                       item, item.base_total_price,
                       item.effective_discount_pct * Decimal('100'),
                       item.effective_margin * Decimal('100'))
            lines.append(row)
            sec_cost += budgeted_cost
            sec_price += row['price']
            sec_sales += row['sales_price']
        if lines:
            sections.append({'section': section, 'lines': lines,
                             'cost': sec_cost, 'price': sec_price, 'sales': sec_sales})
            cost_total += sec_cost
            price_total += sec_price
            sales_total += sec_sales

    services, svc_cost, svc_price, svc_sales = [], Decimal('0'), Decimal('0'), Decimal('0')
    for sow in sheet.scope_of_work_items.all().order_by('order', 'serial_number'):
        base_cost = sow.total_price or Decimal('0')
        budgeted_cost = sow.budgeted_cost if sow.budgeted_cost is not None else base_cost
        # Services are billed at cost on the costing sheet — Sales Price = cost,
        # and their costing margin/discount default to 0.
        row = _row(base_cost, budgeted_cost, sow.budget_discount, sow.budget_margin,
                   sow, base_cost, Decimal('0'), Decimal('0'))
        services.append(row)
        svc_cost += budgeted_cost
        svc_price += row['price']
        svc_sales += row['sales_price']
    cost_total += svc_cost
    price_total += svc_price
    sales_total += svc_sales

    can_approve = (sheet.workflow_stage == 'finance_review'
                   and (request.user.is_super_admin_user
                        or getattr(request.user, 'is_finance_team_user', False)))

    return render(request, 'finance/sheet_budget.html', {
        'sheet': sheet, 'sections': sections, 'services': services,
        'box_margin': box_margin, 'box_discount': box_discount,
        'box_margin_set': global_margin is not None,
        'box_discount_set': global_discount is not None,
        'cost_total': cost_total, 'price_total': price_total, 'sales_total': sales_total,
        'variance_total': price_total - sales_total,
        'editable': editable, 'can_approve': can_approve,
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
