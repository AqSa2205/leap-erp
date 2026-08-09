# Annual Leave — Location Baselines & HR Exception Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat 30-day Annual Leave cap with a per-`work_location` baseline (Office/Site,
editable), a standing HR-granted "exception days" bank on top of it, a configurable override that lets
Super Admins push a specific over-cap Site request through, and sidebar pending-count indicators for Leave
Requests / Team Exceptions.

**Architecture:** `LeaveEntitlement.entitled_days` keeps meaning "standard baseline only." A new
`LeaveExceptionGrant` audit-log model supplies a computed `exception_days` on top of it. Validation checks
the computed `effective_remaining_days`. A request that exceeds it either hard-blocks (default: Office) or
is created "held" — `exceeds_balance=True`, zero approver rows — which the *existing* Leave Requests
queue/detail Override control (already gated only by `has_override_access` + `status=='pending'`) already
knows how to act on with no changes to that gating. Sidebar badges are a single context processor reusing
the exact querysets the real pages already filter by.

**Tech Stack:** Django (this repo's existing patterns only — no new libraries).

## Global Constraints

- Per the spec (`docs/superpowers/specs/2026-08-06-leave-entitlement-exceptions-design.md`): `entitled_days`
  must never be inflated by an exception grant — it is always the baseline anchor shown to the employee.
- `reapply_leave_type_defaults()` must never touch `LeaveExceptionGrant` rows.
- The hold-vs-block branch is driven by `employee.work_location` alone, identically for self-service and
  HR-logged submissions — never by who is submitting.
- Before generating any migration: `git pull origin dev` (the branch was already cut from a fresh
  `origin/dev` at the start of this work — re-pull only if time has passed / other work has landed).
- After any task that touches shared code (`leave_services.py`, `leave_approval_services.py`,
  `models/leave.py`, `models/employee.py`), run the **full** `python manage.py test`, not just `hr` — a
  past refactor broke an unrelated seed command that its own feature's tests never touched.
- Colors/markup for new UI reuse existing classes: `var(--leap-red)` for count badges (matches
  `.notif-badge` in `templates/base.html`), the three existing `.team-exc-tab-*` accent classes stay as tab
  identity colors, not overloaded for badges.

---

### Task 1: `LeaveType` location-aware defaults + location-aware propagation

**Files:**
- Modify: `hr/models/leave.py:5-40` (`LeaveType`)
- Modify: `hr/admin.py:19-21` (`LeaveTypeAdmin`)
- Test: `hr/tests.py` (new test class near the existing `LeaveType`/entitlement tests, e.g. after line 279)

**Interfaces:**
- Produces: `LeaveType.site_default_annual_days` (nullable `DecimalField`), `LeaveType.default_days_for(work_location)` — returns `site_default_annual_days` if set and `work_location == 'site'`, else `default_annual_days`.

- [x] **Step 1: Write the failing test**

```python
class LeaveTypeLocationDefaultsTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'))

    def test_default_days_for_office_uses_flat_default(self):
        self.assertEqual(self.lt.default_days_for('office'), Decimal('30'))

    def test_default_days_for_site_falls_back_when_blank(self):
        self.assertEqual(self.lt.default_days_for('site'), Decimal('30'))

    def test_default_days_for_site_uses_override_when_set(self):
        self.lt.site_default_annual_days = Decimal('45')
        self.lt.save()
        self.assertEqual(self.lt.default_days_for('site'), Decimal('45'))

    def test_propagate_on_save_is_location_aware(self):
        office_emp = Employee.objects.create(full_name='Office Emp', work_location='office', is_active=True)
        site_emp = Employee.objects.create(full_name='Site Emp', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=office_emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        LeaveEntitlement.objects.create(employee=site_emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        self.lt.default_annual_days = Decimal('32')
        self.lt.site_default_annual_days = Decimal('47')
        self.lt.save()
        self.assertEqual(LeaveEntitlement.objects.get(employee=office_emp).entitled_days, Decimal('32'))
        self.assertEqual(LeaveEntitlement.objects.get(employee=site_emp).entitled_days, Decimal('47'))
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LeaveTypeLocationDefaultsTests -v 2`
Expected: FAIL — `site_default_annual_days`/`default_days_for` don't exist yet.

- [x] **Step 3: Implement**

In `hr/models/leave.py`, add the field to `LeaveType` (after `default_annual_days`, line 8):

```python
    site_default_annual_days = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
        help_text='Override default for Site employees. Blank = same as the default above (Office).')
```

Add the method (anywhere in the class body, e.g. after `__str__`):

```python
    def default_days_for(self, work_location):
        if work_location == 'site' and self.site_default_annual_days is not None:
            return self.site_default_annual_days
        return self.default_annual_days
```

Replace the flat propagation in `save()` (currently `self.entitlements.update(entitled_days=self.default_annual_days)`) with location-aware updates:

```python
        if old_days is not None and (old_days != self.default_annual_days or old_site_days != self.site_default_annual_days):
            self.entitlements.filter(employee__work_location='office').update(entitled_days=self.default_annual_days)
            self.entitlements.filter(employee__work_location='site').update(
                entitled_days=self.site_default_annual_days if self.site_default_annual_days is not None else self.default_annual_days)
```

This also needs `old_site_days` captured alongside `old_days` at the top of `save()` — change:
```python
        old_days = None
        if self.pk:
            prev = type(self).objects.filter(pk=self.pk).only('default_annual_days').first()
            if prev is not None:
                old_days = prev.default_annual_days
```
to:
```python
        old_days = None
        old_site_days = None
        if self.pk:
            prev = type(self).objects.filter(pk=self.pk).only('default_annual_days', 'site_default_annual_days').first()
            if prev is not None:
                old_days = prev.default_annual_days
                old_site_days = prev.site_default_annual_days
```

Update `LeaveTypeAdmin.list_display` in `hr/admin.py`:
```python
    list_display = ['name', 'code', 'default_annual_days', 'site_default_annual_days', 'is_paid', 'is_active']
```

- [x] **Step 4: Generate and apply the migration**

Run: `venv\Scripts\python.exe manage.py makemigrations hr`
Expected: creates `hr/migrations/0037_leavetype_site_default_annual_days.py` (or similar auto-generated name) adding the one field. Then: `venv\Scripts\python.exe manage.py migrate hr`

- [x] **Step 5: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LeaveTypeLocationDefaultsTests -v 2`
Expected: PASS (4/4).

- [x] **Step 6: Commit**

```bash
git add hr/models/leave.py hr/admin.py hr/migrations/ hr/tests.py
git commit -m "hr: location-aware LeaveType defaults (Office/Site)"
```

---

### Task 2: `LeaveExceptionGrant` model + admin

**Files:**
- Modify: `hr/models/leave.py` (add new model, e.g. directly after `LeaveEntitlement`, before `LeaveRecord` at line 72)
- Modify: `hr/models/__init__.py:3-16`
- Modify: `hr/admin.py`
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `hr.models.Employee`, `hr.models.LeaveType`.
- Produces: `LeaveExceptionGrant(employee, leave_type, year, days, granted_by, granted_at, reason)`, importable from `hr.models`.

- [x] **Step 1: Write the failing test**

```python
class LeaveExceptionGrantTests(TestCase):
    def setUp(self):
        self.emp = Employee.objects.create(full_name='Grant Test', work_location='site', is_active=True)
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('45'))
        self.hr_user = User.objects.create_user(username='hr1', password='x', is_super_admin_user=True)

    def test_grant_is_recorded_with_audit_fields(self):
        grant = LeaveExceptionGrant.objects.create(
            employee=self.emp, leave_type=self.lt, year=2026, days=Decimal('5'),
            granted_by=self.hr_user, reason='Worked through Eid holidays.')
        self.assertEqual(grant.days, Decimal('5'))
        self.assertEqual(grant.granted_by, self.hr_user)
        self.assertIsNotNone(grant.granted_at)
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LeaveExceptionGrantTests -v 2`
Expected: FAIL — `LeaveExceptionGrant` does not exist.

- [x] **Step 3: Implement**

In `hr/models/leave.py`, add:

```python
class LeaveExceptionGrant(models.Model):
    """One HR-granted addition to an employee's standard entitlement for a
    year — an audit log (one row per grant action), not a single
    overwritable counter, so multiple grants across a year each keep their
    own reason/date. LeaveEntitlement.exception_days sums these rather than
    storing a redundant total."""
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_exception_grants')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='exception_grants')
    year = models.PositiveIntegerField()
    days = models.DecimalField(max_digits=5, decimal_places=1)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    granted_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()

    class Meta:
        ordering = ['-granted_at']

    def __str__(self):
        return f"{self.employee.full_name} +{self.days} {self.leave_type.name} ({self.year})"
