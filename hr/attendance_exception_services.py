"""Service layer for the single-approver-plus-override attendance exception
workflow (site visits, outside meetings, etc. excused from the Wi-Fi
attendance tracker).

Simpler sibling of hr/leave_approval_services.py's dual-approval workflow:
there's no multi-approver fan-out here, just one manager snapshotted at
submission time, plus an HR/upstream-hierarchy override path. Both decision
paths funnel through a single locking helper (_finalize_attendance_exception)
which is this file's analogue of _finalize there — the one place that flips
status and derives the day's AttendanceRecord outcome.
"""
from django.db import transaction
from django.utils import timezone

from hr.models import AttendanceException, AttendanceExceptionRevokeRequest, AttendanceRecord
from notifications.services import notify_users


def submit_attendance_exception(*, employee, event_date, event_start_time, reason_category,
                                 custom_reason='', employee_comment='', created_by):
    """Creates the AttendanceException, snapshotting employee.main_manager as
    the assigned approver (so a later reassignment doesn't retroactively
    change who owned the decision), and notifies the manager (if they have a
    linked login). Rejects a duplicate submission for the same employee and
    exact event moment while an earlier one is still undecided (pending or
    expired-but-unswept) — a double-click or resubmit must not create two
    queue entries for the same event."""
    if AttendanceException.objects.filter(
            employee=employee, event_date=event_date, event_start_time=event_start_time,
            status__in=('pending', 'expired')).exists():
        raise ValueError('An attendance exception for this exact event has already been submitted and is still awaiting a decision.')
    exc = AttendanceException.objects.create(
        employee=employee, event_date=event_date, event_start_time=event_start_time,
        reason_category=reason_category, custom_reason=custom_reason, employee_comment=employee_comment,
        main_manager=employee.main_manager, created_by=created_by,
    )
    if exc.main_manager and exc.main_manager.user_id:
        notify_users(
            recipients=[exc.main_manager.user],
            verb=f'{employee.full_name} submitted an attendance exception for {event_date}',
            actor=created_by,
            description=exc.get_reason_category_display(),
        )
    return exc


def decide_attendance_exception(exc, deciding_user, decision, note=''):
    """The assigned manager approves or rejects."""
    if exc.employee.user_id and exc.employee.user_id == deciding_user.id:
        raise ValueError("You cannot decide on your own attendance exception request.")
    if decision not in ('approved', 'rejected'):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    if exc.status not in ('pending', 'expired'):
        raise ValueError(f'This request is already {exc.status}.')
    # Checks the LIVE exc.employee.main_manager relationship, not the
    # exc.main_manager snapshot taken at submission time: if the employee's
    # main_manager is assigned/corrected via the Org Chart page after this
    # request was already submitted, the now-correctly-assigned manager must
    # be authorized immediately — not permanently blocked from the plain
    # decide path by a stale snapshot. exc.main_manager itself is left
    # untouched as historical/audit metadata; this only changes what
    # authorization reads.
    if not exc.employee.main_manager or exc.employee.main_manager.user_id != deciding_user.id:
        raise ValueError('You are not the assigned manager for this request.')
    # Deliberately NO is_within_decision_window()/decision_deadline check
    # here (removed): a late decision — including one the cron sweep has
    # already auto-expired — must still be decidable by the real assigned
    # manager as a normal decision, not forced through the Override path.
    # allowed_statuses={'pending', 'expired'} below is what actually lets an
    # expired request through; the manager-identity check above is unchanged.

    now = timezone.now()

    def apply(locked):
        locked.status = decision
        locked.decision_note = note
        locked.decided_by = deciding_user
        locked.decided_at = now

    _finalize_attendance_exception(
        exc, allowed_statuses={'pending', 'expired'}, apply=apply,
        race_message=lambda current_status: f'This request is already {current_status}.',
    )

    if exc.employee.user_id:
        notify_users(
            recipients=[exc.employee.user],
            verb=f'Your attendance exception for {exc.event_date} was {decision}',
            actor=deciding_user,
            description=note,
        )
    return exc


