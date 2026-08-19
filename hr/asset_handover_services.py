"""Service layer for the AssetHandover digital-signature workflow.

Issuance: Issued By (HR, at creation) -> Authorized By (Super Admin,
race-guarded so only the first submission wins) -> Received By (Employee,
which mirrors the handover onto AssetAssignment so existing "assets in
custody" views keep working unchanged).

Return: Returned By (Employee) -> Received By (HR, acknowledging the
return - also race-guarded), which releases custody.
"""
from django.db import transaction
from django.utils import timezone

from hr.models import AssetHandover, AssetAssignment, AssetHandoverAuthorizerPreference, Asset


def create_asset_handover(employee, item, issued_by, issued_signature, authorizer,
                           accessories='', software_installed='', remember_authorizer=False):
    """HR creates and signs a new handover in one step. `item` is an Asset
    or a Vehicle instance - whichever model it belongs to determines which
    FK gets set. `authorizer` must be a Super Admin (checked by the caller/
    view, not here, matching this codebase's existing separation between
    view-layer permission checks and service-layer business logic).
    Refuses to create a second handover for an item that already has one
    in progress (or, for Assets, an open legacy AssetAssignment) - without
    this guard two handovers for the same item could both reach
    pending_receipt and race each other in receive_handover."""
    from hr.models import Asset, Vehicle

    if isinstance(item, Asset):
        if AssetAssignment.objects.filter(asset=item, returned_at__isnull=True).exists():
            raise ValueError(
                f'{item} already has an open assignment. Resolve it with HR/IT '
                'before starting a new handover.')
        already_in_progress = AssetHandover.objects.filter(
            asset=item).exclude(status='returned').exists()
    elif isinstance(item, Vehicle):
        already_in_progress = AssetHandover.objects.filter(
            vehicle=item).exclude(status='returned').exists()
    else:
        raise ValueError('item must be an Asset or a Vehicle instance.')
    if already_in_progress:
        raise ValueError(f'{item} already has a handover in progress.')

    handover = AssetHandover(
        employee=employee,
        accessories=accessories,
        software_installed=software_installed,
        status='pending_authorization',
        issued_by=issued_by,
        issued_signature=issued_signature,
        issued_at=timezone.now(),
        authorizer=authorizer,
    )
    if isinstance(item, Asset):
        handover.asset = item
    elif isinstance(item, Vehicle):
        handover.vehicle = item
    else:
        raise ValueError('item must be an Asset or a Vehicle instance.')
    handover.save()

    if remember_authorizer:
        AssetHandoverAuthorizerPreference.objects.update_or_create(
            user=issued_by, defaults={'default_authorizer': authorizer})

    from notifications.services import create_notification
    from django.urls import reverse
    if authorizer:
        create_notification(
            recipient=authorizer,
            verb=f'Asset handover awaiting your authorization: {handover.item} to {employee.full_name}',
            actor=issued_by, level='info',
            target_url=reverse('hr:asset_handover_detail', args=[handover.pk]),
        )
    return handover


def _finalize_handover(handover, allowed_statuses, apply, race_message, post_save=None):
    """Shared locking core, mirroring
    attendance_exception_services._finalize_attendance_exception: lock the
    row, re-check its status is still one of the caller's allowed starting
    states, apply the caller-supplied mutations, save - all inside one
    transaction so two people acting on the same handover at the same
    moment can never both succeed. post_save, if given, runs inside the
    same atomic block right after the save - use it for anything that
    must commit-or-rollback together with the status change (e.g.
    creating the mirrored AssetAssignment row), rather than as a
    separate step after this function returns."""
    with transaction.atomic():
        locked = AssetHandover.objects.select_for_update().get(pk=handover.pk)
        if locked.status not in allowed_statuses:
            raise ValueError(race_message(locked.status))
        apply(locked)
        locked.save()
        if post_save is not None:
            post_save(locked)

    for field in ('status', 'authorized_signature', 'authorized_at',
                  'received_signature', 'received_at',
                  'returned_signature', 'returned_at',
                  'return_received_by', 'return_received_by_id',
                  'return_received_signature', 'return_received_at'):
        setattr(handover, field, getattr(locked, field))
    return handover


