"""Leave entitlement generation — encapsulates the Leap annual rule."""
from decimal import Decimal


def annual_entitlement_for(joining_date, year):
    """Leap policy: 25 in the joining calendar year, 30 from the next year on.

    (The 12-month anniversary always lands in joining_date.year + 1, so the
    'anniversary year -> 30' rule reduces to: joining year = 25, after = 30.)
    """
    if joining_date is None:
        return Decimal('30')
    return Decimal('25') if year <= joining_date.year else Decimal('30')


def generate_year_entitlements(year, actor=None):
    """Create missing LeaveEntitlement rows for all active employees for `year`.

    Annual uses annual_entitlement_for(); other leave types use their flat
    default_annual_days. Existing (employee, type, year) rows are left untouched.
    Returns the count of rows created.
    """
    from hr.models import Employee, LeaveType, LeaveEntitlement
    created = 0
    leave_types = list(LeaveType.objects.filter(is_active=True))
    for emp in Employee.objects.filter(is_active=True):
        for lt in leave_types:
            if lt.code == 'annual':
                entitled = annual_entitlement_for(emp.joining_date, year)
            else:
                entitled = lt.default_annual_days
            _, was_created = LeaveEntitlement.objects.get_or_create(
                employee=emp, leave_type=lt, year=year,
                defaults={'entitled_days': entitled, 'created_by': actor},
            )
            if was_created:
                created += 1
    return created
