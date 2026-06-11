"""Attendance status derivation: leave > holiday > weekend > present > absent."""
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
    """Return (status, hours_worked). Reads leave records, holidays, weekend config."""
    from hr.models import LeaveRecord, Holiday, AttendanceSettings
    on_leave = LeaveRecord.objects.filter(
        employee=employee, start_date__lte=d, end_date__gte=d).exists()
    if on_leave:
        return 'leave', None
    if Holiday.objects.filter(date=d, is_active=True).exists():
        return 'holiday', None
    weekends = AttendanceSettings.load().weekend_day_set()
    if d.weekday() in weekends:
        return 'weekend', None
    if check_in:
        return 'present', _hours_between(check_in, check_out)
    return 'absent', None
