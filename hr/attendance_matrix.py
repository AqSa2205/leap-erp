"""Batched computation for the attendance register (matrix).

Pure helpers (no HTTP) so the grid logic is unit-testable. Cell precedence
mirrors derive_status: a stored AttendanceRecord.status wins; else
leave > holiday > weekend; else '' (blank = not yet recorded).
"""
import calendar
from datetime import timedelta


def period_range(period, anchor):
    """Return (start, end) dates for the week (Sun-start) or month containing `anchor`."""
    if period == 'week':
        days_since_sunday = (anchor.weekday() + 1) % 7   # Mon=0..Sun=6 -> Sun-start
        start = anchor - timedelta(days=days_since_sunday)
        end = start + timedelta(days=6)
    else:  # 'month'
        start = anchor.replace(day=1)
        last_day = calendar.monthrange(anchor.year, anchor.month)[1]
        end = anchor.replace(day=last_day)
    return start, end


def display_status_no_record(d):
    """Cell status for a day with no AttendanceRecord and no leave: holiday/weekend/''."""
    from hr.models import Holiday, AttendanceSettings
    if Holiday.objects.filter(date=d, is_active=True).exists():
        return 'holiday'
    if d.weekday() in AttendanceSettings.load().weekend_day_set():
        return 'weekend'
    return ''


def build_matrix(employees, start, end):
    """Return (days, rows). `days` is the list of dates; `rows` is
    [{'employee', 'cells': [{'date','status','leave_record_id','locked'}]}].
    Batched: ~4 queries regardless of grid size."""
    from hr.models import AttendanceRecord, LeaveRecord, Holiday, AttendanceSettings

    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)

    emp_ids = [e.pk for e in employees]

    rec_status = {
        (r.employee_id, r.date): r.status
        for r in AttendanceRecord.objects.filter(employee_id__in=emp_ids, date__range=(start, end))
    }

    # (emp_id, date) -> leave_record pk if that record is single-day (removable), else None
    leave_cell = {}
    for lr in LeaveRecord.objects.filter(
            employee_id__in=emp_ids, start_date__lte=end, end_date__gte=start):
        removable_pk = lr.pk if lr.start_date == lr.end_date else None
        dd = max(lr.start_date, start)
        last = min(lr.end_date, end)
        while dd <= last:
            leave_cell[(lr.employee_id, dd)] = removable_pk
            dd += timedelta(days=1)

    holidays = set(Holiday.objects.filter(
        is_active=True, date__range=(start, end)).values_list('date', flat=True))
    weekends = AttendanceSettings.load().weekend_day_set()

    rows = []
    for emp in employees:
        cells = []
        for day in days:
            key = (emp.pk, day)
            leave_pk = None
            if key in rec_status:
                status = rec_status[key]
                if status == 'leave':
                    leave_pk = leave_cell.get(key)
            elif key in leave_cell:
                status = 'leave'
                leave_pk = leave_cell[key]
            elif day in holidays:
                status = 'holiday'
            elif day.weekday() in weekends:
                status = 'weekend'
            else:
                status = ''
            cells.append({
                'date': day, 'status': status,
                'leave_record_id': leave_pk,
                'locked': status in ('weekend', 'holiday'),
            })
        rows.append({'employee': emp, 'cells': cells})
    return days, rows