def override_attendance_exception(exc, overriding_user, decision, reason):
    """HR or an upstream hierarchy manager force-decides. Does NOT check who
    is allowed to call it — that's the caller's job (a later task's view
    layer checks Employee.is_manager_of / HR role flags before calling this),
    same separation of concerns as override_finalize in
    hr/leave_approval_services.py, which doesn't check is_super_admin_user
    itself either."""
    if exc.employee.user_id and exc.employee.user_id == overriding_user.id:
        raise ValueError("You cannot override your own attendance exception request.")
    if decision not in ('approved', 'rejected'):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    if not reason or not reason.strip():
        raise ValueError('An override requires a written reason.')
    if exc.status not in ('pending', 'expired'):
        raise ValueError(f'This request is already {exc.status} and cannot be overridden.')

    now = timezone.now()

    def apply(locked):
        locked.status = decision
        locked.decision_note = reason
        locked.decided_by = overriding_user
        locked.decided_at = now
        locked.is_overridden = True
        locked.overridden_by = overriding_user
        locked.override_reason = reason

    _finalize_attendance_exception(
        exc, allowed_statuses={'pending', 'expired'}, apply=apply,
        race_message=lambda current_status: f'This request is already {current_status} and cannot be overridden.',
    )

    if exc.employee.user_id:
        notify_users(
            recipients=[exc.employee.user],
            verb=f'Your attendance exception for {exc.event_date} was {decision} (overridden)',
            actor=overriding_user,
            description=reason,
        )
    return exc


def _finalize_attendance_exception(exc, allowed_statuses, apply, race_message):
    """Shared locking core for decide_attendance_exception and
    override_attendance_exception. Mirrors leave_approval_services._finalize's
    concurrency guard: lock the row, re-check its status is still one of the
    caller's allowed starting states (a concurrent decide/override call could
    have moved it since the caller's own pre-lock check), apply the
    caller-supplied field mutations, save, and derive the day's
    AttendanceRecord outcome — all inside one transaction so a manager
    deciding and an HR override can never both apply an outcome for the same
    request.

    `apply(locked)` sets the decision fields (status/decision_note/decided_by/
    decided_at/override bookkeeping) on the freshly-locked row.
    `race_message(current_status)` builds the ValueError message to raise if
    the row is no longer in an allowed starting status by the time the lock
    is acquired.

    On success, copies the saved fields back onto the caller's `exc` instance
    so callers can rely on it (and any code afterwards, e.g. notifications)
    reflecting the persisted state without a manual refresh_from_db().
    """
    with transaction.atomic():
        locked = AttendanceException.objects.select_for_update().get(pk=exc.pk)
        if locked.status not in allowed_statuses:
            raise ValueError(race_message(locked.status))
        apply(locked)
        locked.save()
        _apply_attendance_outcome(locked)

    for field in ('status', 'decision_note', 'decided_by', 'decided_by_id', 'decided_at',
                  'is_overridden', 'overridden_by', 'overridden_by_id',
                  'override_reason'):
        if hasattr(locked, field):
            setattr(exc, field, getattr(locked, field))
    return exc


def _apply_attendance_outcome(exc):
    """Upsert the AttendanceRecord for (exc.employee, exc.event_date) based on
    exc's current status. Called only from inside
    _finalize_attendance_exception's transaction.atomic()/select_for_update()
    block (decide_/override_/expire_ paths), so the row is already locked and
    this is safe from races."""
    if exc.status == 'approved':
        # Excused — the Wi-Fi/manual attendance-status derivation layer
        # (attendance.services.sync_hr_attendance / hr.attendance_services.
        # derive_status) is what actually determines late-vs-not from real
        # check-in time when an approved exception exists for that day; this
        # upsert just marks the day as excused/present at decision time.
        new_status = 'present'
    elif exc.status in ('rejected', 'expired', 'revoked'):
        new_status = 'absent'
    else:  # pending — no-op
        return

    AttendanceRecord.objects.update_or_create(
        employee=exc.employee, date=exc.event_date,
        defaults={
            'status': new_status,
            'source': 'manual',
            'note': f'Attendance exception #{exc.pk} ({exc.get_status_display()})',
        },
    )


