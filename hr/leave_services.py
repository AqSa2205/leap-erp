"""Leave entitlement generation and submission validation."""


def validate_leave_submission(employee, leave_type, start_date, end_date, lock=False, exclude_request_id=None):
    """Raises ValueError if this leave submission is invalid:
    - no entitlement set up for this employee/type/year,
    - it would exceed the effective remaining balance (standard baseline +
      any HR-granted exception days) once OTHER still-pending requests for
      the same employee/type/year are also counted as already spoken-for
      (a pending request doesn't yet have a LeaveRecord, so
      effective_remaining_days alone doesn't know about it — without this,
      two individually-valid submissions could together exceed the
      entitlement) — UNLESS the employee's work_location has balance
      holding enabled (see OverrideAccessSettings), in which case this
      returns True instead of raising: the caller creates a held,
      Super-Admin-reviewable request instead of blocking it,
    - it overlaps another pending request or already-taken/approved leave
      (LeaveRecord) this employee has, REGARDLESS of leave type (a person
      cannot be on two kinds of leave at once).

    Returns a bool: True if this submission exceeds the effective balance
    and was allowed through as a hold rather than raising. False means the
    submission is within the effective balance.

    With lock=True, the relevant LeaveEntitlement row is locked
    (select_for_update) for the rest of the caller's transaction — the
    caller MUST be inside transaction.atomic() and must create the
    LeaveRequest before that transaction commits, or concurrent submissions
    for the same employee/type/year can still race past each other.

    `exclude_request_id`, when set, excludes that LeaveRequest's own pk from
    both the pending-balance sum and the overlap check — required when
    re-validating an in-place EDIT of an already-pending request, which
    would otherwise collide with its own unmodified row."""
    from decimal import Decimal
    from .models import LeaveEntitlement, LeaveRequest, LeaveRecord, OverrideAccessSettings

    entitlement_qs = LeaveEntitlement.objects.filter(
        employee=employee, leave_type=leave_type, year=start_date.year)
    if lock:
        entitlement_qs = entitlement_qs.select_for_update()
    entitlement = entitlement_qs.first()
    if entitlement is None:
        raise ValueError(
            f'No leave entitlement has been set up for {leave_type.name} in {start_date.year} '
            f'for {employee.full_name}; contact HR before submitting this request.')

    requested_days = Decimal((end_date - start_date).days + 1)
    pending_qs = LeaveRequest.objects.filter(
        employee=employee, leave_type=leave_type, status='pending', start_date__year=start_date.year)
    if exclude_request_id is not None:
        pending_qs = pending_qs.exclude(pk=exclude_request_id)
    pending_days = sum((r.days or Decimal('0') for r in pending_qs), Decimal('0'))
    available_days = entitlement.effective_remaining_days - pending_days

    exceeds_balance = False
    if requested_days > available_days:
        config = OverrideAccessSettings.get_solo()
        if config.balance_hold_enabled_for(employee.work_location):
            exceeds_balance = True
        elif pending_days:
            raise ValueError(
                f'This request is {requested_days} day(s), but only {available_days} day(s) of '
                f'{leave_type.name} remain for {employee.full_name} in {start_date.year} '
                f'({pending_days} day(s) are already tied up in other pending requests). '
                'Reduce the date range, wait for those to be decided, or contact HR.')
        else:
            raise ValueError(
                f'This request is {requested_days} day(s), but only {available_days} day(s) of '
                f'{leave_type.name} remain for {employee.full_name} in {start_date.year}. '
                'Reduce the date range or contact HR.')

    overlap_qs = LeaveRequest.objects.filter(
        employee=employee, status='pending', start_date__lte=end_date, end_date__gte=start_date)
    if exclude_request_id is not None:
        overlap_qs = overlap_qs.exclude(pk=exclude_request_id)
    if overlap_qs.exists():
        raise ValueError(
            'This date range overlaps with another leave request you already have pending '
            '(regardless of leave type — you cannot be on two leaves at once).')
    if LeaveRecord.objects.filter(
            employee=employee, start_date__lte=end_date, end_date__gte=start_date).exists():
        raise ValueError(
            'This date range overlaps with leave you have already taken or been approved for.')

    return exceeds_balance


