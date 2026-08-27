"""Per-employee attendance totals, and the filtering and ordering the
Attendance Register's filter bar offers.

Kept out of the view so the figures the register ranks on, the figures it
prints, and the figures the Excel/PDF exports carry all come from one place -
a ranking whose numbers are computed twice is a ranking that will eventually
disagree with itself.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

# What the register can be narrowed to. 'all' is the full register; the other
# two keep only the people who actually have that kind of day in the period,
# which is what makes the filter useful rather than decorative.
SHOW_CHOICES = [
    ('all', 'Full attendance'),
    ('late', 'Lates'),
    ('absent', 'Absents'),
]

ORDER_CHOICES = [
    ('name', 'Name'),
    ('punctual', 'Most punctual'),
    ('late', 'Most late'),
    ('warnings', 'Warnings received'),
]

# Punctuality is judged only on days somebody was expected in and an arrival
# time was actually assessed. derive_status() returns 'wfh' before it ever
# looks at check_in, and leave/holiday/weekend never carry one, so those days
# have no arrival to be punctual about and must not dilute the rate. An
# approved AttendanceException or LateQuery already turns the day back into
# 'present', so an excused late counts as on time without anything extra here.
JUDGED_STATUSES = ('present', 'late')


def _month_starts(start, end):
    """First day of every calendar month the period touches."""
    out = []
    d = start.replace(day=1)
    while d <= end:
        out.append(d)
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _next_month(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def warning_counts(employee_ids, start, end):
    """{employee_id: warnings} over the calendar months the period touches.

    There is no Warning model. A warning is the notification
    AttendanceRecord._maybe_notify_late_threshold() fires when someone's late
    count for a CALENDAR MONTH reaches LATE_WARNING_THRESHOLD, once per month.
    Recomputing it from the same rule rather than counting Notification rows
    is deliberate: a notification is only created for an employee who has a
    linked login account, so counting rows would silently under-report anyone
    who has not got one, and notifications can be cleared.

    Counted over whole months, not clipped to the window - a warning was
    earned by a whole month of lates, and showing a third of it for a week
    view would be a number that never happened.
    """
    from hr.models import AttendanceRecord, LATE_WARNING_THRESHOLD

    months = _month_starts(start, end)
    if not months:
        return {}
    window_start, window_end = months[0], _next_month(months[-1])

    per_month = defaultdict(int)
    rows = (AttendanceRecord.objects
            .filter(employee_id__in=employee_ids, status='late',
                    date__gte=window_start, date__lt=window_end)
            .values_list('employee_id', 'date'))
    for employee_id, day in rows:
        per_month[(employee_id, day.replace(day=1))] += 1

    counts = defaultdict(int)
    for (employee_id, _month), lates in per_month.items():
        if lates >= LATE_WARNING_THRESHOLD:
            counts[employee_id] += 1
    return dict(counts)


def build_attendance_summary(employees, start, end):
    """{employee_id: totals} for the period.

    `on_time` and `late` are the two halves of a judged day, so `rate` reads
    as "of the days your arrival was assessed, this share were on time".
    `absent` sits outside that on purpose: not turning up at all is a
    different failure from turning up late, and averaging them into one number
    would let a run of absences improve somebody's punctuality.
    """
    from hr.models import AttendanceRecord

    employee_ids = [e.pk for e in employees]
    totals = {
        e.pk: {'on_time': 0, 'late': 0, 'absent': 0, 'leave': 0, 'wfh': 0,
               'judged': 0, 'rate': None, 'warnings': 0}
        for e in employees
    }
    rows = (AttendanceRecord.objects
            .filter(employee_id__in=employee_ids, date__range=(start, end))
            .values_list('employee_id', 'status'))
    for employee_id, status in rows:
        bucket = totals.get(employee_id)
        if bucket is None:
            continue
        if status == 'present':
            bucket['on_time'] += 1
        elif status == 'late':
            bucket['late'] += 1
        elif status == 'absent':
            bucket['absent'] += 1
        elif status == 'leave':
            bucket['leave'] += 1
        elif status == 'wfh':
            bucket['wfh'] += 1

    warnings = warning_counts(employee_ids, start, end)
    for employee_id, bucket in totals.items():
        bucket['warnings'] = warnings.get(employee_id, 0)
        judged = bucket['on_time'] + bucket['late']
        bucket['judged'] = judged
        if judged:
            bucket['rate'] = (Decimal(bucket['on_time']) / Decimal(judged)
                              * 100).quantize(Decimal('0.1'))
    return totals


def filter_employees(employees, totals, show):
    """Keep only the people the chosen filter is about."""
    if show == 'late':
        return [e for e in employees if totals[e.pk]['late']]
    if show == 'absent':
        return [e for e in employees if totals[e.pk]['absent']]
    return list(employees)


def order_employees(employees, totals, order):
    """Rank the register.

    Punctuality ranks on the rate, then on how many days back it - a single
    perfect day must not outrank a month of them. Someone with no assessed day
    has neither, so they sink to the bottom on both terms: no record at all is
    not a perfect record, and `rate or 0` is what stops a missing rate
    sorting as though it were the best one.
    """
    if order == 'punctual':
        return sorted(
            employees,
            key=lambda e: (totals[e.pk]['rate'] or 0, totals[e.pk]['judged']),
            reverse=True)
    if order == 'late':
        return sorted(employees,
                      key=lambda e: (totals[e.pk]['late'],
                                     totals[e.pk]['absent']),
                      reverse=True)
    if order == 'warnings':
        return sorted(employees,
                      key=lambda e: (totals[e.pk]['warnings'],
                                     totals[e.pk]['late']),
                      reverse=True)
    return sorted(employees, key=lambda e: (e.full_name or '').lower())


def resolve_register_filters(GET):
    """(show, order), both validated against the choices above.

    Anything unrecognised falls back to the full register in name order rather
    than to an empty page, so a stale or hand-edited link still shows the
    register.
    """
    show = GET.get('show')
    if show not in dict(SHOW_CHOICES):
        show = 'all'
    order = GET.get('order')
    if order not in dict(ORDER_CHOICES):
        order = 'name'
    return show, order


def apply_register_filters(employees, start, end, show, order):
    """(rows_in_order, totals, counts) for the register and its exports.

    `counts` is how many people each filter would show, so the chips can carry
    their own numbers and a filter that would empty the page says so before it
    is clicked.
    """
    totals = build_attendance_summary(employees, start, end)
    counts = {
        'all': len(employees),
        'late': sum(1 for e in employees if totals[e.pk]['late']),
        'absent': sum(1 for e in employees if totals[e.pk]['absent']),
    }
    kept = filter_employees(employees, totals, show)
    return order_employees(kept, totals, order), totals, counts