def expire_overdue_exceptions():
    """Cron-driven sweep (the cron command itself is a separate task): finds
    all pending AttendanceException rows past their 24h decision deadline,
    expires them, marks the day absent, and notifies both the employee and
    the manager that it went unactioned. Returns the count expired."""
    now = timezone.now()
    count = 0
    for exc in AttendanceException.objects.filter(status='pending'):
        if not exc.is_overdue(now=now):
            continue

        with transaction.atomic():
            locked = AttendanceException.objects.select_for_update().get(pk=exc.pk)
            if locked.status != 'pending':
                continue  # decided/overridden concurrently — nothing to expire
            locked.status = 'expired'
            locked.decided_at = now
            locked.save()
            _apply_attendance_outcome(locked)

        recipients = []
        if exc.employee.user_id:
            recipients.append(exc.employee.user)
        if exc.main_manager and exc.main_manager.user_id:
            recipients.append(exc.main_manager.user)
        if recipients:
            notify_users(
                recipients=recipients,
                verb=f'Attendance exception for {exc.event_date} expired unactioned — day marked absent',
                description=f'{exc.employee.full_name} — {exc.get_reason_category_display()}',
            )
        count += 1
    return count


def send_pending_start_reminders():
    """Cron-driven sweep (same cron command as expire_overdue_exceptions,
    run first): for every pending request whose event has started but whose
    manager hasn't been reminded yet, notify the manager that the 24h
    decision clock has started, then stamp reminder_sent_at so a later run
    never re-sends it.

    Cron runs periodically (not continuously), so "has started" is checked
    as `event_start <= now` rather than any tighter window — a delayed or
    missed cron tick still catches up and sends the (slightly late)
    reminder next time, rather than silently skipping it forever.

    Marks reminder_sent_at even when the manager has no linked login: there
    is nothing to notify, but the row still shouldn't be re-evaluated on
    every subsequent tick indefinitely. Returns the count processed
    (notified or not).
    """
    now = timezone.now()
    count = 0
    for exc in AttendanceException.objects.filter(status='pending', reminder_sent_at__isnull=True):
        if exc.event_start is None or now < exc.event_start:
            continue

        if exc.main_manager and exc.main_manager.user_id:
            notify_users(
                recipients=[exc.main_manager.user],
                verb=f"{exc.employee.full_name}'s attendance exception event has started — "
                     f"you have 24h to decide before the day is marked absent",
                description=f'{exc.get_reason_category_display()} on {exc.event_date}',
            )
        exc.reminder_sent_at = now
        exc.save(update_fields=['reminder_sent_at'])
        count += 1
    return count


def edit_attendance_exception(exc, editing_user, *, event_date, event_start_time, reason_category,
                              custom_reason='', employee_comment=''):
    """The creator edits their own still-pending, undecided exception in
    place. Assumes the caller (the view, via AttendanceExceptionForm) has
    already validated the fields — same trust level as
    submit_attendance_exception, which also doesn't re-validate
    reason_category/custom_reason itself."""
    if exc.created_by_id != editing_user.id:
        raise ValueError('Only the person who submitted this request can edit it.')
    exc.refresh_from_db()
    if exc.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be edited.')
    if AttendanceException.objects.filter(
            employee=exc.employee, event_date=event_date, event_start_time=event_start_time,
            status__in=('pending', 'expired')).exclude(pk=exc.pk).exists():
        raise ValueError(
            'An attendance exception for this exact event has already been submitted and is still awaiting a decision.')

    exc.event_date = event_date
    exc.event_start_time = event_start_time
    exc.reason_category = reason_category
    exc.custom_reason = custom_reason
    exc.employee_comment = employee_comment
    exc.save(update_fields=[
        'event_date', 'event_start_time', 'reason_category', 'custom_reason', 'employee_comment', 'updated_at'])
    return exc


def delete_attendance_exception(exc, deleting_user):
    """The creator withdraws their own still-pending, undecided exception."""
    if exc.created_by_id != deleting_user.id:
        raise ValueError('Only the person who submitted this request can delete it.')
    exc.refresh_from_db()
    if exc.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be deleted.')
    exc.delete()