def preview_leave_shortfall(employee, leave_type, start_date, end_date):
    """Read-only preview (no locking, no side effects, never raises): how
    many days, if any, this request would exceed the employee's effective
    balance by. Returns Decimal('0') if it fits (or there's no entitlement
    set up — validate_leave_submission is what raises that error). Used by
    the HR 'log on behalf of' form to show a warning before submission."""
    from decimal import Decimal
    from .models import LeaveEntitlement, LeaveRequest

    entitlement = LeaveEntitlement.objects.filter(
        employee=employee, leave_type=leave_type, year=start_date.year).first()
    if entitlement is None:
        return Decimal('0')
    requested_days = Decimal((end_date - start_date).days + 1)
    pending_days = sum(
        (r.days or Decimal('0') for r in LeaveRequest.objects.filter(
            employee=employee, leave_type=leave_type, status='pending', start_date__year=start_date.year)),
        Decimal('0'))
    available_days = entitlement.effective_remaining_days - pending_days
    shortfall = requested_days - available_days
    return shortfall if shortfall > Decimal('0') else Decimal('0')


def generate_entitlements_for_employee(employee, year, actor=None):
    """Create this one employee's missing LeaveEntitlement rows for `year`,
    using each leave type's default_annual_days, or site_default_annual_days
    when the employee is Site-based and one is set. Existing (employee,
    type, year) rows are left untouched. Returns rows created. Shared by
    generate_year_entitlements (all employees, bulk) and Employee.save()
    (a single newly-created employee)."""
    from hr.models import LeaveType, LeaveEntitlement
    created = 0
    for lt in LeaveType.objects.filter(is_active=True):
        _, was_created = LeaveEntitlement.objects.get_or_create(
            employee=employee, leave_type=lt, year=year,
            defaults={'entitled_days': lt.default_days_for(employee.work_location), 'created_by': actor},
        )
        if was_created:
            created += 1
    return created


def generate_year_entitlements(year, actor=None):
    """Create missing LeaveEntitlement rows for all active employees for `year`,
    using each leave type's flat default_annual_days (Annual included). Existing
    (employee, type, year) rows are left untouched. Returns rows created.
    """
    from hr.models import Employee
    created = 0
    for emp in Employee.objects.filter(is_active=True):
        created += generate_entitlements_for_employee(emp, year, actor=actor)
    return created


def reapply_leave_type_defaults(year=None, leave_type=None):
    """Force every entitlement's day count to match its leave type's current
    default (Office or Site default, per employee.work_location). Overwrites
    custom entitled_days values (the leave type is the source of truth for
    the baseline) — but never touches LeaveExceptionGrant rows, which live
    outside entitled_days entirely, so an HR exception grant survives a
    reapply. Optionally scoped to a single `year` and/or a single
    `leave_type` (an instance or pk). Returns rows updated.
    """
    from hr.models import LeaveType, LeaveEntitlement
    updated = 0
    leave_types = [leave_type] if leave_type is not None else LeaveType.objects.all()
    for lt in leave_types:
        qs = LeaveEntitlement.objects.filter(leave_type=lt)
        if year is not None:
            qs = qs.filter(year=year)
        updated += qs.exclude(employee__work_location='site').update(entitled_days=lt.default_annual_days)
        updated += qs.filter(employee__work_location='site').update(entitled_days=lt.default_days_for('site'))
    return updated


def apply_work_location_transfer(employee, *, old_location, new_location, year, actor):
    """Recompute the current year's Annual entitlement baseline after an
    Employee.work_location change — retroactive for the whole year. On a
    downgrade where taken_days already exceeds the new, lower baseline,
    auto-grants the exact shortfall as a LeaveExceptionGrant so the
    employee isn't shown a negative balance for leave they validly took
    under the old baseline."""
    from decimal import Decimal
    from hr.models import LeaveType, LeaveEntitlement, LeaveExceptionGrant
    if old_location == new_location:
        return
    for lt in LeaveType.objects.filter(is_active=True, code='annual'):
        ent = LeaveEntitlement.objects.filter(employee=employee, leave_type=lt, year=year).first()
        if ent is None:
            continue
        new_baseline = lt.default_days_for(new_location)
        shortfall = ent.taken_days - new_baseline
        ent.entitled_days = new_baseline
        ent.save(update_fields=['entitled_days'])
        if shortfall > Decimal('0'):
            LeaveExceptionGrant.objects.create(
                employee=employee, leave_type=lt, year=year, days=shortfall, granted_by=actor,
                reason=f'Auto-preserved {shortfall} day(s) already taken under the {old_location or "unassigned"} '
                       f'baseline before transfer to {new_location}.')
