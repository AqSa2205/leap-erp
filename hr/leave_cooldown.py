""" Post-vacation cooldown on annual leaves, this is a standalone, additive check.
It is only consulted for is_accumulative leave types and today thats
just annual. Every other leave type and file is unaffected by this
page. """

from datetime import date
from decimal import Decimal
import calendar

SHORT_LEAVE_THRESHOLD_DAYS = Decimal('10')
SHORT_LEAVE_COOLDOWN_MONTHS = 1
LONG_LEAVE_COOLDOWN_MONTHS = 3


def _add_months(start, months):
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _next_joining_anniversary(joining_date, after):
    try:
        candidate = joining_date.replace(year=after.year)
    except ValueError:
        candidate = joining_date.replace(year=after.year, day=28)
    if candidate <= after:
        try:
            candidate = joining_date.replace(year=after.year + 1)
        except ValueError:
            candidate = joining_date.replace(year=after.year + 1, day=28)
    return candidate


def annual_leave_cooldown(employee, leave_type, *, today=None):
    from hr.models import LeaveRecord

    if not leave_type.is_accumulative:
        return None

    today = today or date.today()

    last_record = (
        LeaveRecord.objects
        .filter(employee=employee, leave_type=leave_type, end_date__lte=today)
        .order_by('-end_date')
        .first())
    if last_record is None:
        return None

    taken_days = last_record.days or Decimal('0')

    entitlement = employee.leave_entitlements.filter(
        leave_type=leave_type, year=last_record.start_date.year).first()
    used_full_entitlement = bool(
        entitlement and entitlement.entitled_days > 0
        and taken_days >= entitlement.entitled_days)

    if used_full_entitlement:
        if employee.joining_date:
            eligible_date = _next_joining_anniversary(employee.joining_date, last_record.end_date)
        else:
            eligible_date = date(last_record.end_date.year + 1, 1, 1)
        reason = (
            f'You used your full {entitlement.entitled_days:g}-day annual leave entitlement in one '
            f'booking (ending {last_record.end_date:%d %b %Y}). You can apply again from '
            f'{eligible_date:%d %b %Y}, when your leave entitlement renews.')
    elif taken_days < SHORT_LEAVE_THRESHOLD_DAYS:
        eligible_date = _add_months(last_record.end_date, SHORT_LEAVE_COOLDOWN_MONTHS)
        reason = (
            f'You returned from a {taken_days:g}-day annual leave on {last_record.end_date:%d %b %Y}. '
            f'You can apply for annual leave again from {eligible_date:%d %b %Y}.')
    else:
        eligible_date = _add_months(last_record.end_date, LONG_LEAVE_COOLDOWN_MONTHS)
        reason = (
            f'You returned from a {taken_days:g}-day annual leave on {last_record.end_date:%d %b %Y}. '
            f'Because it was 10 days or more, you can apply for annual leave again from '
            f'{eligible_date:%d %b %Y}.')

    if today < eligible_date:
        return {'eligible_date': eligible_date, 'reason': reason}
    return None