```

Update `hr/models/__init__.py`:
```python
from .leave import (LeaveType, LeaveEntitlement, LeaveRecord, LeaveExceptionGrant,
                    LeaveDashboardAccess, LeaveRequest, LeaveRequestApproval, LeaveRequestNote,
                    OverrideAccessSettings, OverrideAccessRole, OverrideAccessEmployee)
```
and add `'LeaveExceptionGrant'` to `__all__`.

Add to `hr/admin.py`:
```python
from .models import (Employee, Asset, LeaveType, LeaveEntitlement, LeaveRecord, LeaveExceptionGrant,
                     Holiday, AttendanceSettings, AttendanceRecord, WorkingDay, WFHRecord)

@admin.register(LeaveExceptionGrant)
class LeaveExceptionGrantAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'year', 'days', 'granted_by', 'granted_at']
    list_filter = ['year', 'leave_type']
    search_fields = ['employee__full_name', 'reason']
    readonly_fields = ['granted_at']
```

- [x] **Step 4: Generate and apply the migration**

Run: `venv\Scripts\python.exe manage.py makemigrations hr` then `venv\Scripts\python.exe manage.py migrate hr`

- [x] **Step 5: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LeaveExceptionGrantTests -v 2`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add hr/models/leave.py hr/models/__init__.py hr/admin.py hr/migrations/ hr/tests.py
git commit -m "hr: add LeaveExceptionGrant audit model"
```

---

### Task 3: `LeaveEntitlement` computed exception properties + `LeaveRequest.exceeds_balance`

**Files:**
- Modify: `hr/models/leave.py:43-69` (`LeaveEntitlement`), `hr/models/leave.py:195-224` (`LeaveRequest`)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `LeaveExceptionGrant` (Task 2).
- Produces: `LeaveEntitlement.exception_days` (property), `LeaveEntitlement.effective_entitled_days` (property), `LeaveEntitlement.effective_remaining_days` (property), `LeaveRequest.exceeds_balance` (field, default `False`).

- [x] **Step 1: Write the failing test**

```python
class LeaveEntitlementEffectiveDaysTests(TestCase):
    def setUp(self):
        self.emp = Employee.objects.create(full_name='Eff Test', work_location='site', is_active=True)
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('45'))
        self.ent = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))

    def test_no_grants_effective_equals_baseline(self):
        self.assertEqual(self.ent.exception_days, Decimal('0'))
        self.assertEqual(self.ent.effective_entitled_days, Decimal('45'))
        self.assertEqual(self.ent.effective_remaining_days, Decimal('45'))

    def test_grants_sum_into_effective_but_not_baseline(self):
        LeaveExceptionGrant.objects.create(employee=self.emp, leave_type=self.lt, year=2026, days=Decimal('5'), reason='x')
        LeaveExceptionGrant.objects.create(employee=self.emp, leave_type=self.lt, year=2026, days=Decimal('2'), reason='y')
        self.assertEqual(self.ent.entitled_days, Decimal('45'))  # baseline untouched
        self.assertEqual(self.ent.exception_days, Decimal('7'))
        self.assertEqual(self.ent.effective_entitled_days, Decimal('52'))
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LeaveEntitlementEffectiveDaysTests -v 2`
Expected: FAIL — properties don't exist.

- [x] **Step 3: Implement**

Add to `LeaveEntitlement` (after the existing `remaining_days` property):

```python
    @property
    def exception_days(self):
        from decimal import Decimal
        agg = self.employee.leave_exception_grants.filter(
            leave_type=self.leave_type, year=self.year,
        ).aggregate(models.Sum('days'))
        return agg['days__sum'] or Decimal('0')

    @property
    def effective_entitled_days(self):
        return self.entitled_days + self.exception_days

    @property
    def effective_remaining_days(self):
        return self.effective_entitled_days - self.taken_days
```

Add to `LeaveRequest` (alongside the other flags, e.g. next to `is_overridden`):
```python
    exceeds_balance = models.BooleanField(
        default=False,
        help_text='True if this request was held (not hard-blocked) because it exceeds the '
                   "employee's effective balance — only possible for work locations where balance "
                   'holding is enabled. Requires a Super Admin override to approve.')
