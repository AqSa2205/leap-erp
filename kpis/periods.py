"""Period parsing for KPI reporting windows.

A *period* is a short string the dashboard passes around and the KPIEntry rows
key off. Three granularities are supported:

    '2026'      -> full calendar year
    '2026-Q2'   -> a quarter
    '2026-06'   -> a single month

`period_bounds()` turns any of these into a half-open [start, end) date range so
the compute functions can filter with `__gte=start, __lt=end` (no off-by-one on
the last day).
"""
import datetime


def period_bounds(period):
    """Return (start_date, end_date) half-open for a period string.

    Raises ValueError on anything we don't recognise so a bad querystring fails
    loudly in the view rather than silently returning the wrong window.
    """
    period = (period or '').strip()
    parts = period.split('-')

    # 'YYYY'
    if len(parts) == 1:
        year = int(parts[0])
        return datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1)

    year = int(parts[0])
    tail = parts[1]

    # 'YYYY-Qn'
    if tail.upper().startswith('Q'):
        q = int(tail[1:])
        if not 1 <= q <= 4:
            raise ValueError(f'Bad quarter: {period}')
        start_month = (q - 1) * 3 + 1
        start = datetime.date(year, start_month, 1)
        end = _add_months(start, 3)
        return start, end

    # 'YYYY-MM'
    month = int(tail)
    if not 1 <= month <= 12:
        raise ValueError(f'Bad month: {period}')
    start = datetime.date(year, month, 1)
    return start, _add_months(start, 1)


def _add_months(d, n):
    """First day of the month n months after date d (which must be a 1st)."""
    idx = (d.year * 12 + (d.month - 1)) + n
    return datetime.date(idx // 12, idx % 12 + 1, 1)


def label_for(period):
    """Human label for a period string, e.g. '2026-06' -> 'June 2026'."""
    parts = (period or '').split('-')
    if len(parts) == 1:
        return f'FY {parts[0]}'
    if parts[1].upper().startswith('Q'):
        return f'{parts[1].upper()} {parts[0]}'
    month = int(parts[1])
    return f'{datetime.date(2000, month, 1):%B} {parts[0]}'


def current_period(period_type='month', today=None):
    """The period string covering `today` (defaults to the current date)."""
    if today is None:
        from django.utils import timezone
        today = timezone.now().date()
    if period_type == 'year':
        return str(today.year)
    if period_type == 'quarter':
        q = (today.month - 1) // 3 + 1
        return f'{today.year}-Q{q}'
    return f'{today.year}-{today.month:02d}'


def period_options(today=None, back=12):
    """Recent selectable periods: current + last `back` months, the 4 quarters,
    and the current + previous year. Returned as a list of (value, label)."""
    if today is None:
        from django.utils import timezone
        today = timezone.now().date()
    out = []
    # Months: current and prior `back` months.
    cur = datetime.date(today.year, today.month, 1)
    for i in range(back):
        idx = (cur.year * 12 + (cur.month - 1)) - i
        d = datetime.date(idx // 12, idx % 12 + 1, 1)
        p = f'{d.year}-{d.month:02d}'
        out.append((p, label_for(p)))
    # Quarters of the current year.
    for q in range(1, 5):
        p = f'{today.year}-Q{q}'
        out.append((p, label_for(p)))
    # Years.
    for y in (today.year, today.year - 1):
        out.append((str(y), label_for(str(y))))
    return out
