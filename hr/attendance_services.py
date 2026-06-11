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

    Precedence: leave > holiday > weekend(unless WorkingDay) > wfh > late/present > absent.
    """
    from hr.models import LeaveRecord, Holiday, AttendanceSettings, WorkingDay
    if LeaveRecord.objects.filter(employee=employee, start_date__lte=d, end_date__gte=d).exists():
        return 'leave', None
    if Holiday.objects.filter(date=d, is_active=True).exists():
        return 'holiday', None
    settings = AttendanceSettings.load()
    is_weekend = d.weekday() in settings.weekend_day_set()
    if is_weekend and not WorkingDay.objects.filter(date=d, is_active=True).exists():
        return 'weekend', None
    # (A WFH branch will be added here in a later task, before the check_in block.)
    if check_in:
        if check_in > settings.expected_in_by:
            return 'late', _hours_between(check_in, check_out)
        return 'present', _hours_between(check_in, check_out)
    return 'absent', None
