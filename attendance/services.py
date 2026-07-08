"""Feed the Wi-Fi attendance into the existing HR attendance grid.

Each counted heartbeat updates an AttendanceDay; that in turn upserts the
matching hr.AttendanceRecord so the normal HR attendance grid / dashboards show
auto-attendance.

Policy: Wi-Fi is the SOURCE OF TRUTH for present/absent — a real office
detection overrides a manual present/absent for that day. It only leaves alone
the authoritative manual states HR must set by hand (leave / holiday / weekend /
WFH), which Wi-Fi can't know about.
"""
from datetime import datetime, time as _time
from decimal import Decimal

from django.conf import settings
from django.utils import timezone


# HR statuses that are authoritative and must never be overwritten by Wi-Fi.
_PROTECTED_STATUSES = {'leave', 'holiday', 'weekend', 'wfh'}


def _fixed_checkout():
    """The standard check-out time stamped on auto records (default 18:00)."""
    raw = getattr(settings, 'ATT_CHECKOUT_TIME', '18:00')
    try:
        h, m = str(raw).split(':')
        return _time(int(h), int(m))
    except (ValueError, AttributeError):
        return _time(18, 0)


def sync_hr_attendance(day):
    """Upsert the hr.AttendanceRecord for this AttendanceDay.

    Wi-Fi detection overrides a manual present/absent (Wi-Fi is authoritative for
    present/absent). Days marked leave / holiday / weekend / WFH are left alone.
    """
    from hr.models import AttendanceRecord
    try:
        from hr.models import AttendanceSettings
        expected_in_by = AttendanceSettings.load().expected_in_by
    except Exception:
        expected_in_by = None

    rec = AttendanceRecord.objects.filter(employee=day.employee, date=day.date).first()
    if rec is not None and rec.status in _PROTECTED_STATUSES:
        return  # leave / holiday / weekend / WFH — HR-set, keep it

    check_in = timezone.localtime(day.first_seen).time() if day.first_seen else None
    status = 'present'
    if check_in and expected_in_by and check_in > expected_in_by:
        status = 'late'

    # Arrival is detected; departure isn't, so stamp a standard 6 PM check-out
    # and compute hours from the real check-in to it.
    check_out = _fixed_checkout()
    hours = None
    if check_in:
        delta = (datetime.combine(day.date, check_out)
                 - datetime.combine(day.date, check_in)).total_seconds() / 3600.0
        hours = Decimal(str(round(max(0.0, delta), 2)))

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
