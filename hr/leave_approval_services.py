"""Finalization logic for the conditional-leave dual-approval workflow.

Two entry points:
- record_approver_decision: a designated LeaveApprover records their own decision.
- override_finalize: any super admin force-finalizes a stuck request (deadlock breaker).

Both funnel through _finalize, which is the single place that creates the
balance-deducting LeaveRecord or sets the salary-deduction flag.
"""
from django.utils import timezone

from hr.models import LeaveRecord
from notifications.services import notify_users


def record_approver_decision(leave_request, approver_user, decision, comment=''):
    if decision not in ('approved', 'disapproved'):
        raise ValueError(f"decision must be 'approved' or 'disapproved', got {decision!r}")
    try:
        approval = leave_request.approvals.get(approver=approver_user)
    except leave_request.approvals.model.DoesNotExist:
        raise ValueError(f"{approver_user} is not a designated approver for this request.")
    if approval.decision != 'pending':
        raise ValueError(f"{approver_user} has already decided ({approval.decision}).")

    approval.decision = decision
    approval.comment = comment
    approval.decided_at = timezone.now()
    approval.save(update_fields=['decision', 'comment', 'decided_at'])

    _reconcile(leave_request)
    leave_request.refresh_from_db()
    if leave_request.status == 'pending':
        remaining = leave_request.pending_approvers()
        if remaining:
            notify_users(
                recipients=remaining,
                verb=f'{approver_user.get_full_name() or approver_user.username} decided on a leave request awaiting your review',
                actor=approver_user,
                description=f'{leave_request.employee.full_name} — {leave_request.leave_type.name} '
                            f'({leave_request.start_date} to {leave_request.end_date})',
            )
    return leave_request


def override_finalize(leave_request, superadmin_user, decision, reason):
    if decision not in ('approved', 'disapproved'):
        raise ValueError(f"decision must be 'approved' or 'disapproved', got {decision!r}")
    if not reason or not reason.strip():
        raise ValueError('An override requires a written reason.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError(f"Request is already {leave_request.status}; nothing to override.")

    leave_request.approvals.filter(decision='pending').update(decision='skipped', decided_at=timezone.now())
    leave_request.is_overridden = True
    leave_request.overridden_by = superadmin_user
    leave_request.override_reason = reason
    _finalize(leave_request, decision)
    return leave_request


def _reconcile(leave_request):
    """Re-derive overall status from the individual approval rows."""
    leave_request.refresh_from_db()
    decisions = list(leave_request.approvals.values_list('decision', flat=True))
    if any(d == 'disapproved' for d in decisions):
        _finalize(leave_request, 'disapproved')
    elif decisions and all(d == 'approved' for d in decisions):
        _finalize(leave_request, 'approved')
    # else: still pending, nothing to do.


def _finalize(leave_request, status):
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