```

- [x] **Step 4: Generate and apply the migration**

Run: `venv\Scripts\python.exe manage.py makemigrations hr` then `venv\Scripts\python.exe manage.py migrate hr`

- [x] **Step 5: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LeaveEntitlementEffectiveDaysTests -v 2`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add hr/models/leave.py hr/migrations/ hr/tests.py
git commit -m "hr: computed effective-entitlement properties + LeaveRequest.exceeds_balance"
```

---

### Task 4: `OverrideAccessSettings` balance-hold location toggles

**Files:**
- Modify: `hr/models/leave.py:139-165` (`OverrideAccessSettings`)
- Test: `hr/tests.py`

**Interfaces:**
- Produces: `OverrideAccessSettings.allow_site_balance_hold` (bool, default `True`), `OverrideAccessSettings.allow_office_balance_hold` (bool, default `False`), `OverrideAccessSettings.balance_hold_enabled_for(work_location)`.

- [x] **Step 1: Write the failing test**

```python
class OverrideAccessSettingsBalanceHoldTests(TestCase):
    def test_defaults_site_enabled_office_disabled(self):
        config = OverrideAccessSettings.get_solo()
        self.assertTrue(config.balance_hold_enabled_for('site'))
        self.assertFalse(config.balance_hold_enabled_for('office'))

    def test_office_can_be_enabled_without_code_change(self):
        config = OverrideAccessSettings.get_solo()
        config.allow_office_balance_hold = True
        config.save()
        self.assertTrue(OverrideAccessSettings.get_solo().balance_hold_enabled_for('office'))
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.OverrideAccessSettingsBalanceHoldTests -v 2`
Expected: FAIL.

- [x] **Step 3: Implement**

Add fields to `OverrideAccessSettings` (after `mode`, before `updated_at`):
```python
    allow_site_balance_hold = models.BooleanField(
        default=True,
        help_text='Site employees who submit leave exceeding their available balance get a held, '
                   'reviewable request instead of a hard block.')
    allow_office_balance_hold = models.BooleanField(
        default=False,
        help_text='Same as above, for Office employees. Off by default — Office keeps the plain hard block.')
```
Add method:
```python
    def balance_hold_enabled_for(self, work_location):
        return self.allow_site_balance_hold if work_location == 'site' else self.allow_office_balance_hold
```

- [x] **Step 4: Generate and apply the migration**

Run: `venv\Scripts\python.exe manage.py makemigrations hr` then `venv\Scripts\python.exe manage.py migrate hr`

- [x] **Step 5: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.OverrideAccessSettingsBalanceHoldTests -v 2`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add hr/models/leave.py hr/migrations/ hr/tests.py
git commit -m "hr: add per-location balance-hold toggles to OverrideAccessSettings"
```

---

### Task 5: Location-aware entitlement generation + reapply

**Files:**
- Modify: `hr/leave_services.py:65-92` (`generate_entitlements_for_employee`, `generate_year_entitlements`), `hr/leave_services.py:95-109` (`reapply_leave_type_defaults`)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `LeaveType.default_days_for(work_location)` (Task 1).
- Produces: same function signatures, unchanged callers.

- [x] **Step 1: Write the failing test**

```python
class LocationAwareGenerationTests(TestCase):
    def setUp(self):
        LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'), site_default_annual_days=Decimal('45'))

    def test_generate_picks_baseline_by_location(self):
        from hr.leave_services import generate_entitlements_for_employee
        office_emp = Employee.objects.create(full_name='Office', work_location='office', is_active=True)
        site_emp = Employee.objects.create(full_name='Site', work_location='site', is_active=True)
        generate_entitlements_for_employee(office_emp, 2026)
        generate_entitlements_for_employee(site_emp, 2026)
        self.assertEqual(LeaveEntitlement.objects.get(employee=office_emp).entitled_days, Decimal('30'))
        self.assertEqual(LeaveEntitlement.objects.get(employee=site_emp).entitled_days, Decimal('45'))

    def test_reapply_is_location_aware_and_preserves_exceptions(self):
        from hr.leave_services import generate_entitlements_for_employee, reapply_leave_type_defaults
        site_emp = Employee.objects.create(full_name='Site2', work_location='site', is_active=True)
        generate_entitlements_for_employee(site_emp, 2026)
        lt = LeaveType.objects.get(code='annual')
        LeaveExceptionGrant.objects.create(employee=site_emp, leave_type=lt, year=2026, days=Decimal('5'), reason='x')
        reapply_leave_type_defaults(2026)
        ent = LeaveEntitlement.objects.get(employee=site_emp)
        self.assertEqual(ent.entitled_days, Decimal('45'))  # still the Site default, not wiped to Office's 30
        self.assertEqual(ent.exception_days, Decimal('5'))  # untouched
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LocationAwareGenerationTests -v 2`
Expected: FAIL — both currently use the flat `lt.default_annual_days` for everyone.

- [x] **Step 3: Implement**

In `hr/leave_services.py`, change `generate_entitlements_for_employee`:
```python
def generate_entitlements_for_employee(employee, year, actor=None):
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
```

Change `reapply_leave_type_defaults`:
```python
def reapply_leave_type_defaults(year=None, leave_type=None):
    from hr.models import LeaveType, LeaveEntitlement
    updated = 0
    leave_types = [leave_type] if leave_type is not None else LeaveType.objects.all()
    for lt in leave_types:
        qs = LeaveEntitlement.objects.filter(leave_type=lt)
        if year is not None:
            qs = qs.filter(year=year)
        for location, days in (('office', lt.default_annual_days),
                                ('site', lt.default_days_for('site'))):
            updated += qs.filter(employee__work_location=location).update(entitled_days=days)
    return updated
```

- [x] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.LocationAwareGenerationTests -v 2`
Expected: PASS.

- [x] **Step 5: Run the full suite (shared code touched)**

Run: `venv\Scripts\python.exe manage.py test`
Expected: same pre-existing failures as before this branch (the unrelated staticfiles-manifest errors), no new failures.

- [x] **Step 6: Commit**

```bash
git add hr/leave_services.py hr/tests.py
git commit -m "hr: location-aware entitlement generation and defaults reapply"
```

---

### Task 6: Effective-cap validation with the hold-vs-block branch

**Files:**
- Modify: `hr/models/leave.py:4-62` (`validate_leave_submission`)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `LeaveEntitlement.effective_remaining_days` (Task 3), `OverrideAccessSettings.balance_hold_enabled_for` (Task 4).
- Produces: `validate_leave_submission(...)` now **returns `bool`** — `True` means "exceeds balance, held" — instead of always returning `None`. Still raises `ValueError` for every existing invariant (no entitlement, overlap, and Office-style hard block).

- [x] **Step 1: Write the failing test**

