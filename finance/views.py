from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from projects.models import Project
from costing.models import CostingSheet
from .models import ProjectFinance, PaymentMilestone


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

    finances = {f.project_id: f for f in ProjectFinance.objects.filter(
        project__in=projects)}
    rows = [{'project': p, 'finance': finances.get(p.id)} for p in projects]
    return render(request, 'finance/home.html', {'rows': rows})


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

        # Save the header + all milestone rows.
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
