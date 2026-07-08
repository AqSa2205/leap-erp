"""Feed the Wi-Fi attendance into the existing HR attendance grid.

Each counted heartbeat updates an AttendanceDay; that in turn upserts the
matching hr.AttendanceRecord so the normal HR attendance grid / dashboards show
auto-attendance. It NEVER clobbers an explicit manual entry.
"""
from decimal import Decimal

from django.utils import timezone


# HR statuses that are authoritative and must never be overwritten by Wi-Fi.
_PROTECTED_STATUSES = {'leave', 'holiday', 'weekend', 'wfh'}


def sync_hr_attendance(day):
    """Upsert the hr.AttendanceRecord for this AttendanceDay.

    Skips (leaves untouched) any existing record that is a real manual entry —
    one the agent didn't create — i.e. it has a check-in time or a protected
    status (leave/holiday/weekend/WFH). A bare auto/absent placeholder with no
    check-in is upgraded to the detected presence.
    """
    from hr.models import AttendanceRecord
    try:
        from hr.models import AttendanceSettings
        expected_in_by = AttendanceSettings.load().expected_in_by
    except Exception:
        expected_in_by = None

    rec = AttendanceRecord.objects.filter(employee=day.employee, date=day.date).first()
    if rec is not None and rec.source != 'wifi':
        if rec.check_in is not None or rec.status in _PROTECTED_STATUSES:
            return  # explicit manual entry — respect it

    check_in = timezone.localtime(day.first_seen).time() if day.first_seen else None
    check_out = timezone.localtime(day.last_seen).time() if day.last_seen else None
    status = 'present'
    if check_in and expected_in_by and check_in > expected_in_by:
        status = 'late'
    hours = (Decimal(day.active_minutes) / Decimal('60')).quantize(Decimal('0.01'))

    if rec is None:
        rec = AttendanceRecord(employee=day.employee, date=day.date)
    rec.check_in = check_in
    rec.check_out = check_out
    rec.status = status
    rec.hours_worked = hours
    rec.source = 'wifi'
    rec.note = 'Auto (Wi-Fi)'
    rec.save()
    return rec
