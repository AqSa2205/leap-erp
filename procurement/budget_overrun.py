"""Whether purchase orders have taken a budget over its approved amount.

Over budget is measured in money, not quantity: a budget line approved at 50
SAR with linked PO lines costing 80 SAR is 30 over, even if the quantity is
within budget. It is checked at two levels:

  * per budget line - everything linked to the line against its approved price;
  * per budget (costing sheet) - everything linked to the sheet against the
    sheet's approved total.

Spend is measured the way budget_status measures commitment, so the two never
disagree: the PO line's value after the PO's discount, without VAT (budgets
carry none), converted to SAR. Every PO counts except released (cancelled)
ones, so a draft is flagged as soon as it pushes a budget over.

Procurement sub items (added_by_procurement) have no approved budget of their
own - adding one must never move the finance-approved figure - so their spend
counts against their parent line.

Nothing here writes. Going over never blocks a PO; the caller records the
overrun and routes it for approval.
"""
from decimal import Decimal

from .budget_status import RELEASED_STATUSES, exchange_rates, to_base_currency

ZERO = Decimal('0')
HUNDRED = Decimal('100')
CENT = Decimal('0.01')


def item_spend_sar(item, rates):
    """One PO line's cost against the budget, in SAR."""
    po = item.purchase_order
    value = item.total_value or ZERO
    discount = po.discount_rate or ZERO
    after_discount = (value * (HUNDRED - discount) / HUNDRED).quantize(CENT)
    return to_base_currency(after_discount, po.currency, rates)


def budget_lines(sheet):
    """The sheet's lines that carry spend, sub items included, with
    everything the arithmetic walks loaded up front."""
    from costing.models import CostingLineItem
    return (CostingLineItem.all_objects
            .filter(section__costing_sheet=sheet, section__is_optional=False)
            .select_related('section')
            .prefetch_related('procured_po_items__purchase_order'))


def check_sheet(sheet, rates=None):
    """Budget against spend for one costing sheet.

    Returns a dict:
      budget, spend, over   - whole-budget figures in SAR (over is 0 if within)
      lines                 - one dict per budget line that is over:
                              {line, budget, spend, over}
    """
    rates = exchange_rates() if rates is None else rates
    lines = list(budget_lines(sheet))

    budgets = {}
    spends = {}
    for line in lines:
        target = line.parent_item_id or line.pk
        spend = ZERO
        for item in line.procured_po_items.all():
            if item.purchase_order.status in RELEASED_STATUSES:
                continue
            spend += item_spend_sar(item, rates)
        spends[target] = spends.get(target, ZERO) + spend
        if not line.added_by_procurement:
            line.set_exchange_rates_cache(rates)
            line.set_sheet_cache(sheet)
            budgets[line.pk] = (line, line.budget_line_price())

    over_lines = []
    for pk, (line, budget) in budgets.items():
        spend = spends.get(pk, ZERO)
        if spend > budget:
            over_lines.append({'line': line, 'budget': budget,
                               'spend': spend, 'over': spend - budget})

    total_budget = sum((b for _line, b in budgets.values()), ZERO)
    total_spend = sum(spends.values(), ZERO)
    return {
        'budget': total_budget,
        'spend': total_spend,
        'over': max(total_spend - total_budget, ZERO),
        'lines': over_lines,
    }

# ─── Recording overruns ────────────────────────────────────────────────────

def _models():
    from .models import BudgetOverrun, BudgetOverrunEvent, BudgetOverrunLine
    return BudgetOverrun, BudgetOverrunEvent, BudgetOverrunLine


def _covered(approved, whole_over, lines):
    """Whether an approved request already covers this overrun. A larger
    overrun, or a line that was not part of it, needs a fresh decision."""
    if whole_over > approved.over_total:
        return False
    approved_lines = {l.budget_line_id: l.over for l in approved.lines.all()}
    return all(l['line'].pk in approved_lines and l['over'] <= approved_lines[l['line'].pk]
               for l in lines)