```python
class ValidateLeaveSubmissionHoldTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'), site_default_annual_days=Decimal('45'))

    def test_within_cap_returns_false(self):
        from hr.leave_services import validate_leave_submission
        emp = Employee.objects.create(full_name='OK', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        held = validate_leave_submission(emp, self.lt, date(2026, 3, 1), date(2026, 3, 5))
        self.assertFalse(held)

    def test_site_over_cap_is_held_not_blocked(self):
        from hr.leave_services import validate_leave_submission
        emp = Employee.objects.create(full_name='Site Over', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        held = validate_leave_submission(emp, self.lt, date(2026, 1, 1), date(2026, 2, 15))  # 46 days
        self.assertTrue(held)

    def test_office_over_cap_still_hard_blocks(self):
        from hr.leave_services import validate_leave_submission
        emp = Employee.objects.create(full_name='Office Over', work_location='office', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        with self.assertRaises(ValueError):
            validate_leave_submission(emp, self.lt, date(2026, 1, 1), date(2026, 2, 1))  # 32 days

    def test_site_within_effective_cap_via_exception_grant_returns_false(self):
        from hr.leave_services import validate_leave_submission
        emp = Employee.objects.create(full_name='Site Grant', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        LeaveExceptionGrant.objects.create(employee=emp, leave_type=self.lt, year=2026, days=Decimal('5'), reason='x')
        held = validate_leave_submission(emp, self.lt, date(2026, 1, 1), date(2026, 2, 19))  # 50 days
        self.assertFalse(held)
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.ValidateLeaveSubmissionHoldTests -v 2`
Expected: FAIL — today it always raises past 45/30 regardless of location, and never returns `True`.

- [x] **Step 3: Implement**

Replace the balance-check block in `validate_leave_submission` (currently the `if requested_days > available_days:` block that always raises) with:

```python
    requested_days = Decimal((end_date - start_date).days + 1)
    pending_days = sum(
        (r.days or Decimal('0') for r in LeaveRequest.objects.filter(
            employee=employee, leave_type=leave_type, status='pending', start_date__year=start_date.year)),
        Decimal('0'))
    available_days = entitlement.effective_remaining_days - pending_days

    exceeds_balance = False
    if requested_days > available_days:
        from hr.models import OverrideAccessSettings
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
```

(The overlap checks below stay exactly as they are.) Change the function's final line from implicit `None` to `return exceeds_balance`, and initialize `exceeds_balance = False` before the overlap checks so it's returned even when the balance check passed cleanly:

```python
    if LeaveRequest.objects.filter(
            employee=employee, status='pending',
            start_date__lte=end_date, end_date__gte=start_date).exists():
        raise ValueError(...)
    if LeaveRecord.objects.filter(...).exists():
        raise ValueError(...)
    return exceeds_balance
```

