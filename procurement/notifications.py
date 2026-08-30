"""Telling the right person a purchase order is waiting on their signature.

Who is NOTIFIED and who is ALLOWED are separate questions, deliberately.
PurchaseOrder.can_user_approve_stage() answers the second and is not touched
here — a super admin may still sign anything. This module answers the first,
which needs a real account with a real address, and the approval stages name
their signers as plain text ("Ali Sultan") with no link to a user.
"""


def stage_recipients(stage_key):
    """Users to tell about a stage, best match first.

    Three sources, in order of how well they answer "who is this waiting on":

    1. The POStageApprover mapping — someone configured this deliberately.
    2. Otherwise every user holding the stage's role, so an unconfigured
       system still tells somebody rather than nobody. Noisier, but a missed
       approval costs more than a redundant email.
    3. Otherwise nobody, returned as an empty list rather than an error: a PO
       must still be creatable on a system where the roles have not been set
       up yet.
    """
    from accounts.models import User
    from .models import POStageApprover

    mapped = (POStageApprover.objects
              .filter(stage=stage_key, user__is_active=True)
              .select_related('user'))
    users = [row.user for row in mapped]
    if users:
        return users

    # Role fallback, mirroring can_user_approve_stage()'s mapping.
    active = User.objects.filter(is_active=True)
    if stage_key == 'scm':
        return [u for u in active if getattr(u, 'is_procurement_manager_user', False)]
    if stage_key in ('pm', 'coo'):
        return [u for u in active if u.is_admin_user]
    if stage_key == 'ceo':
        return [u for u in active if u.is_super_admin_user]
    return []


def notify_stage_approver(po, *, actor=None, base_url=''):
    """Tell whoever the PO is now waiting on, in-app and by email.

    Returns the notifications created, which is what the tests assert on.

    Silent for a PO that is not waiting on anyone — released, cancelled or
    locked — because those are not somebody's outstanding work and an email
    saying otherwise trains people to ignore the next one.
    """
    from django.urls import reverse
    from notifications.services import notify_users

    if po.status == 'cancelled' or po.is_locked:
        return []
    stage = po.current_stage
    if stage is None:
        return []

    recipients = stage_recipients(stage['key'])
    if not recipients:
        return []

    path = reverse('procurement:po_detail', args=[po.pk])
    # An absolute URL when the caller can supply the host, so the link works
    # from an inbox. A relative one still works from the in-app bell.
    target_url = f'{base_url.rstrip("/")}{path}' if base_url else path

    value = f'{po.total_value:,.2f} {po.currency}' if po.total_value else ''
    description = (
        f'{po.po_number} for {po.vendor_name}'
        + (f', {value}' if value else '')
        + f'. Waiting on {stage["label"]}. Open the purchase order to review '
        'and sign it.'
    )

    return notify_users(
        recipients=recipients,
        verb=f'{stage["label"]} needed on {po.po_number}',
        actor=actor,
        target=po,
        target_url=target_url,
        description=description,
        level='warning',
        send_email=True,
    )


def notify_stage_approver_on_commit(po, *, actor=None, request=None):
    """Queue the notification for after the surrounding transaction commits.

    An approval writes the signature inside an atomic block. Sending from
    inside it risks an email describing a signature that then rolls back —
    and there is no way to recall it.
    """
    from django.db import transaction

    base_url = request.build_absolute_uri('/') if request is not None else ''
    transaction.on_commit(
        lambda: notify_stage_approver(po, actor=actor, base_url=base_url))
