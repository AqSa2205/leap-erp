"""Finalization logic for the dual-approval leave workflow — every leave
type, including Annual, routes through this queue; there is no
approval-bypass path.

Two entry points:
- record_approver_decision: a user with an active LeaveDashboardAccess grant
  records their own decision.
- override_finalize: any super admin force-finalizes a stuck request (deadlock breaker).

Both funnel through _finalize, which is the single place that creates the
balance-deducting LeaveRecord or sets the salary-deduction flag.

Reconciliation requires UNANIMOUS agreement in either direction: the request
only finalizes once every designated approver has decided AND all of their
decisions agree (all-approved -> approved, all-disapproved -> disapproved).
A split decision (some approve, some disapprove) leaves the request pending
indefinitely rather than fail-fast finalizing on the first disapproval —
each approver can keep changing their own decision (see
edit_approver_decision) for as long as the request stays pending; there is
no fixed time limit, only whichever happens first: unanimous agreement, or
someone changing their mind to match the others.
"""
from django.db import transaction
from django.utils import timezone

from hr.models import LeaveRecord, LeaveRequest, LeaveRevokeRequest
from notifications.services import notify_users


def record_approver_decision(leave_request, approver_user, decision, comment=''):
    if decision not in ('approved', 'disapproved'):
        raise ValueError(f"decision must be 'approved' or 'disapproved', got {decision!r}")
    if leave_request.employee.user_id and leave_request.employee.user_id == approver_user.id:
        # Defense in depth: submit_leave_request already excludes the
        # requester from the approver roster for every NEW request, but this
        # blocks any legacy/edge-case row where a self-approval slipped
        # through — a user must never be able to decide their own request.
        raise ValueError('You cannot approve or disapprove your own leave request.')
    try:
        approval = leave_request.approvals.get(approver=approver_user)
    except leave_request.approvals.model.DoesNotExist:
        raise ValueError(f"{approver_user} is not a designated approver for this request.")
    if approval.decision != 'pending':
        raise ValueError(f"{approver_user} has already decided ({approval.decision}).")

    # Recording the decision and reconciling/finalizing must succeed or fail
    # together — otherwise a transient failure inside _finalize (e.g. the
    # LeaveRecord write) leaves this approval permanently stuck showing
    # 'approved'/'disapproved' while the request itself never leaves
    # 'pending', and record_approver_decision can never be called again for
    # it (it requires decision == 'pending').
    with transaction.atomic():
        approval.decision = decision
        approval.comment = comment
        approval.decided_at = timezone.now()
        approval.save(update_fields=['decision', 'comment', 'decided_at'])

        if comment and comment.strip():
            # A comment typed alongside the decision IS the message to the
            # employee — mirrored onto a LeaveRequestNote (is_internal=False
            # by default) so it actually reaches My Profile, the only page a
            # plain employee can view their own request from. Previously
            # this text was saved only on the LeaveRequestApproval row,
            # which nothing employee-facing ever reads — a decision comment
            # was effectively invisible to the person it was about.
            from hr.models import LeaveRequestNote
            LeaveRequestNote.objects.create(
                leave_request=leave_request, author=approver_user, note=comment.strip())

        _reconcile(leave_request)
    leave_request.refresh_from_db()
    _notify_after_reconcile(leave_request, actor=approver_user)
    return leave_request


