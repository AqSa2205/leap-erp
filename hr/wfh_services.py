"""Employees marking themselves as working from home.

Separate from the HR grid's WFH checkbox because the two are not the same act.
HR ticking a box on the register is a record being corrected by someone with
the standing to correct it; an employee declaring their own remote day is a
statement about a day that has not been assessed yet, and the rules below are
what keep the second from being able to rewrite the first.
"""

from datetime import timedelta

# A typo in a date field should not be able to declare a year of remote work.
MAX_WFH_RANGE_DAYS = 31


class WFHError(ValueError):
    """Something about the request means it cannot be recorded. The message is
    written to be shown to the employee."""


def _working_days_in(start, end):
    """The days in the range that are actually working days, in order."""
    from hr.models import AttendanceSettings, Holiday, WorkingDay
    weekend_days = AttendanceSettings.load().weekend_day_set()
    holidays = set(Holiday.objects.filter(
        is_active=True, date__range=(start, end)).values_list('date', flat=True))
    overrides = set(WorkingDay.objects.filter(
        is_active=True, date__range=(start, end)).values_list('date', flat=True))
    out = []
    d = start
    while d <= end:
        is_off = (d.weekday() in weekend_days or d in holidays) and d not in overrides
        if not is_off:
            out.append(d)
        d += timedelta(days=1)
    return out


def mark_self_wfh(*, employee, start_date, end_date=None, note='', created_by,
                  today=None):
    """Record an employee's own work-from-home days. Returns the WFHRecord.

    Raises WFHError, with a message meant for the employee, rather than
    recording something misleading.

    The one rule worth explaining: this cannot be backdated. derive_status()
    checks for a WFH record BEFORE it looks at check_in, so a WFH day is never
    late and never absent. A self-declaration against a past date would
    therefore erase a Late or Absent that has already been assessed, with
    nobody reviewing the change - the employee could clear their own record at
    will. Today and future days have not been judged yet, so declaring them is
    a statement of intent rather than a rewrite of history. A past day that
    genuinely needs correcting already has a reviewed path: raise an
    Attendance Exception, or query the Late mark.
    """
    from django.utils import timezone
    from hr.models import LeaveRecord, WFHRecord

    today = today or timezone.localdate()
    end_date = end_date or start_date

    if end_date < start_date:
        raise WFHError('The end date cannot be before the start date.')
    if start_date < today:
        raise WFHError(
            'Work from home can only be marked for today or a future date. '
            'To correct a day that has already passed, raise an attendance '
            'exception or query the late mark instead.')
    span = (end_date - start_date).days + 1
    if span > MAX_WFH_RANGE_DAYS:
        raise WFHError(
            f'Work from home can be marked for at most {MAX_WFH_RANGE_DAYS} '
            'days at a time.')

    working = _working_days_in(start_date, end_date)
    if not working:
        raise WFHError(
            'Those dates are all weekend or holiday, so there is nothing to '
            'mark.')

    # Leave outranks WFH in derive_status(), so a record covering a leave day
    # would sit there doing nothing. Say so rather than accept it silently.
    if LeaveRecord.objects.filter(
            employee=employee, start_date__lte=end_date,
            end_date__gte=start_date).exists():
        raise WFHError(
            'You already have leave booked in that range. Cancel the leave '
            'first if you mean to work from home instead.')

    if WFHRecord.objects.filter(
            employee=employee, start_date__lte=end_date,
            end_date__gte=start_date).exists():
        raise WFHError('Those days are already marked as work from home.')

    record = WFHRecord.objects.create(
        employee=employee, start_date=start_date, end_date=end_date,
        note=note or '', created_by=created_by)
    _resync(employee, working, today)
    return record


def cancel_self_wfh(*, record, requested_by, today=None):
    """Withdraw a self-marked WFH period the employee has not finished yet.

    Only the person who marked it can withdraw it, and only while it still has
    a day left to run - a period that has already passed is a record of what
    happened, not a plan, and letting somebody delete it would put the same
    hole in the register that backdating would.
    """
    from django.utils import timezone
    today = today or timezone.localdate()

    if record.created_by_id != requested_by.pk:
        raise WFHError('Only the person who marked this can withdraw it.')
    if record.end_date < today:
        raise WFHError(
            'That period has already passed and is part of your attendance '
            'record. Ask HR if it needs correcting.')

    employee = record.employee
    affected = _working_days_in(record.start_date, record.end_date)
    record.delete()
    _resync(employee, affected, today)


def _resync(employee, days, today):
    """Re-derive the days that already have a stored status.

    Only days up to today: re-deriving a future day would write an 'absent'
    record for a date nobody could have attended yet, and the register would
    show a wall of absences for a fortnight of planned remote work.
    """
    from hr.attendance_services import regenerate_attendance_record
    from hr.models import AttendanceRecord

    for d in days:
        if d > today:
            continue
        if d < today and not AttendanceRecord.objects.filter(
                employee=employee, date=d).exists():
            # Nothing was recorded for that past day; leave it alone rather
            # than inventing a row.
            continue
        regenerate_attendance_record(employee, d)
