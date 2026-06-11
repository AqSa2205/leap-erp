"""Working-day helpers shared by leave and attendance.

Weekday numbering is Python's: Monday=0 .. Sunday=6. Weekend defaults to
Friday(4)+Saturday(5) for KSA, overridable via AttendanceSettings.
"""
from datetime import timedelta


def is_working_day(d, weekend_days, holidays):
    return d.weekday() not in weekend_days and d not in holidays


def count_working_days(start, end, weekend_days, holidays):
    """Inclusive count of working days in [start, end], excluding weekend + holidays."""
    if end < start:
        return 0
    total = 0
    d = start
    while d <= end:
        if is_working_day(d, weekend_days, holidays):
            total += 1
        d += timedelta(days=1)
    return total