def authorize_handover(handover, authorizing_user, signature):
    """The selected Super Admin signs Authorized By. Only the first
    submission succeeds - a second Super Admin trying afterward gets a
    clear 'already authorized' error rather than silently overwriting."""
    def apply(locked):
        locked.authorized_signature = signature
        locked.authorized_at = timezone.now()
        locked.status = 'pending_receipt'

    result = _finalize_handover(
        handover, allowed_statuses={'pending_authorization'}, apply=apply,
        race_message=lambda current: f'This handover has already been authorized (current status: {current}).')

    from notifications.services import create_notification
    from django.urls import reverse
    if handover.employee.user_id:
        create_notification(
            recipient=handover.employee.user,
            verb=f'Asset handover ready for your signature: {handover.item}',
            actor=authorizing_user, level='info',
            target_url=reverse('hr:my_profile'),
        )
    return result

def receive_handover(handover, signature):
    """The employee signs Received By, completing issuance. This mirrors
    the handover onto AssetAssignment (for Assets only - Vehicles don't
    have an equivalent assignment model, they use the existing
    driver_id/driver_name denormalised fields, updated separately).
    The AssetAssignment row is created inside the same locked transaction
    as the status change, and only if the item doesn't already have one
    open - so a stale legacy assignment (or a race between two handovers
    for the same asset) surfaces as a clean ValueError here rather than
    an unhandled IntegrityError from the database's uniqueness constraint."""
    def apply(locked):
        locked.received_signature = signature
        locked.received_at = timezone.now()
        locked.status = 'active'

    def post_save(locked):
        if locked.asset_id:
            if AssetAssignment.objects.filter(
                    asset_id=locked.asset_id, returned_at__isnull=True).exists():
                raise ValueError(
                    'This asset already has an open assignment. Contact HR/IT to '
                    'resolve it before this handover can be received.')
            AssetAssignment.objects.create(
                asset_id=locked.asset_id, employee=locked.employee,
                assigned_at=timezone.now().date(), assigned_by_id=locked.issued_by_id,
                handover_form=None,
            )
            # Keep the legacy in_stock checkbox accurate too - it is no
            # longer the source of truth for custody (AssetHandover is),
            # but the Asset edit form still shows it, and CSV export still
            # reads it, so it shouldn't silently drift from reality.
            Asset.objects.filter(pk=locked.asset_id).update(in_stock=False)

    result = _finalize_handover(
        handover, allowed_statuses={'pending_receipt'}, apply=apply,
        race_message=lambda current: f'This handover has already been received (current status: {current}).',
        post_save=post_save)

    from notifications.services import create_notification
    from django.urls import reverse
    if handover.issued_by_id:
        create_notification(
            recipient=handover.issued_by,
            verb=f'Asset handover completed: {handover.item} to {handover.employee.full_name}',
            actor=result.employee.user if result.employee.user_id else None, level='success',
            target_url=reverse('hr:asset_handover_detail', args=[handover.pk]),
        )
    return result


def initiate_return(handover, receiving_user, signature, remarks=''):
    # HR clicks Return Asset and signs Received By first, starting the
    # return flow and requesting the employee confirm.
    def apply(locked):
        locked.return_received_by = receiving_user
        locked.return_received_signature = signature
        locked.return_received_at = timezone.now()
        locked.return_remarks = remarks
        locked.status = 'pending_return_confirmation'

    result = _finalize_handover(
        handover, allowed_statuses={'active'}, apply=apply,
        race_message=lambda current: f'This handover is not currently active (current status: {current}).')

    from notifications.services import create_notification
    from django.urls import reverse
    if handover.employee.user_id:
        create_notification(
            recipient=handover.employee.user,
            verb=f'HR is requesting the return of {handover.item} - please confirm',
            actor=receiving_user, level='info',
            target_url=reverse('hr:my_profile'),
        )
    return result


def acknowledge_return(handover, signature):
    # The employee signs Returned By, confirming the return and releasing
    # custody. Race-guarded the same way as receive_handover: only the
    # first submission succeeds.
    def apply(locked):
        locked.returned_signature = signature
        locked.returned_at = timezone.now()
        locked.status = 'returned'

    result = _finalize_handover(
        handover, allowed_statuses={'pending_return_confirmation'}, apply=apply,
        race_message=lambda current: f'This return has already been confirmed (current status: {current}).')

    if result.asset_id:
        AssetAssignment.objects.filter(
            asset=result.asset, employee=result.employee, returned_at__isnull=True
        ).update(returned_at=timezone.now().date())
        # Keep the legacy in_stock checkbox accurate too - see the matching
        # note in receive_handover above.
        Asset.objects.filter(pk=result.asset_id).update(in_stock=True)

    return result

def get_default_authorizer(user):
    """The HR user's remembered default authorizer, if they've ever
    checked 'set as default authorizer' before. None if they haven't."""
    pref = AssetHandoverAuthorizerPreference.objects.filter(user=user).first()
    return pref.default_authorizer if pref else None