def record_for_po(po, actor=None, base_url=''):
    """Bring this PO's overrun requests up to date. Call after anything that
    changes what a PO spends against a budget: creating it from a budget,
    linking it, editing its lines or prices, or cancelling it.

    Never blocks. Returns the overrun requests touched.
    """
    from costing.models import CostingSheet
    BudgetOverrun, _event, _line = _models()
    items = list(po.items.select_related('source_bom_item__section'))
    sheet_ids = {i.source_bom_item.section.costing_sheet_id
                 for i in items if i.source_bom_item_id}
    # A sheet the PO no longer touches may still have an open request to resolve.
    sheet_ids |= set(BudgetOverrun.objects
                     .filter(purchase_order=po, status__in=BudgetOverrun.OPEN_STATUSES)
                     .values_list('costing_sheet_id', flat=True))
    rates = exchange_rates()
    touched = []
    for sheet in CostingSheet.objects.filter(pk__in=sheet_ids):
        overrun = _record_sheet(po, sheet, items, rates, actor, base_url)
        if overrun is not None:
            touched.append(overrun)
    return touched


def _record_sheet(po, sheet, items, rates, actor, base_url=''):
    from django.db import transaction
    BudgetOverrun, BudgetOverrunEvent, BudgetOverrunLine = _models()

    # The budget lines this PO spends on, sub items counted on their parent.
    po_lines = set()
    if po.status not in RELEASED_STATUSES:
        for i in items:
            line = i.source_bom_item
            if line is not None and line.section.costing_sheet_id == sheet.pk:
                po_lines.add(line.parent_item_id or line.pk)

    result = check_sheet(sheet, rates)
    lines = [l for l in result['lines'] if l['line'].pk in po_lines]
    whole_over = result['over'] if po_lines else ZERO

    with transaction.atomic():
        overrun = (BudgetOverrun.objects.select_for_update()
                   .filter(purchase_order=po, costing_sheet=sheet,
                           status__in=BudgetOverrun.OPEN_STATUSES).first())

        if not lines and whole_over <= ZERO:
            if overrun is not None:
                overrun.status = BudgetOverrun.RESOLVED
                overrun.save(update_fields=['status', 'updated_at'])
                BudgetOverrunEvent.objects.create(
                    overrun=overrun, action=BudgetOverrunEvent.RESOLVED, actor=actor,
                    over_total=ZERO)
            return overrun

        if overrun is None:
            approved = (BudgetOverrun.objects
                        .filter(purchase_order=po, costing_sheet=sheet,
                                status=BudgetOverrun.APPROVED)
                        .order_by('-decided_at').first())
            if approved is not None and _covered(approved, whole_over, lines):
                return approved
            # A rejected request sends the PO back for revision. Until the
            # figures change, nothing has been revised, so nothing new to ask.
            rejected = (BudgetOverrun.objects
                        .filter(purchase_order=po, costing_sheet=sheet,
                                status=BudgetOverrun.REJECTED)
                        .order_by('-decided_at').first())
            if rejected is not None and _same_figures(rejected, whole_over, lines):
                return rejected
            overrun = BudgetOverrun(purchase_order=po, costing_sheet=sheet)
            action = BudgetOverrunEvent.FLAGGED
        else:
            old = (overrun.over_total,
                   sorted((l.budget_line_id, l.over) for l in overrun.lines.all()))
            new = (whole_over, sorted((l['line'].pk, l['over']) for l in lines))
            action = BudgetOverrunEvent.UPDATED if old != new else None

        overrun.budget_total = result['budget']
        overrun.spend_total = result['spend']
        overrun.over_total = whole_over
        overrun.save()
        overrun.lines.all().delete()
        BudgetOverrunLine.objects.bulk_create([
            BudgetOverrunLine(overrun=overrun, budget_line=l['line'], budget=l['budget'],
                              spend=l['spend'], over=l['over'])
            for l in lines])
        if action is not None:
            BudgetOverrunEvent.objects.create(
                overrun=overrun, action=action, actor=actor, over_total=whole_over)
        if action == BudgetOverrunEvent.FLAGGED:
            _queue(notify_remarks_needed, overrun, actor, base_url)
    return overrun


