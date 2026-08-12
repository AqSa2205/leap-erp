"""Attendance status derivation: leave > holiday > weekend(unless WorkingDay) > wfh > late/present > absent."""
from decimal import Decimal
from datetime import datetime


def _hours_between(check_in, check_out):
    if not (check_in and check_out):
        return None
    base = datetime(2000, 1, 1)
    delta = datetime.combine(base, check_out) - datetime.combine(base, check_in)
    # Night shifts aren't modelled (the model's clean() treats check_out < check_in
    # as invalid). The grid upserts bypass clean(), so guard here: never store
    # negative hours — they'd silently corrupt monthly totals. Leave hours blank
    # for the admin to correct.
    if delta.total_seconds() < 0:
        return None
    return Decimal(round(delta.total_seconds() / 3600, 2))


def derive_status(employee, d, check_in, check_out=None):
    """Return (status, hours_worked).

    Precedence: leave > holiday > weekend(unless WorkingDay) > wfh >
    approved attendance exception > late/present > absent.
    """
    from hr.models import (LeaveRecord, Holiday, AttendanceSettings, WorkingDay, WFHRecord,
                            AttendanceException, LateQuery)
    if LeaveRecord.objects.filter(employee=employee, start_date__lte=d, end_date__gte=d).exists():
        return 'leave', None
    if Holiday.objects.filter(date=d, is_active=True).exists():
        return 'holiday', None
    settings = AttendanceSettings.load()
    is_weekend = d.weekday() in settings.weekend_day_set()
    if is_weekend and not WorkingDay.objects.filter(date=d, is_active=True).exists():
        return 'weekend', None
    if WFHRecord.objects.filter(employee=employee, start_date__lte=d, end_date__gte=d).exists():
        return 'wfh', _hours_between(check_in, check_out)
    # An approved attendance exception excuses the day regardless of actual
    # check-in time — the employee was off-site with manager/HR sign-off, so
    # the day previews as 'present' rather than 'late'. employee/d are
    # already the exact keys the exception was decided against, so no extra
    # parameter needs to be threaded through this function's signature.
    if AttendanceException.objects.filter(employee=employee, event_date=d, status='approved').exists():
        return 'present', _hours_between(check_in, check_out)
    # An approved LateQuery means the employee successfully challenged this
    # exact day as an incorrect Late mark - same "excuse the day" outcome
    # as an approved AttendanceException.
    if LateQuery.objects.filter(
            employee=employee, attendance_record__date=d, status='approved').exists():
        return 'present', _hours_between(check_in, check_out)
    if check_in:
        if check_in > settings.expected_in_by:
            return 'late', _hours_between(check_in, check_out)
        return 'present', _hours_between(check_in, check_out)
    return 'absent', None


def regenerate_attendance_record(employee, d):
    # Re-derives and saves the AttendanceRecord for one employee/date - used
    # to auto-correct a day the moment an AttendanceException or LateQuery
    # is approved for it, so an already-saved Late/Absent record does not
    # sit wrong indefinitely.
    from hr.models import AttendanceRecord
    rec = AttendanceRecord.objects.filter(employee=employee, date=d).first()
    check_in = rec.check_in if rec else None
    check_out = rec.check_out if rec else None
    status, hours = derive_status(employee, d, check_in, check_out)
    AttendanceRecord.objects.update_or_create(
        employee=employee, date=d,
        defaults={'check_in': check_in, 'check_out': check_out,
                  'status': status, 'hours_worked': hours})