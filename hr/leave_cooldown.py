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

    # Deliberately NOT filtered to end_date__lte=today: an already-approved
    # leave that hasn't happened yet must count too, or an employee could
    # get a holiday approved for later this year and immediately submit a
    # second one the same afternoon, defeating the whole point of spacing
    # applications out. Taking the record with the latest end_date (past,
    # ongoing, or future) means an upcoming booking is what the cooldown is
    # computed from until it's actually the most recent thing on record.
    last_record = (
        LeaveRecord.objects
        .filter(employee=employee, leave_type=leave_type)
        .order_by('-end_date')
        .first())
    if last_record is None:
        return None

    last_leave_days = last_record.days or Decimal('0')
    already_ended = last_record.end_date <= today
    if already_ended:
        leave_phrase = f'You returned from a {last_leave_days:g}-day annual leave on {last_record.end_date:%d %b %Y}.'
    else:
        leave_phrase = f'You have a {last_leave_days:g}-day annual leave booked, ending {last_record.end_date:%d %b %Y}.'

    entitlement_year = last_record.start_date.year
    entitlement = employee.leave_entitlements.filter(
        leave_type=leave_type, year=entitlement_year).first()
    # Cumulative across every booking in the year (entitlement.taken_days
    # sums all of the employee's LeaveRecord rows for this type/year), not
    # just the last one — someone who uses up their full entitlement
    # gradually across several smaller bookings hits the same "wait for
    # next year" rule as someone who used it all in a single request.
    # Compared against effective_entitled_days (base + any HR-granted
    # LeaveExceptionGrant days), matching how "full balance" is defined
    # everywhere else in this codebase (see validate_leave_submission) —
    # an employee with bonus exception days must not be judged against
    # their un-topped-up base entitled_days alone.
    used_full_entitlement = bool(
        entitlement and entitlement.effective_entitled_days > 0
        and entitlement.taken_days >= entitlement.effective_entitled_days)

    if used_full_entitlement:
        used_up_phrase = (
            f'You have used your full {entitlement.effective_entitled_days:g}-day annual leave entitlement '
            f'for {entitlement_year} (most recently ending {last_record.end_date:%d %b %Y}).')
        if employee.joining_date:
            eligible_date = _next_joining_anniversary(employee.joining_date, last_record.end_date)
            # Deliberately NOT phrased as "when your leave entitlement
            # renews" — LeaveEntitlement is a calendar-year row and has
            # already renewed on 1 Jan by this point. This wait is a
            # separate, deliberate policy (re-apply from your own work
            # anniversary), not a claim about when the entitlement itself
            # resets, so the wording says so plainly instead of conflating
            # the two.
            reason = (
                f'{used_up_phrase} Our policy is that once you have used your full entitlement for the '
                f'year, you can apply again from your next work anniversary: {eligible_date:%d %b %Y}.')
        else:
            # Anchored to entitlement_year (the same year the entitlement
            # itself is keyed to), not last_record.end_date.year — those can
            # differ for a leave that spans a year boundary.
            eligible_date = date(entitlement_year + 1, 1, 1)
            reason = (
                f'{used_up_phrase} You can apply again from {eligible_date:%d %b %Y}, when your leave '
                f'entitlement renews.')
    elif last_leave_days < SHORT_LEAVE_THRESHOLD_DAYS:
        eligible_date = _add_months(last_record.end_date, SHORT_LEAVE_COOLDOWN_MONTHS)
        reason = (
            f'{leave_phrase} '
            f'You can apply for annual leave again from {eligible_date:%d %b %Y}.')
    else:
        eligible_date = _add_months(last_record.end_date, LONG_LEAVE_COOLDOWN_MONTHS)
        reason = (
            f'{leave_phrase} '
            f'Because it was 10 days or more, you can apply for annual leave again from '
            f'{eligible_date:%d %b %Y}.')

    if today < eligible_date:
        return {
            'eligible_date': eligible_date,
            'reason': reason,
            'days_remaining': (eligible_date - today).days,
            'leave_type_id': leave_type.pk,
        }
    return None
