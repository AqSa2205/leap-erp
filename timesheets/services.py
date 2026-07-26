from django.db import transaction
from django.utils import timezone
from .models import TimesheetRequest, TimesheetRequestAck
from .models import TimesheetEntry, TimesheetMonth
from accounts.models import User, RolePermission
from notifications.services import notify_users

def submit_month(*, employee, year, month, submitted_by):
    """Lock an employee's timesheet for one calendar month.

    Mirrors leave_approval_services.py's shape: this is the one authoritative
    place the lock actually happens — the view calls this rather than
    flipping fields itself, so the same rules apply no matter what UI (or
    future HR bulk-action) triggers a submit.
    """
    entries = TimesheetEntry.objects.filter(
        employee=employee, date__year=year, date__month=month)

    if not entries.exists():
        raise ValueError('There are no entries logged for this month yet — nothing to submit.')

    draft_entries = entries.filter(status=TimesheetEntry.STATUS_DRAFT)

    with transaction.atomic():
        tsm, _ = TimesheetMonth.objects.get_or_create(
            employee=employee, year=year, month=month)

        if tsm.is_submitted:
            raise ValueError('This month is already submitted.')

        draft_entries.update(status=TimesheetEntry.STATUS_SUBMITTED)

        tsm.submitted_at = timezone.now()
        tsm.submitted_by = submitted_by
        tsm.save(update_fields=['submitted_at', 'submitted_by'])

    return tsm


def reopen_month(*, employee, year, month, reopened_by):
    """HR-side: unlock a month so the employee can fix a mistake.

    This flips entries back to draft too — a reopened month with entries
    still marked 'submitted' would mean the employee still couldn't edit
    them, defeating the point of reopening.
    """
    try:
        tsm = TimesheetMonth.objects.get(employee=employee, year=year, month=month)
    except TimesheetMonth.DoesNotExist:
        raise ValueError('This month was never submitted, so there is nothing to reopen.')

    if not tsm.is_submitted:
        raise ValueError('This month is not currently submitted.')

    with transaction.atomic():
        TimesheetEntry.objects.filter(
            employee=employee, date__year=year, date__month=month,
            status=TimesheetEntry.STATUS_SUBMITTED,
        ).update(status=TimesheetEntry.STATUS_DRAFT)

        tsm.reopened_at = timezone.now()
        tsm.reopened_by = reopened_by
        tsm.save(update_fields=['reopened_at', 'reopened_by'])

    return tsm





def _users_with_capability(codename):
    """All active users whose role currently grants `codename`=True.
    Mirrors how has_capability checks it, but as a bulk queryset instead of
    per-user, so we don't run one query per employee when notifying everyone."""
    role_ids = RolePermission.objects.filter(
        codename=codename, allowed=True).values_list('role_id', flat=True)
    return User.objects.filter(role_id__in=role_ids, is_active=True)


def request_timesheets(*, year, month, requested_by):
    """HR asks everyone for their timesheet for a given month. Creates the
    request record and notifies every user who actually has timesheets
    access. Re-using an existing request for the same month instead of
    creating a duplicate — otherwise every re-submit fragments the
    'have I sent this' tracking across multiple rows for the same month
    (an employee who acked the first one still shows as Pending against
    the second)."""
    req, created = TimesheetRequest.objects.get_or_create(
        year=year, month=month,
        defaults={'requested_by': requested_by},
    )

    recipients = _users_with_capability('timesheets.access')
    notify_users(
        recipients,
        verb='requested your timesheet',
        actor=requested_by,
        target=req,
        target_url='/timesheets/',
        description=f'HR has requested your timesheet for {month}/{year}.',
        send_email=False,
    )
    return req

def employees_for_request(ts_request):
    """Every employee who was actually eligible to receive this request
    (i.e. their linked user has timesheets.access), split into sent/pending.
    Returns a list of dicts: {'employee': ..., 'acked': True/False, 'ack': ...}."""
    from hr.models import Employee
    eligible_users = _users_with_capability('timesheets.access')
    employees = Employee.objects.filter(user__in=eligible_users).select_related('user')

    acked_map = {
        ack.employee_id: ack
        for ack in ts_request.acks.select_related('employee')
    }

    rows = []
    for emp in employees:
        ack = acked_map.get(emp.pk)
        rows.append({'employee': emp, 'acked': ack is not None, 'ack': ack})
    rows.sort(key=lambda r: (r['acked'], r['employee'].full_name))
    return rows


def remind_employee(*, ts_request, employee, reminded_by):
    """HR nudges one specific employee who hasn't sent theirs yet."""
    if employee.user is None:
        raise ValueError('This employee has no linked login to notify.')

    already_acked = TimesheetRequestAck.objects.filter(
        request=ts_request, employee=employee).exists()
    if already_acked:
        raise ValueError(f'{employee.full_name} has already sent this timesheet.')

    notify_users(
        [employee.user],
        verb='reminded you to send your timesheet',
        actor=reminded_by,
        target=ts_request,
        target_url='/timesheets/',
        description=f'Reminder: please send your timesheet for {ts_request.month}/{ts_request.year}.',
        send_email=False,
    )