- [x] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.ValidateLeaveSubmissionHoldTests -v 2`
Expected: PASS (4/4).

- [x] **Step 5: Run the full suite (this function is the shared validation path for every leave submission)**

Run: `venv\Scripts\python.exe manage.py test`
Expected: no new failures beyond the pre-existing unrelated staticfiles ones.

- [x] **Step 6: Commit**

```bash
git add hr/models/leave.py hr/tests.py
git commit -m "hr: validate_leave_submission holds over-cap Site requests instead of blocking"
```

---

### Task 7: `submit_leave_request` skips approver roster for held requests

**Files:**
- Modify: `hr/leave_approval_services.py:177-222` (`submit_leave_request`)
- Modify: `hr/forms.py:413-431` (`check_leave_balance` — must not swallow the return value's meaning)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `validate_leave_submission` now returning `bool` (Task 6).
- Produces: `submit_leave_request(...)` — same signature and return type (`LeaveRequest`), but sets `exceeds_balance` on the created row and skips creating `LeaveRequestApproval` rows when held.

- [x] **Step 1: Write the failing test**

```python
class SubmitLeaveRequestHeldTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'), site_default_annual_days=Decimal('45'))
        self.approver_user = User.objects.create_user(username='appr', password='x')
        LeaveDashboardAccess.objects.create(user=self.approver_user, is_active=True)

    def test_held_request_has_no_approval_rows(self):
        from hr.leave_approval_services import submit_leave_request
        emp = Employee.objects.create(full_name='Site Over', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        req = submit_leave_request(
            employee=emp, leave_type=self.lt, start_date=date(2026, 1, 1), end_date=date(2026, 2, 15),
            created_by=self.approver_user)
        self.assertTrue(req.exceeds_balance)
        self.assertEqual(req.approvals.count(), 0)
        self.assertEqual(req.status, 'pending')

    def test_normal_request_still_gets_approval_rows(self):
        from hr.leave_approval_services import submit_leave_request
        emp = Employee.objects.create(full_name='Site OK', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        req = submit_leave_request(
            employee=emp, leave_type=self.lt, start_date=date(2026, 3, 1), end_date=date(2026, 3, 5),
            created_by=self.approver_user)
        self.assertFalse(req.exceeds_balance)
        self.assertEqual(req.approvals.count(), 1)
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.SubmitLeaveRequestHeldTests -v 2`
Expected: FAIL — `exceeds_balance` never gets set True and approval rows are always created.

- [x] **Step 3: Implement**

In `hr/leave_approval_services.py`, change the `with transaction.atomic():` block:

```python
    with transaction.atomic():
        exceeds_balance = validate_leave_submission(employee, leave_type, start_date, end_date, lock=True)
        leave_request = LeaveRequest.objects.create(
            employee=employee, leave_type=leave_type, start_date=start_date, end_date=end_date,
            employee_reason=employee_reason, document=document, created_by=created_by,
            exceeds_balance=exceeds_balance,
        )
        if not exceeds_balance:
            approvers = LeaveDashboardAccess.objects.filter(is_active=True)
            if employee.user_id:
                approvers = approvers.exclude(user_id=employee.user_id)
            for grant in approvers:
                LeaveRequestApproval.objects.create(leave_request=leave_request, approver=grant.user)
```

Update the notification message right after (still inside `if employee.user_id:`) to distinguish the two cases:
```python
    if employee.user_id:
        from notifications.services import notify_users
        verb = ('Your leave request was submitted — it exceeds your available balance and needs Super '
                'Admin review' if exceeds_balance else
                'Your leave request was submitted and is pending approval')
        notify_users(recipients=[employee.user], verb=verb, actor=created_by)
```

No change needed in `hr/forms.py`'s `check_leave_balance` — it already just calls `validate_leave_submission` and only reacts to a raised `ValueError`; a `True` return (held, not raised) now correctly passes form validation without any edit there. Confirm this by reading `hr/forms.py:413-431` — no code change, just verifying the existing call site doesn't need one.

- [x] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.SubmitLeaveRequestHeldTests -v 2`
Expected: PASS.

- [x] **Step 5: Run the full suite**

Run: `venv\Scripts\python.exe manage.py test`
Expected: no new failures.

- [x] **Step 6: Commit**

```bash
git add hr/leave_approval_services.py hr/tests.py
git commit -m "hr: submit_leave_request holds over-cap requests with no approver roster"
```

---

### Task 8: HR "Add Exception Days" action + work-location transfer recompute

**Files:**
- Modify: `hr/leave_approval_services.py` (add `grant_exception_days`)
- Modify: `hr/views.py:619-627` (`EmployeeUpdateView.form_valid`) and add a new `EmployeeGrantExceptionDaysView`
- Modify: `hr/forms.py` (add `ExceptionGrantForm`)
- Modify: `hr/urls.py` (new route)
- Create: `templates/hr/exception_grant_form.html`
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `has_override_access` (`hr/views.py:75`), `LeaveExceptionGrant` (Task 2), `LeaveType.default_days_for` (Task 1).
- Produces: `grant_exception_days(*, employee, leave_type, year, days, granted_by, reason) -> LeaveExceptionGrant`.

- [x] **Step 1: Write the failing test**

```python
class GrantExceptionDaysTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'), site_default_annual_days=Decimal('45'))
        self.emp = Employee.objects.create(full_name='Grantee', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        self.hr_user = User.objects.create_user(username='hrgrant', password='x', is_super_admin_user=True)

    def test_grant_increases_effective_but_not_baseline(self):
        from hr.leave_approval_services import grant_exception_days
        grant_exception_days(employee=self.emp, leave_type=self.lt, year=2026, days=Decimal('5'),
                              granted_by=self.hr_user, reason='Eid overtime.')
        ent = LeaveEntitlement.objects.get(employee=self.emp, leave_type=self.lt, year=2026)
        self.assertEqual(ent.entitled_days, Decimal('45'))
        self.assertEqual(ent.exception_days, Decimal('5'))

    def test_grant_requires_a_reason(self):
        from hr.leave_approval_services import grant_exception_days
        with self.assertRaises(ValueError):
            grant_exception_days(employee=self.emp, leave_type=self.lt, year=2026, days=Decimal('5'),
                                  granted_by=self.hr_user, reason='   ')


class EmployeeWorkLocationTransferTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'), site_default_annual_days=Decimal('45'))

    def test_upgrade_recomputes_baseline_for_whole_year(self):
        emp = Employee.objects.create(full_name='Upgrade', work_location='office', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        LeaveRecord.objects.create(employee=emp, leave_type=self.lt, start_date=date(2026, 2, 1), end_date=date(2026, 2, 10))  # 10 days taken
        from hr.leave_services import apply_work_location_transfer
        apply_work_location_transfer(emp, old_location='office', new_location='site', year=2026, actor=None)
        ent = LeaveEntitlement.objects.get(employee=emp, leave_type=self.lt, year=2026)
        self.assertEqual(ent.entitled_days, Decimal('45'))

    def test_downgrade_auto_grants_the_shortfall(self):
        emp = Employee.objects.create(full_name='Downgrade', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        LeaveRecord.objects.create(employee=emp, leave_type=self.lt, start_date=date(2026, 1, 1), end_date=date(2026, 2, 4))  # 35 days taken
        from hr.leave_services import apply_work_location_transfer
        apply_work_location_transfer(emp, old_location='site', new_location='office', year=2026, actor=None)
        ent = LeaveEntitlement.objects.get(employee=emp, leave_type=self.lt, year=2026)
        self.assertEqual(ent.entitled_days, Decimal('30'))  # baseline drops to Office
        self.assertEqual(ent.exception_days, Decimal('5'))  # 35 taken - 30 new baseline, auto-preserved
        self.assertEqual(ent.effective_remaining_days, Decimal('0'))
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.GrantExceptionDaysTests hr.tests.EmployeeWorkLocationTransferTests -v 2`
Expected: FAIL — neither function exists yet.

- [x] **Step 3: Implement**

Add to `hr/leave_approval_services.py`:
```python
def grant_exception_days(*, employee, leave_type, year, days, granted_by, reason):
    if not reason or not reason.strip():
        raise ValueError('An exception grant requires a written reason.')
    from hr.models import LeaveExceptionGrant
    return LeaveExceptionGrant.objects.create(
        employee=employee, leave_type=leave_type, year=year, days=days,
        granted_by=granted_by, reason=reason.strip())
```

Add to `hr/leave_services.py`:
```python
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
                reason=f'Auto-preserved {shortfall} day(s) already taken under the {old_location} '
                       f'baseline before transfer to {new_location}.')
```

In `hr/views.py`, change `EmployeeUpdateView.form_valid` (line 625-627) to detect the change:
```python
    def form_valid(self, form):
        old_location = Employee.objects.filter(pk=self.object.pk).values_list('work_location', flat=True).first()
        response = super().form_valid(form)
        new_location = self.object.work_location
        if old_location and old_location != new_location:
            from hr.leave_services import apply_work_location_transfer
            apply_work_location_transfer(
                self.object, old_location=old_location, new_location=new_location,
                year=timezone.now().year, actor=self.request.user)
        messages.success(self.request, 'Employee updated successfully.')
        return response
```

Add `ExceptionGrantForm` to `hr/forms.py`:
```python
class ExceptionGrantForm(forms.Form):
    leave_type = forms.ModelChoiceField(queryset=LeaveType.objects.filter(is_active=True), widget=forms.Select(attrs={'class': 'form-select'}))
    year = forms.IntegerField(widget=forms.NumberInput(attrs={'class': 'form-control'}))
    days = forms.DecimalField(min_value=Decimal('0.5'), widget=forms.NumberInput(attrs={'class': 'form-control'}))
    reason = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
```

Add view to `hr/views.py` (near `EmployeeUpdateView`):
```python
class EmployeeGrantExceptionDaysView(SuperAdminRequiredMixin, FormView):
    form_class = ExceptionGrantForm
    template_name = 'hr/exception_grant_form.html'

    def test_func(self):
        return has_override_access(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        self.employee = get_object_or_404(Employee, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {'year': timezone.now().year}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['employee'] = self.employee
        return ctx

    def form_valid(self, form):
        from hr.leave_approval_services import grant_exception_days
        grant_exception_days(
            employee=self.employee, leave_type=form.cleaned_data['leave_type'], year=form.cleaned_data['year'],
            days=form.cleaned_data['days'], granted_by=self.request.user, reason=form.cleaned_data['reason'])
        messages.success(self.request, f'Granted {form.cleaned_data["days"]} exception day(s) to {self.employee.full_name}.')
        return redirect('hr:leave_summary', pk=self.employee.pk)
```

Add route to `hr/urls.py` (near `leave_summary`, line 74):
```python
    path('<int:pk>/leave/grant-exception/', views.EmployeeGrantExceptionDaysView.as_view(), name='grant_exception_days'),
```

Create `templates/hr/exception_grant_form.html`:
```html
{% extends 'base.html' %}
{% block title %}Add Exception Days{% endblock %}
{% block content %}
<div class="container-fluid">
  <div class="row justify-content-center">
    <div class="col-lg-6">
      <h1 class="h3 mb-4">Add Exception Days — {{ employee.full_name }}</h1>
      <div class="card"><div class="card-body">
        <form method="post" novalidate>
          {% csrf_token %}
          {% if form.non_field_errors %}<div class="alert alert-danger">{{ form.non_field_errors }}</div>{% endif %}
          <div class="mb-3"><label class="form-label">Leave Type *</label>{{ form.leave_type }}</div>
          <div class="mb-3"><label class="form-label">Year *</label>{{ form.year }}</div>
          <div class="mb-3"><label class="form-label">Extra Days *</label>{{ form.days }}</div>
          <div class="mb-4"><label class="form-label">Reason *</label>{{ form.reason }}
            {% if form.reason.errors %}<div class="invalid-feedback d-block">{{ form.reason.errors.0 }}</div>{% endif %}</div>
          <div class="d-flex justify-content-end gap-2 border-top pt-3">
            <a href="{% url 'hr:leave_summary' employee.pk %}" class="btn btn-outline-secondary">Cancel</a>
            <button type="submit" class="btn btn-primary"><i class="bi bi-plus-lg"></i> Grant Days</button>
          </div>
        </form>
      </div></div>
    </div>
  </div>
</div>
{% endblock %}
```

- [x] **Step 4: Generate and apply the migration (none expected — this task adds no fields)**

Run: `venv\Scripts\python.exe manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [x] **Step 5: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.GrantExceptionDaysTests hr.tests.EmployeeWorkLocationTransferTests -v 2`
Expected: PASS.

- [x] **Step 6: Run the full suite**

Run: `venv\Scripts\python.exe manage.py test`
Expected: no new failures.

- [x] **Step 7: Commit**

```bash
git add hr/leave_approval_services.py hr/leave_services.py hr/views.py hr/forms.py hr/urls.py templates/hr/exception_grant_form.html hr/tests.py
git commit -m "hr: HR-granted exception days action + work-location transfer recompute"
```

---

### Task 9: Held-request visibility — red badge on the Leave Requests queue/detail pages

**Files:**
- Modify: `templates/hr/leave_request_list.html`, `templates/hr/leave_request_detail.html`
- Test: `hr/tests.py` (view-level assertion on rendered context, not markup-fragile HTML parsing)

**Interfaces:**
- Consumes: `LeaveRequest.exceeds_balance` (Task 3), `LeaveEntitlement.effective_remaining_days`/`exception_days`/`entitled_days`/`taken_days` (Task 3).

- [x] **Step 1: Write the failing test**

```python
class HeldRequestDisplayTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'), site_default_annual_days=Decimal('45'))
        self.emp = Employee.objects.create(full_name='Held Display', work_location='site', is_active=True)
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        self.hr_user = User.objects.create_user(username='hrview', password='x', is_super_admin_user=True)
        self.client.login(username='hrview', password='x')

    def test_list_page_shows_exceeds_balance_badge(self):
        from hr.leave_approval_services import submit_leave_request
        req = submit_leave_request(employee=self.emp, leave_type=self.lt, start_date=date(2026, 1, 1),
                                    end_date=date(2026, 2, 15), created_by=self.hr_user)
        resp = self.client.get(reverse('hr:leave_request_list'))
        self.assertContains(resp, 'Exceeds balance')

    def test_detail_page_shows_breakdown(self):
        from hr.leave_approval_services import submit_leave_request
        req = submit_leave_request(employee=self.emp, leave_type=self.lt, start_date=date(2026, 1, 1),
                                    end_date=date(2026, 2, 15), created_by=self.hr_user)
        resp = self.client.get(reverse('hr:leave_request_detail', args=[req.pk]))
        self.assertContains(resp, 'Exceeds balance')
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.HeldRequestDisplayTests -v 2`
Expected: FAIL — neither template mentions "Exceeds balance" yet.

- [x] **Step 3: Implement**

In `templates/hr/leave_request_list.html`, find the row loop for `pending_requests` and add, right after the employee/leave-type cell (inspect the existing `<td>` structure first and match its style — the addition is a small badge):
```html
{% if request_obj.exceeds_balance %}
<span class="badge" style="background: var(--leap-red); color:#fff;">Exceeds balance</span>
{% endif %}
```
(Use whatever the loop variable is actually named in that template — confirm with a read before editing; it is not necessarily `request_obj`.)

In `templates/hr/leave_request_detail.html`, add near the top of the detail card:
```html
{% if leave_request.exceeds_balance %}
<div class="alert alert-danger">
  <strong>Exceeds balance</strong> — this request goes over {{ leave_request.employee.full_name }}'s
  available balance for {{ leave_request.leave_type.name }} ({{ leave_request.start_date|date:"Y" }}).
  No normal approver can decide this; only a Super Admin Override can approve or reject it.
</div>
{% endif %}
```

- [x] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.HeldRequestDisplayTests -v 2`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add templates/hr/leave_request_list.html templates/hr/leave_request_detail.html hr/tests.py
git commit -m "hr: show an Exceeds Balance badge on held leave requests"
```

---

### Task 10: Employee dashboard — anchor to baseline, show exception line separately

**Files:**
- Modify: `templates/hr/leave_summary.html:49-52` (or nearby), `templates/hr/entitlement_year.html`
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `LeaveEntitlement.exception_days`/`effective_remaining_days` (Task 3).

- [x] **Step 1: Write the failing test**

```python
class EmployeeDashboardExceptionDisplayTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'), site_default_annual_days=Decimal('45'))
        self.emp = Employee.objects.create(full_name='Dashboard Test', work_location='site', is_active=True)
        self.ent = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        LeaveExceptionGrant.objects.create(employee=self.emp, leave_type=self.lt, year=2026, days=Decimal('5'), reason='x')
        self.hr_user = User.objects.create_user(username='hrdash', password='x', is_super_admin_user=True)
        self.client.login(username='hrdash', password='x')

    def test_leave_summary_shows_baseline_and_exception_separately(self):
        resp = self.client.get(reverse('hr:leave_summary', args=[self.emp.pk]) + '?year=2026')
        self.assertContains(resp, '45')  # baseline anchor
        self.assertContains(resp, '+5')  # exception line, not blended into 50
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.EmployeeDashboardExceptionDisplayTests -v 2`
Expected: FAIL.

- [x] **Step 3: Implement**

Read `templates/hr/leave_summary.html` in full first to match its existing table structure exactly (it iterates `entitlements` — confirm the loop variable name), then add, inside the same row as the existing `entitled_days`/`taken_days`/`remaining_days` cells:
```html
{% if e.exception_days %}
<tr class="table-light">
  <td colspan="2" class="ps-4 text-muted">+ {{ e.exception_days }} exception days granted</td>
  <td class="text-end">{{ e.effective_remaining_days }} available</td>
</tr>
{% endif %}
```
Adjust the exact `<td>` count/colspan to match the real table's column count (confirmed by reading the file first — do not guess the column count blind).

- [x] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.EmployeeDashboardExceptionDisplayTests -v 2`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add templates/hr/leave_summary.html hr/tests.py
git commit -m "hr: show exception-day grants as a separate line, baseline stays the anchor"
```

---

### Task 11: Sidebar pending-count context processor

**Files:**
- Create: `hr/context_processors.py`
- Modify: `erp_leap/settings.py` (`TEMPLATES[0]['OPTIONS']['context_processors']`)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `can_view_team_exceptions`, `TeamExceptionsView._tab_queryset` pattern (`hr/views.py:2413-2481`), `can_view_leave_dashboard` (`hr/views.py:93`).
- Produces: template context keys `leave_requests_pending_count`, `team_exceptions_pending_count`, `team_exceptions_direct_count`, `team_exceptions_secondary_count`, `team_exceptions_all_count` (present only for users who qualify; absent — not zero — for everyone else, so `{% if %}` in the template naturally hides the dot for users who can't see these pages at all).

- [x] **Step 1: Write the failing test**

```python
class PendingCountsContextProcessorTests(TestCase):
    def setUp(self):
        self.lt = LeaveType.objects.create(code='annual', name='Annual', default_annual_days=Decimal('30'))
        self.hr_user = User.objects.create_user(username='hrctx', password='x', is_super_admin_user=True)
        LeaveDashboardAccess.objects.create(user=self.hr_user, is_active=True)
        self.client.login(username='hrctx', password='x')

    def test_leave_requests_pending_count_reflects_actual_pending(self):
        emp = Employee.objects.create(full_name='Ctx Test', work_location='office', is_active=True)
        LeaveEntitlement.objects.create(employee=emp, leave_type=self.lt, year=timezone.now().year, entitled_days=Decimal('30'))
        from hr.leave_approval_services import submit_leave_request
        submit_leave_request(employee=emp, leave_type=self.lt, start_date=date.today(), end_date=date.today(), created_by=self.hr_user)
        resp = self.client.get(reverse('hr:hr_dashboard'))
        self.assertEqual(resp.context['leave_requests_pending_count'], 1)

    def test_plain_employee_gets_no_counts_in_context(self):
        plain_user = User.objects.create_user(username='plainctx', password='x')
        self.client.logout()
        self.client.login(username='plainctx', password='x')
        resp = self.client.get(reverse('hr:hr_dashboard'))
        self.assertNotIn('leave_requests_pending_count', resp.context)
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.PendingCountsContextProcessorTests -v 2`
Expected: FAIL — context processor doesn't exist, keys absent for everyone.

- [x] **Step 3: Implement**

Create `hr/context_processors.py`:
```python
"""Sidebar pending-count badges — Leave Requests and Team Exceptions.
Reuses the exact querysets the real pages themselves filter by, so a badge
number can never drift from what the page shows when you open it."""
from hr.models import LeaveRequest, AttendanceException


def pending_counts(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    ctx = {}
    from hr.views import can_view_leave_dashboard, can_view_team_exceptions, _is_head_manager_role
    if can_view_leave_dashboard(user):
        ctx['leave_requests_pending_count'] = LeaveRequest.objects.filter(status='pending').count()
    if can_view_team_exceptions(user):
        emp = getattr(user, 'employee_profile', None)
        is_hr = bool(user.is_super_admin_user or user.is_admin_user)
        direct = AttendanceException.objects.filter(status__in=('pending', 'expired'))
        secondary = AttendanceException.objects.filter(status__in=('pending', 'expired'))
        if emp:
            direct = direct.filter(employee__main_manager=emp).exclude(employee__user=user)
            secondary = secondary.filter(employee__secondary_managers=emp).exclude(employee__user=user)
        else:
            direct = direct.none()
            secondary = secondary.none()
        ctx['team_exceptions_direct_count'] = direct.count()
        ctx['team_exceptions_secondary_count'] = secondary.count()
        total = ctx['team_exceptions_direct_count'] + ctx['team_exceptions_secondary_count']
        if is_hr:
            ctx['team_exceptions_all_count'] = AttendanceException.objects.filter(status__in=('pending', 'expired')).count()
            total = ctx['team_exceptions_all_count']  # HR's dot reflects the org-wide tab, the broadest view they have
        ctx['team_exceptions_pending_count'] = total
    return ctx
```

In `erp_leap/settings.py`, find `TEMPLATES` (`context_processors` list under `django.template.context_processors.*`) and add `'hr.context_processors.pending_counts',` to that list.

- [x] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.PendingCountsContextProcessorTests -v 2`
Expected: PASS.

- [x] **Step 5: Run the full suite (settings.py + a global context processor touches every page)**

Run: `venv\Scripts\python.exe manage.py test`
Expected: no new failures.

- [x] **Step 6: Commit**

```bash
git add hr/context_processors.py erp_leap/settings.py hr/tests.py
git commit -m "hr: add pending-count context processor for sidebar badges"
```

---

### Task 12: Sidebar badges — CSS + markup on Leave/My Profile parents and children

**Files:**
- Modify: `templates/base.html:406-422` (new CSS near `.notif-badge`), `templates/base.html:858-883` (My Profile block), `templates/base.html:1241-1272` (Leave block)
- Test: manual verification only (pure template markup driven by the already-tested context processor — no new Python behavior to unit test)

**Interfaces:**
- Consumes: `leave_requests_pending_count`, `team_exceptions_pending_count` (Task 11).

- [x] **Step 1: Add CSS**

In `templates/base.html`, right after the `.notif-badge.show { display: block; }` rule (line 422), add:
```css
        .nav-badge-count {
            background: var(--leap-red);
            color: #fff;
            font-size: 0.65rem;
            font-weight: 700;
            min-width: 18px;
            height: 18px;
            line-height: 18px;
            text-align: center;
            border-radius: 9px;
            padding: 0 5px;
            margin-left: auto;
        }
        .nav-badge-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--leap-red);
            display: inline-block;
            margin-left: 6px;
        }
```

- [x] **Step 2: Add the child-level count badges**

In the "Leave" submenu (`templates/base.html:1249-1253`), change the "Leave Requests" link to:
```html
                        <li class="nav-item">
                            <a class="nav-link py-1 d-flex justify-content-between align-items-center {% if 'leave_request' in request.resolver_match.url_name %}active{% endif %}" href="{% url 'hr:leave_request_list' %}">
                                <span><i class="bi bi-inboxes"></i> <span>Leave Requests</span></span>
                                {% if leave_requests_pending_count %}<span class="nav-badge-count">{{ leave_requests_pending_count }}</span>{% endif %}
                            </a>
                        </li>
```

In the "My Profile" submenu (`templates/base.html:870-874`), change the "Team Exceptions" link to:
```html
                        <li class="nav-item">
                            <a class="nav-link py-1 d-flex justify-content-between align-items-center {% if 'team_exceptions' in request.resolver_match.url_name %}active{% endif %}" href="{% url 'hr:team_exceptions' %}">
                                <span><i class="bi bi-geo-alt"></i> <span>Team Exceptions</span></span>
                                {% if team_exceptions_pending_count %}<span class="nav-badge-count">{{ team_exceptions_pending_count }}</span>{% endif %}
                            </a>
                        </li>
```

- [x] **Step 3: Add the parent-level dots**

In the "Leave" parent link (`templates/base.html:1242-1244`), change:
```html
                <a class="nav-link d-flex justify-content-between align-items-center" data-bs-toggle="collapse" href="#leaveSubmenu" role="button" aria-expanded="...">
                    <span><i class="bi bi-calendar2-check"></i> <span>Leave</span>{% if leave_requests_pending_count %}<span class="nav-badge-dot"></span>{% endif %}</span>
                    <i class="bi bi-chevron-down" style="font-size:0.7rem;"></i>
                </a>
```

In the "My Profile" parent link (`templates/base.html:858-862`), change:
```html
                <a class="nav-link d-flex justify-content-between align-items-center ..." data-bs-toggle="collapse" href="#myProfileSubmenu" role="button" aria-expanded="...">
                    <span><i class="bi bi-person-badge"></i> <span>My Profile</span>{% if team_exceptions_pending_count %}<span class="nav-badge-dot"></span>{% endif %}</span>
                    <i class="bi bi-chevron-down" style="font-size:0.7rem;"></i>
                </a>
```
(Preserve the rest of each `<a>` tag's existing classes/conditionals exactly as they are today — only inserting the new `{% if %}` span, not replacing the whole tag. Read the exact current lines before editing since the surrounding conditional classes are long.)

- [x] **Step 4: Manual verification**

Run the dev server, log in as a user with `LeaveDashboardAccess` and a pending held request from Task 7/9's tests (or seed one), confirm: the "Leave" parent shows a small red dot when collapsed, "Leave Requests" shows the numbered pill once expanded, and the same for My Profile/Team Exceptions. Confirm both are absent for a plain employee account with no pending items.

- [x] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "hr: sidebar pending-count dot/badge indicators for Leave and Team Exceptions"
```

---

### Task 13: Team Exceptions tab count badges

**Files:**
- Modify: `hr/views.py:2483-2489` (`TeamExceptionsView.get_context_data`) to expose per-tab counts regardless of the currently-selected tab
- Modify: `templates/hr/team_exceptions.html:126-137` (the 3 tab buttons)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `TeamExceptionsView._tab_queryset` (existing, `hr/views.py:2442-2466`).
- Produces: context keys `direct_tab_count`, `secondary_tab_count`, `all_tab_count` (the last only when `show_all_tab` is true) on `TeamExceptionsView`.

- [x] **Step 1: Write the failing test**

```python
class TeamExceptionsTabCountTests(TestCase):
    def test_tab_buttons_show_pending_counts(self):
        manager_user = User.objects.create_user(username='tabmgr', password='x')
        manager = Employee.objects.create(full_name='Tab Manager', is_active=True, user=manager_user)
        report = Employee.objects.create(full_name='Tab Report', is_active=True, main_manager=manager)
        AttendanceException.objects.create(
            employee=report, main_manager=manager, event_date=date.today(),
            event_start_time=time(9, 0), status='pending', reason='Late')
        self.client.login(username='tabmgr', password='x')
        resp = self.client.get(reverse('hr:team_exceptions'))
        self.assertEqual(resp.context['direct_tab_count'], 1)
```

- [x] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.TeamExceptionsTabCountTests -v 2`
Expected: FAIL — `direct_tab_count` not in context.

- [x] **Step 3: Implement**

In `TeamExceptionsView.get_context_data` (`hr/views.py`, right after `is_hr = bool(...)` at line 2488), add:
```python
        ctx['direct_tab_count'] = self._tab_queryset('direct', user, emp).filter(status__in=('pending', 'expired')).count()
        ctx['secondary_tab_count'] = self._tab_queryset('secondary', user, emp).filter(status__in=('pending', 'expired')).count()
        if ctx.get('show_all_tab'):
            ctx['all_tab_count'] = self._tab_queryset('all', user, emp).filter(status__in=('pending', 'expired')).count()
```
(Confirm the exact existing name/placement of `show_all_tab` in this method by reading the surrounding lines first — it's referenced in the template per line 133 of `team_exceptions.html`, so it already exists somewhere in this method; add the three new lines right after it's set, not before.)

In `templates/hr/team_exceptions.html`, update the 3 tab buttons:
```html
    <li class="nav-item">
      <a class="nav-link team-exc-tab-direct {% if tab == 'direct' %}active{% endif %}" href="?tab=direct">Direct Reports{% if direct_tab_count %} <span class="nav-badge-count">{{ direct_tab_count }}</span>{% endif %}</a>
    </li>
    <li class="nav-item">
      <a class="nav-link team-exc-tab-secondary {% if tab == 'secondary' %}active{% endif %}" href="?tab=secondary">Secondary Reports{% if secondary_tab_count %} <span class="nav-badge-count">{{ secondary_tab_count }}</span>{% endif %}</a>
    </li>
    {% if show_all_tab %}
    <li class="nav-item">
      <a class="nav-link team-exc-tab-all {% if tab == 'all' %}active{% endif %}" href="?tab=all">All Organization Requests{% if all_tab_count %} <span class="nav-badge-count">{{ all_tab_count }}</span>{% endif %}</a>
    </li>
    {% endif %}
```

- [x] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.TeamExceptionsTabCountTests -v 2`
Expected: PASS.

- [x] **Step 5: Run the full suite**

Run: `venv\Scripts\python.exe manage.py test`
Expected: no new failures.

- [x] **Step 6: Commit**

```bash
git add hr/views.py templates/hr/team_exceptions.html hr/tests.py
git commit -m "hr: per-tab pending-count badges on Team Exceptions"
```

---

### Task 14: Final whole-branch verification and cleanup

- [x] **Step 1: Run the complete suite one more time**

Run: `venv\Scripts\python.exe manage.py test`
Expected: identical failure set to the branch's starting point (the pre-existing, unrelated staticfiles-manifest errors from PR #22 — `CostingProjectAutofillTests` x2, `PipelineVisibilityTests.test_sales_can_edit_own_project`) — zero new failures.

- [x] **Step 2: Check for stray migration conflicts**

Run: `venv\Scripts\python.exe manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [x] **Step 3: Confirm no stray artifacts**

Run: `git status --short` — expect only the intended modified/new files from Tasks 1-13, nothing else (no debug prints, no scratch files).

- [x] **Step 4: Do not commit further — this task is verification only, report results to the user.**