def add_remarks(overrun, stage, user, text, base_url=''):
    """SCM or PM remarks. Once both are in, the request waits for the COO."""
    from django.db import transaction
    from django.utils import timezone
    BudgetOverrun, BudgetOverrunEvent, _line = _models()
    if stage not in ('scm', 'pm'):
        raise ValueError('Remarks come from the SCM or PM signer.')
    text = (text or '').strip()
    if not text:
        raise ValueError('Remarks cannot be empty.')
    if not overrun.purchase_order.can_user_approve_stage(user, stage):
        raise PermissionError('You cannot add remarks for this stage.')
    with transaction.atomic():
        overrun = BudgetOverrun.objects.select_for_update().get(pk=overrun.pk)
        if not overrun.is_open:
            raise ValueError('This request has already been decided.')
        was_waiting_on_coo = overrun.status == BudgetOverrun.AWAITING_COO
        setattr(overrun, f'{stage}_remarks', text)
        setattr(overrun, f'{stage}_remarks_by', user)
        setattr(overrun, f'{stage}_remarks_at', timezone.now())
        if overrun.scm_remarks and overrun.pm_remarks:
            overrun.status = BudgetOverrun.AWAITING_COO
        overrun.save()
        BudgetOverrunEvent.objects.create(
            overrun=overrun, action=f'{stage}_remarks', actor=user, remarks=text,
            over_total=overrun.over_total)
        if overrun.status == BudgetOverrun.AWAITING_COO and not was_waiting_on_coo:
            _queue(notify_decision_needed, overrun, user, base_url)
    return overrun


def decide(overrun, user, approve, remarks='', base_url=''):
    """The COO signer's decision, once SCM and PM have both given remarks."""
    from django.db import transaction
    from django.utils import timezone
    BudgetOverrun, BudgetOverrunEvent, _line = _models()
    remarks = (remarks or '').strip()
    if not approve and not remarks:
        raise ValueError('Give a reason when rejecting.')
    if not overrun.purchase_order.can_user_approve_stage(user, 'coo'):
        raise PermissionError('Only the COO signer can decide this.')
    with transaction.atomic():
        overrun = BudgetOverrun.objects.select_for_update().get(pk=overrun.pk)
        if overrun.status != BudgetOverrun.AWAITING_COO:
            raise ValueError('This request is not waiting for a COO decision.')
        overrun.status = BudgetOverrun.APPROVED if approve else BudgetOverrun.REJECTED
        overrun.decision_remarks = remarks
        overrun.decided_by = user
        overrun.decided_at = timezone.now()
        overrun.save()
        event_remarks = remarks
        if not approve:
            cleared = _send_back_for_revision(overrun.purchase_order_id, user, remarks)
            if cleared:
                event_remarks = f'{remarks}\nApprovals cleared: {cleared}'
        BudgetOverrunEvent.objects.create(
            overrun=overrun,
            action=BudgetOverrunEvent.APPROVED if approve else BudgetOverrunEvent.REJECTED,
            actor=user, remarks=event_remarks, over_total=overrun.over_total)
        _queue(notify_decided, overrun, user, base_url)
    return overrun


def line_flags(sheet):
    """{budget line pk: status of its latest overrun} for lines on this
    sheet that are, or were, over budget. Resolved requests are not flags."""
    _overrun, _event, BudgetOverrunLine = _models()
    flags = {}
    rows = (BudgetOverrunLine.objects
            .filter(overrun__costing_sheet=sheet)
            .exclude(overrun__status='resolved')
            .order_by('overrun__created_at')
            .values_list('budget_line_id', 'overrun__status'))
    for line_pk, status in rows:
        flags[line_pk] = status
    return flags

def refresh_safely(po, actor=None, request=None):
    """record_for_po that can never break the save that called it.

    Going over budget never blocks a purchase order, and neither may a fault
    in checking it: any error is logged and the caller carries on. The check
    runs in its own savepoint so a database error inside it cannot poison an
    outer transaction. Re-fetches the PO, because callers often hold an
    instance whose prefetched items predate the writes they just made.
    """
    import logging
    from django.db import transaction
    from .models import PurchaseOrder
    try:
        with transaction.atomic():
            base_url = request.build_absolute_uri('/') if request is not None else ''
            return record_for_po(PurchaseOrder.objects.get(pk=po.pk), actor=actor,
                                 base_url=base_url)
    except Exception:
        logging.getLogger(__name__).exception(
            'Budget overrun check failed for purchase order %s', po.pk)
        return []



