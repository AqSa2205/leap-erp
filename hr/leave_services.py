"""Leave entitlement generation."""


def generate_year_entitlements(year, actor=None):
    """Create missing LeaveEntitlement rows for all active employees for `year`,
    using each leave type's flat default_annual_days (Annual included). Existing
    (employee, type, year) rows are left untouched. Returns rows created.
    """
    from hr.models import Employee, LeaveType, LeaveEntitlement
    created = 0
    leave_types = list(LeaveType.objects.filter(is_active=True))
    for emp in Employee.objects.filter(is_active=True):
        for lt in leave_types:
            _, was_created = LeaveEntitlement.objects.get_or_create(
                employee=emp, leave_type=lt, year=year,
                defaults={'entitled_days': lt.default_annual_days, 'created_by': actor},
            )
            if was_created:
                created += 1
    return created