def edit_approver_decision(leave_request, approver_user, new_decision, edit_note):
    """Let an approver change their own already-recorded decision for as
    long as the overall request is still pending (see _finalize — once
    finalized, decisions lock; there's no reversal of a created LeaveRecord
    or a salary-deduction flag). No fixed time limit — the request stays
    pending, and therefore editable, until every approver agrees one way or
    the other. Requires a note explaining why, which is recorded as a
    visible LeaveRequestNote."""
    from hr.models import LeaveRequestNote

    if new_decision not in ('approved', 'disapproved'):
        raise ValueError(f"decision must be 'approved' or 'disapproved', got {new_decision!r}")
    if not edit_note or not edit_note.strip():
        raise ValueError('Changing a decision requires a note explaining why.')
    if leave_request.employee.user_id and leave_request.employee.user_id == approver_user.id:
        raise ValueError('You cannot edit a decision on your own leave request.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError('This request has already been finalized; the decision can no longer be edited.')
    try:
        approval = leave_request.approvals.get(approver=approver_user)
    except leave_request.approvals.model.DoesNotExist:
        raise ValueError(f"{approver_user} is not a designated approver for this request.")
    if approval.decision not in ('approved', 'disapproved'):
        raise ValueError('No prior decision to edit.')

    old_decision = approval.decision
    if old_decision == new_decision:
        raise ValueError('That is already your recorded decision.')

    # Same all-or-nothing reasoning as record_approver_decision: the edited
    # decision, its note, and finalization must roll back together on failure.
    with transaction.atomic():
        approval.decision = new_decision
        approval.decided_at = timezone.now()
        approval.save(update_fields=['decision', 'decided_at'])

        LeaveRequestNote.objects.create(
            leave_request=leave_request, author=approver_user,
            note=f'Changed decision from {old_decision} to {new_decision}: {edit_note.strip()}',
        )
        _reconcile(leave_request)
    leave_request.refresh_from_db()
    _notify_after_reconcile(leave_request, actor=approver_user)
    return leave_request


def _notify_after_reconcile(leave_request, actor):
    """Shared post-decision notification logic for record_approver_decision
    and edit_approver_decision — called after _reconcile, once the request's
    current status reflects the just-recorded/edited decision. Only ever
    notifies while the request is still 'pending' (a finalized request's own
    approve/disapprove notification is sent separately, from _finalize)."""
    if leave_request.status != 'pending':
        return
    remaining = leave_request.pending_approvers()
    if remaining:
        notify_users(
            recipients=remaining,
            verb=f'{actor.get_full_name() or actor.username} decided on a leave request awaiting your review',
            actor=actor,
            description=f'{leave_request.employee.full_name} — {leave_request.leave_type.name} '
                        f'({leave_request.start_date} to {leave_request.end_date})',
        )
        return
    # Every approver has now decided, but they disagree — nobody
    # auto-finalizes a split decision. Notify the OTHER already-decided
    # approver(s) that there's a conflict to resolve, since they'd
    # otherwise have no way to know their vote is being contested.
    others = [
        a.approver for a in leave_request.approvals.exclude(approver=actor).exclude(decision='pending')]
    if others:
        notify_users(
            recipients=others,
            verb=f'{actor.get_full_name() or actor.username} disagreed on a leave request — please reconsider your decision',
            actor=actor,
            description=f'{leave_request.employee.full_name} — {leave_request.leave_type.name} '
                        f'({leave_request.start_date} to {leave_request.end_date})',
        )


def override_finalize(leave_request, superadmin_user, decision, reason):
    if decision not in ('approved', 'disapproved'):
        raise ValueError(f"decision must be 'approved' or 'disapproved', got {decision!r}")
    if not reason or not reason.strip():
        raise ValueError('An override requires a written reason.')
    if leave_request.employee.user_id and leave_request.employee.user_id == superadmin_user.id:
        raise ValueError('You cannot override your own leave request.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError(f"Request is already {leave_request.status}; nothing to override.")

    leave_request.is_overridden = True
    leave_request.overridden_by = superadmin_user
    leave_request.override_reason = reason
    _finalize(leave_request, decision)
    return leave_request


def _reconcile(leave_request):
    """Re-derive overall status from the individual approval rows. Finalizes
    only once every approver has decided AND all decisions agree — see the
    module docstring for why a split decision does not fail-fast finalize."""
    leave_request.refresh_from_db()
    decisions = list(leave_request.approvals.values_list('decision', flat=True))
    if not decisions or any(d == 'pending' for d in decisions):
        return  # still waiting on at least one approver
    if all(d == 'approved' for d in decisions):
        _finalize(leave_request, 'approved')
    elif all(d == 'disapproved' for d in decisions):
        _finalize(leave_request, 'disapproved')
    # else: split decision — stays pending, unresolved until someone changes
    # their vote to match consensus.


def _finalize(leave_request, status):
    from hr.models import LeaveRequest  # local import avoids any circularity, matches this file's existing style

    with transaction.atomic():
        current = LeaveRequest.objects.select_for_update().get(pk=leave_request.pk)
        if current.status != 'pending':
            return  # already finalized by a concurrent decision/override — no-op

        leave_request.approvals.filter(decision='pending').update(decision='skipped', decided_at=timezone.now())
        leave_request.status = status
        leave_request.decided_at = timezone.now()
        if status == 'approved':
            record = LeaveRecord.objects.create(
                employee=leave_request.employee,
                leave_type=leave_request.leave_type,
                start_date=leave_request.start_date,
                end_date=leave_request.end_date,
                days=leave_request.days,
                note=f'Approved via leave request #{leave_request.pk}',
            )
            leave_request.leave_record = record
        elif status == 'disapproved':
            leave_request.salary_deduction_applicable = True
        leave_request.save(update_fields=[
            'status', 'decided_at', 'leave_record', 'salary_deduction_applicable',
            'is_overridden', 'overridden_by', 'override_reason',
        ])
    if leave_request.employee.user_id:
        verb = 'approved' if status == 'approved' else 'disapproved'
        notify_users(
            recipients=[leave_request.employee.user],
            verb=f'Your {leave_request.leave_type.name} leave request was {verb}',
            description=leave_request.override_reason if leave_request.is_overridden else '',
        )


def submit_leave_request(*, employee, leave_type, start_date, end_date, employee_reason='', document=None, created_by):
    """Create a LeaveRequest and snapshot the currently-active
    LeaveDashboardAccess roster onto it as LeaveRequestApproval rows — the
    single entry point for ALL leave creation (admin-logged, legacy
    'Add Leave', or self-service), including Annual, which used to skip
    approval entirely (that bypass was removed: every leave type now needs
    Approve/Disapprove). Used by both the employee self-service form and the
    admin's 'log on behalf of' form so the two submission paths can never
    drift out of sync.

    Re-validates balance/overlap under a row lock (see
    hr.leave_services.validate_leave_submission) immediately before
    creating the row — LeaveRequestForm already ran the same check
    unlocked for fast UX feedback, but only this locked, in-transaction
    check is race-safe against two genuinely concurrent submissions for the
    same employee/leave_type/year. Raises ValueError if it fails here (a
    rare case in normal use — it means something changed between the form's
    check and this call, e.g. a concurrent submission).

    The employee's own login (if they have one) is always excluded from the
    approver roster for their own request — an approver submitting leave
    for themselves must never end up as one of their own approvers; the
    remaining co-approver(s) take on approval authority for this request
    automatically, with no extra configuration needed."""
    from hr.leave_services import validate_leave_submission
    from hr.models import LeaveDashboardAccess, LeaveRequest, LeaveRequestApproval

    with transaction.atomic():
        exceeds_balance = validate_leave_submission(employee, leave_type, start_date, end_date, lock=True)
        logged_by_manager = bool(
            created_by is not None and employee.main_manager_id and employee.main_manager.user_id == created_by.id)
        leave_request = LeaveRequest.objects.create(
            employee=employee, leave_type=leave_type, start_date=start_date, end_date=end_date,
            employee_reason=employee_reason, document=document, created_by=created_by,
            exceeds_balance=exceeds_balance, logged_by_manager=logged_by_manager,
        )
        # A held (balance-exceeding) request goes through the exact same
        # approver roster and decide/override flow as any other request —
        # exceeds_balance only drives the warning shown on the request, not
        # who can decide it.
        approvers = LeaveDashboardAccess.objects.filter(is_active=True)
        if employee.user_id:
            approvers = approvers.exclude(user_id=employee.user_id)
        for grant in approvers:
            LeaveRequestApproval.objects.create(leave_request=leave_request, approver=grant.user)

    if employee.user_id:
        from notifications.services import notify_users
        verb = ('Your leave request was submitted — it exceeds your available balance, so it may need extra '
                'review' if exceeds_balance else
                'Your leave request was submitted and is pending approval')
        notify_users(recipients=[employee.user], verb=verb, actor=created_by)
    return leave_request


def grant_exception_days(*, employee, leave_type, year, days, granted_by, reason):
    """HR-granted addition to an employee's standard entitlement for a
    year — creates one audited LeaveExceptionGrant row. Immediately
    reflected in LeaveEntitlement.exception_days/effective_remaining_days
    and usable by the employee's own future self-service submissions."""
    if not reason or not reason.strip():
        raise ValueError('An exception grant requires a written reason.')
    from hr.models import LeaveExceptionGrant
    return LeaveExceptionGrant.objects.create(
        employee=employee, leave_type=leave_type, year=year, days=days,
        granted_by=granted_by, reason=reason.strip())


def edit_leave_request(leave_request, editing_user, *, leave_type, start_date, end_date,
                       employee_reason='', document=None):
    """The creator edits their own still-pending, undecided request in
    place. Re-runs the exact same balance/overlap validation a fresh
    submission would (via exclude_request_id, so the request doesn't
    collide with its own unmodified row) — an edit is not exempt from the
    rules that applied to the original submission, and could newly become
    exceeds_balance if the new dates push it over the balance-hold
    threshold, exactly like any other submission.

    Does NOT reset the approver roster — the same LeaveDashboardAccess
    snapshot taken at original submission stays; this is safe because the
    lock condition below guarantees nobody has decided yet, so there's
    nothing to invalidate."""
    from hr.leave_services import validate_leave_submission

    if leave_request.created_by_id != editing_user.id:
        raise ValueError('Only the person who submitted this request can edit it.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be edited.')
    if leave_request.approvals.exclude(decision='pending').exists():
        raise ValueError('An approver has already recorded a decision on this request; it can no longer be edited.')

    with transaction.atomic():
        exceeds_balance = validate_leave_submission(
            leave_request.employee, leave_type, start_date, end_date, lock=True,
            exclude_request_id=leave_request.pk)
        leave_request.leave_type = leave_type
        leave_request.start_date = start_date
        leave_request.end_date = end_date
        leave_request.employee_reason = employee_reason
        if document is not None:
            leave_request.document = document
        leave_request.exceeds_balance = exceeds_balance
        leave_request.days = leave_request.computed_days()
        leave_request.save(update_fields=[
            'leave_type', 'start_date', 'end_date', 'employee_reason', 'document',
            'exceeds_balance', 'days', 'updated_at'])
    return leave_request


def delete_leave_request(leave_request, deleting_user):
    """The creator withdraws their own still-pending, undecided request.
    No LeaveRecord exists yet at this stage (only created on approval —
    see _finalize), so there's nothing else to clean up; approvals cascade-
    delete with the row."""
    if leave_request.created_by_id != deleting_user.id:
        raise ValueError('Only the person who submitted this request can delete it.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be deleted.')
    if leave_request.approvals.exclude(decision='pending').exists():
        raise ValueError('An approver has already recorded a decision on this request; it can no longer be deleted.')
    leave_request.delete()


def revoke_leave_request(leave_request, revoking_user, reason):
    """Direct revoke by someone with override access — does NOT check
    has_override_access itself (that's the caller's/view's job, same
    separation of concerns as override_finalize). Deletes the linked
    LeaveRecord so taken_days/remaining_days recompute live; the
    LeaveRequest row stays, re-labeled status='revoked'."""
    if leave_request.employee.user_id and leave_request.employee.user_id == revoking_user.id:
        raise ValueError('You cannot revoke your own leave request.')
    if not reason or not reason.strip():
        raise ValueError('A revoke requires a written reason.')
    leave_request.refresh_from_db()
    if leave_request.status != 'approved':
        raise ValueError(f'This request is {leave_request.status}; only an approved request can be revoked.')

    with transaction.atomic():
        current = LeaveRequest.objects.select_for_update().get(pk=leave_request.pk)
        if current.status != 'approved':
            raise ValueError(f'This request is {current.status}; only an approved request can be revoked.')
        if current.leave_record_id:
            LeaveRecord.objects.filter(pk=current.leave_record_id).delete()
        current.status = 'revoked'
        current.revoked_by = revoking_user
        current.revoked_at = timezone.now()
        current.revoke_reason = reason.strip()
        current.leave_record = None
        current.save(update_fields=['status', 'revoked_by', 'revoked_at', 'revoke_reason', 'leave_record'])
        # Auto-close any pending employee-initiated revoke request for the
        # same leave rather than leaving it dangling — a direct revoke by
        # someone with override access always wins immediately.
        LeaveRevokeRequest.objects.filter(leave_request=current, status='pending').update(
            status='approved', decided_by=revoking_user, decided_at=timezone.now(),
            decision_note='Applied via a direct revoke before this request was reviewed.')
    if leave_request.employee.user_id:
        notify_users(
            recipients=[leave_request.employee.user],
            verb=f'Your approved {leave_request.leave_type.name} leave was revoked',
            actor=revoking_user, description=reason.strip())
    leave_request.refresh_from_db()
    return leave_request


def request_leave_revoke(leave_request, requesting_user, reason):
    """The employee themselves requests to void their own approved leave.
    HR/Super Admin use revoke_leave_request (direct) instead of this queue —
    requested_by is always the employee, never a manager acting on their
    behalf, even for a request the manager originally logged."""
    if not (leave_request.employee.user_id and leave_request.employee.user_id == requesting_user.id):
        raise ValueError('Only the employee themselves can request a revoke of their own leave.')
    if not reason or not reason.strip():
        raise ValueError('A reason is required to request a revoke.')
    leave_request.refresh_from_db()
    if leave_request.status != 'approved':
        raise ValueError(f'This request is {leave_request.status}; only an approved request can have its revoke requested.')
    if LeaveRevokeRequest.objects.filter(leave_request=leave_request, status='pending').exists():
        raise ValueError('A revoke request for this leave is already pending review.')

    revoke_request = LeaveRevokeRequest.objects.create(
        leave_request=leave_request, requested_by=requesting_user, reason=reason.strip())

    from django.contrib.auth import get_user_model
    from accounts.models import Role
    from hr.models import LeaveDashboardAccess
    User = get_user_model()
    recipients = set(g.user for g in LeaveDashboardAccess.objects.filter(is_active=True))
    recipients |= set(User.objects.filter(role__name=Role.SUPER_ADMIN))
    if recipients:
        notify_users(
            recipients=list(recipients),
            verb=f'{leave_request.employee.full_name} requested to revoke an approved leave',
            actor=requesting_user, description=reason.strip())
    return revoke_request


def decide_leave_revoke_request(revoke_request, deciding_user, decision, decision_note=''):
    """HR/Super Admin (the same roster that decides normal leave requests)
    approves or rejects an employee's revoke request. Approving applies the
    exact same mechanic as a direct revoke (revoke_leave_request) —
    reusing it keeps the LeaveRecord-deletion/notification logic in one
    place."""
    if decision not in ('approved', 'rejected'):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    revoke_request.refresh_from_db()
    if revoke_request.status != 'pending':
        raise ValueError(f'This revoke request is already {revoke_request.status}.')
    leave_request = revoke_request.leave_request
    if leave_request.employee.user_id and leave_request.employee.user_id == deciding_user.id:
        raise ValueError('You cannot decide a revoke request on your own leave.')
    if decision == 'rejected' and not (decision_note or '').strip():
        raise ValueError('Rejecting a revoke request requires a note explaining why.')

    with transaction.atomic():
        revoke_request.status = decision
        revoke_request.decided_by = deciding_user
        revoke_request.decided_at = timezone.now()
        revoke_request.decision_note = decision_note.strip()
        revoke_request.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        if decision == 'approved':
            revoke_leave_request(leave_request, deciding_user, revoke_request.reason)
        else:
            if leave_request.employee.user_id:
                notify_users(
                    recipients=[leave_request.employee.user],
                    verb=f'Your revoke request for a {leave_request.leave_type.name} leave was rejected',
                    actor=deciding_user, description=decision_note.strip())
    return revoke_request