def overrun_context(po, user):
    """What the PO detail page needs to show budget warnings."""
    overruns = list(po.budget_overruns
                    .select_related('costing_sheet', 'scm_remarks_by', 'pm_remarks_by', 'decided_by')
                    .prefetch_related('lines__budget_line', 'events__actor'))
    # Option A: one request per PO, so the same budget line can be over on
    # several POs. Show each line's other requests, so whoever decides sees
    # what was already asked and decided for that line. One query.
    from .models import BudgetOverrun, BudgetOverrunLine
    line_ids = {l.budget_line_id for o in overruns for l in o.lines.all()}
    others = {}
    if line_ids:
        rows = (BudgetOverrunLine.objects
                .filter(budget_line_id__in=line_ids)
                .exclude(overrun__purchase_order=po)
                .exclude(overrun__status=BudgetOverrun.RESOLVED)
                .select_related('overrun__purchase_order')
                .order_by('overrun__created_at'))
        for row in rows:
            others.setdefault(row.budget_line_id, []).append(row.overrun)
    for o in overruns:
        for l in o.lines.all():
            l.others = others.get(l.budget_line_id, [])
    return {
        'budget_linked': po.items.filter(source_bom_item__isnull=False).exists(),
        'budget_overruns': overruns,
        'overrun_can_scm': po.can_user_approve_stage(user, 'scm'),
        'overrun_can_pm': po.can_user_approve_stage(user, 'pm'),
        'overrun_can_coo': po.can_user_approve_stage(user, 'coo'),
    }



def approved_budgets_for_po(po):
    """The finance-approved budgets of this PO's project: what it could be
    linked to. Empty when the PO has no project."""
    from costing.models import CostingSheet
    from .budget_status import APPROVED_STAGE
    if not po.project_id:
        return CostingSheet.objects.none()
    return (CostingSheet.objects
            .filter(project_id=po.project_id, workflow_stage=APPROVED_STAGE)
            .order_by('title'))



# --- Notifications ------------------------------------------------------------
#
# Same recipients as the normal approval chain (stage_recipients), in-app only:
# no email, by decision. Queued until the transaction commits so nothing is
# sent about a change that rolled back. A failed send is logged, never raised:
# telling people must not break the save it is about.

def _queue(fn, overrun, actor, base_url):
    from django.db import transaction
    pk = overrun.pk
    transaction.on_commit(lambda: _send_safely(fn, pk, actor, base_url))


def _send_safely(fn, overrun_pk, actor, base_url):
    import logging
    from .models import BudgetOverrun
    try:
        overrun = (BudgetOverrun.objects
                   .select_related('purchase_order', 'costing_sheet', 'purchase_order__created_by')
                   .prefetch_related('lines__budget_line')
                   .get(pk=overrun_pk))
        fn(overrun, actor, base_url)
    except Exception:
        logging.getLogger(__name__).exception(
            'Budget overrun notification failed for request %s', overrun_pk)


def _overrun_summary(overrun):
    parts = []
    if overrun.over_total > 0:
        parts.append(f'the whole budget by {overrun.over_total:,.2f} SAR')
    for line in overrun.lines.all():
        parts.append(f'{line.budget_line.description} by {line.over:,.2f} SAR')
    return ', '.join(parts) or 'its budget'


def _send(overrun, recipients, verb, description, actor, base_url, level):
    from django.urls import reverse
    from notifications.services import notify_users
    unique = list({u.pk: u for u in recipients if u is not None and u.is_active}.values())
    if not unique:
        return []
    path = reverse('procurement:po_detail', args=[overrun.purchase_order_id])
    target_url = f'{base_url.rstrip("/")}{path}' if base_url else path
    return notify_users(
        recipients=unique, verb=verb, actor=actor, target=overrun.purchase_order,
        target_url=target_url, description=description, level=level, send_email=False)


def notify_remarks_needed(overrun, actor=None, base_url=''):
    """Flagged: the SCM and PM signers each need to add remarks."""
    from .notifications import stage_recipients
    po = overrun.purchase_order
    return _send(
        overrun, stage_recipients('scm') + stage_recipients('pm'),
        f'Over-budget remarks needed on {po.po_number}',
        f'{po.po_number} takes {overrun.costing_sheet.title} over budget: '
        f'{_overrun_summary(overrun)}. SCM and PM remarks are needed before the COO '
        'decides. The purchase order is not blocked.',
        actor, base_url, 'warning')


