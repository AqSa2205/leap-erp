"""Sidebar pending-count badges — Leave Requests, Team Exceptions and the
Pending Approvals inbox.
Reuses the exact querysets the real pages themselves filter by, so a badge
number can never drift from what the page shows when you open it."""
from .models import LeaveRequest, AttendanceException


def pending_counts(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    from .views import can_view_leave_dashboard, can_view_team_exceptions

    ctx = {}
    if can_view_leave_dashboard(user):
        from .models import LeaveRevokeRequest
        ctx['leave_requests_pending_count'] = (
            LeaveRequest.objects.filter(status='pending').count()
            + LeaveRevokeRequest.objects.filter(status='pending').count())

    # The Pending Approvals inbox. Built by the same function the page uses,
    # for the same reason as everything else here - a badge that counted
    # differently from the page would send people looking for work that is not
    # there. The sources it walks are already narrowed to unreleased or
    # undecided rows, so this stays a handful of indexed lookups.
    from .approvals import pending_approvals_count
    try:
        ctx['my_approvals_count'] = pending_approvals_count(user)
    except Exception:      # noqa: BLE001 - a badge must never break every page
        ctx['my_approvals_count'] = 0

    if can_view_team_exceptions(user):
        from .models import AttendanceExceptionRevokeRequest
        emp = getattr(user, 'employee_profile', None)
        is_hr = bool(user.is_super_admin_user or user.is_admin_user)
        active_qs = AttendanceException.objects.filter(status__in=('pending', 'expired'))
        revoke_qs = AttendanceExceptionRevokeRequest.objects.filter(status='pending')
        if emp:
            direct = active_qs.filter(employee__main_manager=emp).exclude(employee__user=user)
            secondary = active_qs.filter(employee__secondary_managers=emp).exclude(employee__user=user)
            direct_revokes = revoke_qs.filter(attendance_exception__employee__main_manager=emp)
            secondary_revokes = revoke_qs.filter(attendance_exception__employee__secondary_managers=emp)
        else:
            direct = active_qs.none()
            secondary = active_qs.none()
            direct_revokes = revoke_qs.none()
            secondary_revokes = revoke_qs.none()
        ctx['team_exceptions_direct_count'] = direct.count() + direct_revokes.count()
        ctx['team_exceptions_secondary_count'] = secondary.count() + secondary_revokes.count()
        total = ctx['team_exceptions_direct_count'] + ctx['team_exceptions_secondary_count']
        if is_hr:
            # HR's org-wide reach is the broadest view they have of this
            # queue — the dot reflects that total, not just their own
            # direct/secondary reports.
            ctx['team_exceptions_all_count'] = active_qs.count() + revoke_qs.count()
            total = ctx['team_exceptions_all_count']
        ctx['team_exceptions_pending_count'] = total

    return ctx
