"""Feed the Wi-Fi attendance into the existing HR attendance grid.

Each counted heartbeat updates an AttendanceDay; that in turn upserts the
matching hr.AttendanceRecord so the normal HR attendance grid / dashboards show
auto-attendance.

Policy: Wi-Fi is the SOURCE OF TRUTH for present/absent — a real office
detection overrides a manual present/absent for that day. It only leaves alone
the authoritative manual states HR must set by hand (leave / holiday / weekend /
WFH), which Wi-Fi can't know about.
"""
from django.utils import timezone


# HR statuses that are authoritative and must never be overwritten by Wi-Fi.
_PROTECTED_STATUSES = {'leave', 'holiday', 'weekend', 'wfh'}


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

    if rec is None:
        rec = AttendanceRecord(employee=day.employee, date=day.date)
    rec.check_in = check_in
    # Once-a-day model detects arrival only — departure is unknown, so no
    # check-out / hours are recorded.
    rec.check_out = None
    rec.status = status
    rec.hours_worked = None
    rec.source = 'wifi'
    rec.note = 'Auto (Wi-Fi)'
    rec.save()
    return rec