def revoke_attendance_exception(exc, revoking_user, reason):
    """Direct revoke by someone with override access or upstream hierarchy
    authority — does NOT check who is allowed to call it (caller's job, same
    separation of concerns as override_attendance_exception). No linked
    record to delete; the read path (_apply_attendance_outcome) already
    treats 'revoked' as not-excused."""
    if exc.employee.user_id and exc.employee.user_id == revoking_user.id:
        raise ValueError('You cannot revoke your own attendance exception.')
    if not reason or not reason.strip():
        raise ValueError('A revoke requires a written reason.')
    exc.refresh_from_db()
    if exc.status != 'approved':
        raise ValueError(f'This request is {exc.status}; only an approved request can be revoked.')

    with transaction.atomic():
        locked = AttendanceException.objects.select_for_update().get(pk=exc.pk)
        if locked.status != 'approved':
            raise ValueError(f'This request is {locked.status}; only an approved request can be revoked.')
        locked.status = 'revoked'
        locked.revoked_by = revoking_user
        locked.revoked_at = timezone.now()
        locked.revoke_reason = reason.strip()
        locked.save()
        _apply_attendance_outcome(locked)
        AttendanceExceptionRevokeRequest.objects.filter(attendance_exception=locked, status='pending').update(
            status='approved', decided_by=revoking_user, decided_at=timezone.now(),
            decision_note='Applied via a direct revoke before this request was reviewed.')

    for field in ('status', 'revoked_by', 'revoked_by_id', 'revoked_at', 'revoke_reason'):
        if hasattr(locked, field):
            setattr(exc, field, getattr(locked, field))
    if exc.employee.user_id:
        notify_users(
            recipients=[exc.employee.user],
            verb=f'Your approved attendance exception for {exc.event_date} was revoked',
            actor=revoking_user, description=reason.strip())
    return exc


def request_attendance_exception_revoke(exc, requesting_user, reason):
    """The employee themselves requests to void their own approved
    exception."""
    if not (exc.employee.user_id and exc.employee.user_id == requesting_user.id):
        raise ValueError('Only the employee themselves can request a revoke of their own attendance exception.')
    if not reason or not reason.strip():
        raise ValueError('A reason is required to request a revoke.')
    exc.refresh_from_db()
    if exc.status != 'approved':
        raise ValueError(f'This request is {exc.status}; only an approved request can have its revoke requested.')
    if AttendanceExceptionRevokeRequest.objects.filter(attendance_exception=exc, status='pending').exists():
        raise ValueError('A revoke request for this exception is already pending review.')

    revoke_request = AttendanceExceptionRevokeRequest.objects.create(
        attendance_exception=exc, requested_by=requesting_user, reason=reason.strip())
    if exc.main_manager and exc.main_manager.user_id:
        notify_users(
            recipients=[exc.main_manager.user],
            verb=f'{exc.employee.full_name} requested to revoke an approved attendance exception',
            actor=requesting_user, description=reason.strip())
    return revoke_request


def decide_attendance_exception_revoke_request(revoke_request, deciding_user, decision, decision_note=''):
    """The assigned manager, or an override-access/upstream-hierarchy
    holder, approves or rejects an employee's revoke request — mirrors
    can_decide_attendance_exception's eligibility (imported locally to
    avoid a module-load-time circular import between this services module
    and hr.views, matching this file's existing local-import style)."""
    from hr.views import can_decide_attendance_exception

    if decision not in ('approved', 'rejected'):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    revoke_request.refresh_from_db()
    if revoke_request.status != 'pending':
        raise ValueError(f'This revoke request is already {revoke_request.status}.')
    exc = revoke_request.attendance_exception
    if exc.employee.user_id and exc.employee.user_id == deciding_user.id:
        raise ValueError('You cannot decide a revoke request on your own attendance exception.')
    if not can_decide_attendance_exception(deciding_user, exc):
        raise ValueError('You are not authorized to decide this revoke request.')
    if decision == 'rejected' and not (decision_note or '').strip():
        raise ValueError('Rejecting a revoke request requires a note explaining why.')

    with transaction.atomic():
        revoke_request.status = decision
        revoke_request.decided_by = deciding_user
        revoke_request.decided_at = timezone.now()
        revoke_request.decision_note = decision_note.strip()
        revoke_request.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        if decision == 'approved':
            revoke_attendance_exception(exc, deciding_user, revoke_request.reason)
        else:
            if exc.employee.user_id:
                notify_users(
                    recipients=[exc.employee.user],
                    verb='Your revoke request for an attendance exception was rejected',
                    actor=deciding_user, description=decision_note.strip())
    return revoke_request