def notify_decision_needed(overrun, actor=None, base_url=''):
    """Both remarks are in: the COO signer decides."""
    from .notifications import stage_recipients
    po = overrun.purchase_order
    return _send(
        overrun, stage_recipients('coo'),
        f'Over-budget decision needed on {po.po_number}',
        f'{po.po_number} takes {overrun.costing_sheet.title} over budget: '
        f'{_overrun_summary(overrun)}. SCM and PM have added their remarks. '
        'Open the purchase order to approve or reject the overrun.',
        actor, base_url, 'warning')


def notify_decided(overrun, actor=None, base_url=''):
    """The COO has decided: tell the PO's creator and the SCM and PM signers."""
    from .models import BudgetOverrun
    from .notifications import stage_recipients
    po = overrun.purchase_order
    outcome = 'approved' if overrun.status == BudgetOverrun.APPROVED else 'rejected'
    remarks = f' Remarks: {overrun.decision_remarks}' if overrun.decision_remarks else ''
    return _send(
        overrun, [po.created_by] + stage_recipients('scm') + stage_recipients('pm'),
        f'Over-budget request {outcome} on {po.po_number}',
        f'The COO {outcome} the overrun on {po.po_number} '
        f'({_overrun_summary(overrun)}).{remarks}'
        + (' The purchase order has been sent back to draft for revision and its '
           'approvals cleared.' if outcome == 'rejected' else ''),
        actor, base_url, 'success' if outcome == 'approved' else 'error')



def _same_figures(overrun, whole_over, lines):
    """Whether a request recorded exactly these overrun figures."""
    if overrun.over_total != whole_over:
        return False
    recorded = sorted((l.budget_line_id, l.over) for l in overrun.lines.all())
    return recorded == sorted((l['line'].pk, l['over']) for l in lines)


def _send_back_for_revision(po_pk, user, remarks):
    """The COO rejected the overrun: clear the PO's approvals, so the revised
    PO is approved again from SCM, and move it back to draft. Who had signed,
    and when, goes into the PO's status history so nothing is lost. Returns
    that summary ('' if nothing was signed).
    """
    from django.utils import timezone
    from .models import PurchaseOrder
    po = PurchaseOrder.objects.select_for_update().get(pk=po_pk)
    signed, fields = [], []
    for key, label, _default in PurchaseOrder.APPROVAL_STAGES:
        at = getattr(po, f'{key}_approved_at')
        if at is None:
            continue
        by = getattr(po, f'{key}_approved_by')
        who = (by.get_full_name() or by.username) if by else 'unknown'
        signed.append(f'{label} by {who} on {timezone.localtime(at):%d %b %Y %H:%M}')
        setattr(po, f'{key}_approved_at', None)
        setattr(po, f'{key}_approved_by', None)
        setattr(po, f'{key}_signature', None)
        fields += [f'{key}_approved_at', f'{key}_approved_by', f'{key}_signature']
    if fields:
        po.save(update_fields=fields + ['updated_at'])
    cleared = '; '.join(signed)
    if po.status != 'draft':
        reason = f'Over-budget overrun rejected by the COO: {remarks} Sent back to draft for revision.'
        if cleared:
            reason += f' Approvals cleared: {cleared}.'
        po.record_status_change(to_status='draft', changed_by=user, reason=reason)
    return cleared



def approved_overruns(sheet):
    """{budget line pk: approved overrun in SAR} for this sheet's lines.

    Shown beside the finance-approved budget, never added into it. Each
    request records the line's whole overrun at that moment, so a line with
    several approved requests (one per PO) takes the largest, not the sum,
    which would count the same overspend more than once.
    """
    from .models import BudgetOverrun, BudgetOverrunLine
    amounts = {}
    rows = (BudgetOverrunLine.objects
            .filter(overrun__costing_sheet=sheet, overrun__status=BudgetOverrun.APPROVED)
            .values_list('budget_line_id', 'over'))
    for line_pk, over in rows:
        if over > amounts.get(line_pk, ZERO):
            amounts[line_pk] = over
    return amounts



def needs_budget_prompt(po):
    """Whether to ask, right after creating this PO, if it should be linked to
    an approved budget: it has no budget link yet, and its project has at
    least one finance-approved budget to link it to."""
    if po.items.filter(source_bom_item__isnull=False).exists():
        return False
    return approved_budgets_for_po(po).exists()
