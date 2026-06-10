# HR Leave & Daily Attendance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add HR-administered leave entitlements/balances (Leap annual rule 25→30) and a leave/holiday/weekend-aware daily attendance grid to the `hr` app.

**Architecture:** Split `hr/models.py` into an `hr/models/` package; add Leave models (`LeaveType`, `LeaveEntitlement`, `LeaveRecord`) and Attendance models (`Holiday`, `AttendanceSettings`, `AttendanceRecord`). Shared `hr/work_calendar.py` computes working days from a configurable weekend + holiday list. Attendance status is derived (leave > holiday > weekend > present > absent) and stored. All views reuse the existing `AdminRequiredMixin`.

**Tech Stack:** Django, PostgreSQL, Django test runner (`python manage.py test hr`), Bootstrap 5 templates, openpyxl already available.

**Spec:** `docs/superpowers/specs/2026-06-10-hr-leave-attendance-design.md`

**Conventions (verbatim from the existing `hr` app):**
- Access gate: `class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin): def test_func(self): return self.request.user.is_super_admin_user or self.request.user.is_admin_user`
- CBV: `paginate_by = 25`, `template_name = 'hr/<model>_list.html'`, `success_url = reverse_lazy('hr:<name>')`, `messages.success(...)`.
- Forms: Bootstrap widgets `form-control` / `form-select` / `form-check-input`; dates `forms.DateInput(attrs={'class':'form-control','type':'date'})`.
- Templates: `{% extends 'base.html' %}`, `{% block content %}`, error display `<div class="invalid-feedback d-block">{{ form.x.errors.0 }}</div>`.
- URLs: namespace `hr`, e.g. `hr:employee_list`.
- `Employee` key fields: `iqama_number` (unique), `full_name`, `joining_date` (DateField null/blank), `is_active` (default True), `designation`, `deployment`.
- Test runner: `python manage.py test hr -v 2`. `hr/tests.py` does not exist yet — create it.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `hr/models/__init__.py` | Re-export all models (keeps `from hr.models import Employee` working) | new |
| `hr/models/employee.py` | `Employee`, `EmployeeDocument` (moved verbatim) | new (moved) |
| `hr/models/assets.py` | `Asset`, `AssetAssignment`, `Vehicle` (moved verbatim) | new (moved) |
| `hr/models/leave.py` | `LeaveType`, `LeaveEntitlement`, `LeaveRecord` | new |
| `hr/models/attendance.py` | `Holiday`, `AttendanceSettings`, `AttendanceRecord` | new |
| `hr/models.py` | deleted (replaced by package) | delete |
| `hr/work_calendar.py` | weekend/holiday/working-day helpers | new |
| `hr/leave_services.py` | annual-rule + year-entitlement generator | new |
| `hr/forms.py` | add Leave/Holiday/Attendance forms | modify |
| `hr/views.py` | add leave + attendance views | modify |
| `hr/urls.py` | add routes | modify |
| `hr/admin.py` | register new models | modify |
| `templates/hr/*.html` | new list/form/grid/summary templates | new |
| `templates/base.html` | add HR sidebar links (Leave, Attendance) | modify |
| `hr/tests.py` | all tests | new |

---

# PHASE A — Leave + Calendar Foundation

## Task A1: Convert `hr/models.py` into an `hr/models/` package

**Goal:** Move existing models into submodules with ZERO migration churn. This is a pure refactor.

**Files:**
- Create `hr/models/__init__.py`, `hr/models/employee.py`, `hr/models/assets.py`
- Delete `hr/models.py`

- [ ] **Step 1: Read the current file** — `Read hr/models.py` in full. Note the two import lines (`from django.db import models`, `from django.conf import settings`) and the class boundaries: `Employee` (≈5-100), `EmployeeDocument` (≈102-159), `Asset` (≈161-249), `AssetAssignment` (≈251-312), `Vehicle` (≈314-396).

- [ ] **Step 2: Create `hr/models/employee.py`** — paste the imports plus the `Employee` and `EmployeeDocument` classes VERBATIM from the old file (no field changes). Top of file:
```python
from django.db import models
from django.conf import settings

# (Employee and EmployeeDocument classes pasted verbatim from the old hr/models.py)
```

- [ ] **Step 3: Create `hr/models/assets.py`** — imports plus `Asset`, `AssetAssignment`, `Vehicle` VERBATIM:
```python
from django.db import models
from django.conf import settings

# (Asset, AssetAssignment, Vehicle classes pasted verbatim)
```

- [ ] **Step 4: Create `hr/models/__init__.py`**:
```python
from .employee import Employee, EmployeeDocument
from .assets import Asset, AssetAssignment, Vehicle

__all__ = [
    'Employee', 'EmployeeDocument',
    'Asset', 'AssetAssignment', 'Vehicle',
]
```

- [ ] **Step 5: Delete the old file** — remove `hr/models.py` (the package now supersedes it). On Windows: `Remove-Item hr\models.py`.

- [ ] **Step 6: Verify NO migration churn**

Run: `python manage.py makemigrations hr --check --dry-run`
Expected: `No changes detected`. If Django reports changes (it shouldn't — same app_label, same model names, same fields), STOP and investigate before proceeding. Do NOT generate a migration here.

- [ ] **Step 7: Verify imports + system check**

Run: `python manage.py check`
Expected: `System check identified no issues`. (This confirms `from .models import Employee, ...` in `hr/views.py`, `hr/forms.py`, `hr/admin.py` still resolve via the package `__init__`.)

- [ ] **Step 8: Commit**
```
git add hr/models/ hr/models.py
git commit -m "HR: split models.py into hr/models/ package (no schema change)"
```

---

## Task A2: Work-calendar helpers

**Files:**
- Create `hr/work_calendar.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — create `hr/tests.py`:
```python
from datetime import date
from django.test import TestCase
from hr.work_calendar import count_working_days, is_working_day


class WorkCalendarTests(TestCase):
    WEEKENDS = {4, 5}  # Fri, Sat (Mon=0..Sun=6)

    def test_weekday_is_working(self):
        self.assertTrue(is_working_day(date(2026, 7, 13), self.WEEKENDS, set()))  # Monday

    def test_friday_is_not_working(self):
        self.assertFalse(is_working_day(date(2026, 7, 10), self.WEEKENDS, set()))  # Friday

    def test_holiday_is_not_working(self):
        h = {date(2026, 7, 13)}
        self.assertFalse(is_working_day(date(2026, 7, 13), self.WEEKENDS, h))

    def test_count_excludes_weekend_and_holiday(self):
        # Sun 5 Jul -> Thu 9 Jul 2026 is 5 weekdays; add a holiday on Tue 7th -> 4
        holidays = {date(2026, 7, 7)}
        self.assertEqual(count_working_days(date(2026, 7, 5), date(2026, 7, 9), self.WEEKENDS, holidays), 4)

    def test_count_range_all_weekend_is_zero(self):
        # Fri 10 + Sat 11 Jul 2026
        self.assertEqual(count_working_days(date(2026, 7, 10), date(2026, 7, 11), self.WEEKENDS, set()), 0)
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.WorkCalendarTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'hr.work_calendar'`

- [ ] **Step 3: Implement** — create `hr/work_calendar.py`:
```python
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
```

- [ ] **Step 4: Run — expect pass**
Run: `python manage.py test hr.tests.WorkCalendarTests -v 2`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**
```
git add hr/work_calendar.py hr/tests.py
git commit -m "HR: work-calendar helpers (working-day counting)"
```

---

## Task A3: `LeaveType` model

**Files:**
- Modify `hr/models/leave.py` (create), `hr/models/__init__.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append to `hr/tests.py`:
```python
from hr.models import LeaveType


class LeaveTypeModelTests(TestCase):
    def test_create_and_str(self):
        lt = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=21)
        self.assertEqual(str(lt), 'Annual')
        self.assertTrue(lt.is_paid)  # default True

    def test_code_is_unique(self):
        from django.db import IntegrityError
        LeaveType.objects.create(name='Sick', code='sick', default_annual_days=30)
        with self.assertRaises(IntegrityError):
            LeaveType.objects.create(name='Sick 2', code='sick', default_annual_days=10)
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.LeaveTypeModelTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'LeaveType'`

- [ ] **Step 3: Implement** — create `hr/models/leave.py`:
```python
from django.db import models
from django.conf import settings


class LeaveType(models.Model):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=40, unique=True)
    default_annual_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    is_paid = models.BooleanField(default=True)
    color = models.CharField(max_length=20, default='secondary', help_text='Bootstrap color name for badges')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
```

Add to `hr/models/__init__.py`:
```python
from .leave import LeaveType
```
and add `'LeaveType'` to `__all__`.

- [ ] **Step 4: Make + run migration**
Run: `python manage.py makemigrations hr` → creates `hr/migrations/0007_*.py` adding `LeaveType` ONLY.
Then `python manage.py migrate hr`.

- [ ] **Step 5: Run — expect pass**
Run: `python manage.py test hr.tests.LeaveTypeModelTests -v 2`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**
```
git add hr/models/ hr/migrations/ hr/tests.py
git commit -m "HR: LeaveType model"
```

---

## Task A4: `Holiday` + `AttendanceSettings` models

**Files:**
- Create `hr/models/attendance.py`; modify `hr/models/__init__.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append to `hr/tests.py`:
```python
from datetime import date as _date
from hr.models import Holiday, AttendanceSettings


class HolidayAndSettingsTests(TestCase):
    def test_holiday_unique_date(self):
        from django.db import IntegrityError
        Holiday.objects.create(date=_date(2026, 7, 17), name='Eid')
        with self.assertRaises(IntegrityError):
            Holiday.objects.create(date=_date(2026, 7, 17), name='Eid dup')

    def test_settings_singleton_default_weekend(self):
        s = AttendanceSettings.load()
        self.assertEqual(s.weekend_day_set(), {4, 5})  # Fri, Sat
        # load() always returns the same row
        self.assertEqual(AttendanceSettings.load().pk, s.pk)

    def test_settings_parse_custom_weekend(self):
        s = AttendanceSettings.load()
        s.weekend_days = '5,6'  # Sat, Sun
        s.save()
        self.assertEqual(AttendanceSettings.load().weekend_day_set(), {5, 6})
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.HolidayAndSettingsTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'Holiday'`

- [ ] **Step 3: Implement** — create `hr/models/attendance.py`:
```python
from django.db import models
from django.conf import settings


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date:%Y-%m-%d} {self.name}"


class AttendanceSettings(models.Model):
    """Singleton (pk=1) holding global attendance config."""
    weekend_days = models.CharField(
        max_length=20, default='4,5',
        help_text='Comma-separated weekday numbers, Mon=0..Sun=6. Default 4,5 = Fri,Sat.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Attendance settings'

    def __str__(self):
        return 'Attendance settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def weekend_day_set(self):
        out = set()
        for part in (self.weekend_days or '').split(','):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out
```

Add to `hr/models/__init__.py`:
```python
from .attendance import Holiday, AttendanceSettings
```
and extend `__all__`.

- [ ] **Step 4: Make + run migration**
Run: `python manage.py makemigrations hr` (0008_*) then `python manage.py migrate hr`.

- [ ] **Step 5: Run — expect pass**
Run: `python manage.py test hr.tests.HolidayAndSettingsTests -v 2`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**
```
git add hr/models/ hr/migrations/ hr/tests.py
git commit -m "HR: Holiday + AttendanceSettings (singleton, configurable weekend)"
```

---

## Task A5: `LeaveEntitlement` model (with balance properties)

**Files:**
- Modify `hr/models/leave.py`, `hr/models/__init__.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:
```python
from decimal import Decimal
from hr.models import Employee, LeaveEntitlement, LeaveRecord


def make_employee(iqama='E1', name='Ali', joining=None):
    return Employee.objects.create(iqama_number=iqama, full_name=name, joining_date=joining)


class LeaveEntitlementTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)

    def test_unique_per_employee_type_year(self):
        from django.db import IntegrityError
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=30)
        with self.assertRaises(IntegrityError):
            LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=25)

    def test_balance_with_records(self):
        ent = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=30)
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 3, 1), end_date=_date(2026, 3, 5), days=Decimal('5'))
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 6, 1), end_date=_date(2026, 6, 3), days=Decimal('3'))
        self.assertEqual(ent.taken_days, Decimal('8'))
        self.assertEqual(ent.remaining_days, Decimal('22'))

    def test_taken_only_counts_matching_year_and_type(self):
        ent = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=30)
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2025, 12, 30), end_date=_date(2025, 12, 31), days=Decimal('2'))
        self.assertEqual(ent.taken_days, Decimal('0'))  # 2025 record, not 2026
```

(This test also exercises `LeaveRecord`, implemented in Task A6 — it's acceptable for the test to import it now; run it after A6. To keep A5 self-contained, run only the first test in A5 step 4, then the full class after A6.)

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.LeaveEntitlementTests.test_unique_per_employee_type_year -v 2`
Expected: FAIL — `ImportError: cannot import name 'LeaveEntitlement'`

- [ ] **Step 3: Implement** — append to `hr/models/leave.py`:
```python
class LeaveEntitlement(models.Model):
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_entitlements')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='entitlements')
    year = models.PositiveIntegerField()
    entitled_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'leave_type', 'year')
        ordering = ['-year', 'leave_type__name']

    def __str__(self):
        return f"{self.employee.full_name} {self.leave_type.name} {self.year}: {self.entitled_days}"

    @property
    def taken_days(self):
        from decimal import Decimal
        agg = self.employee.leave_records.filter(
            leave_type=self.leave_type, start_date__year=self.year,
        ).aggregate(models.Sum('days'))
        return agg['days__sum'] or Decimal('0')

    @property
    def remaining_days(self):
        return self.entitled_days - self.taken_days
```

Add `from .leave import LeaveType, LeaveEntitlement` to `__init__.py` and `__all__`.

- [ ] **Step 4: Make + run migration**; run the single test → PASS. (Full class passes after A6.)
Run: `python manage.py makemigrations hr && python manage.py migrate hr`
Run: `python manage.py test hr.tests.LeaveEntitlementTests.test_unique_per_employee_type_year -v 2` → PASS

- [ ] **Step 5: Commit**
```
git add hr/models/ hr/migrations/ hr/tests.py
git commit -m "HR: LeaveEntitlement model with balance properties"
```

---

## Task A6: `LeaveRecord` model (auto day-count + validation)

**Files:**
- Modify `hr/models/leave.py`, `hr/models/__init__.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:
```python
from django.core.exceptions import ValidationError


class LeaveRecordTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)

    def test_days_autocomputed_excluding_weekend(self):
        # Sun 5 Jul -> Thu 9 Jul 2026 = 5 working days (Fri/Sat weekend)
        rec = LeaveRecord(employee=self.emp, leave_type=self.annual,
                          start_date=_date(2026, 7, 5), end_date=_date(2026, 7, 9))
        rec.save()
        self.assertEqual(rec.days, Decimal('5'))

    def test_days_excludes_holiday(self):
        Holiday.objects.create(date=_date(2026, 7, 7), name='X')
        rec = LeaveRecord(employee=self.emp, leave_type=self.annual,
                          start_date=_date(2026, 7, 5), end_date=_date(2026, 7, 9))
        rec.save()
        self.assertEqual(rec.days, Decimal('4'))

    def test_manual_days_override_preserved(self):
        rec = LeaveRecord(employee=self.emp, leave_type=self.annual,
                          start_date=_date(2026, 7, 5), end_date=_date(2026, 7, 9), days=Decimal('3'))
        rec.save()
        self.assertEqual(rec.days, Decimal('3'))

    def test_end_before_start_rejected(self):
        rec = LeaveRecord(employee=self.emp, leave_type=self.annual,
                          start_date=_date(2026, 7, 9), end_date=_date(2026, 7, 5))
        with self.assertRaises(ValidationError):
            rec.full_clean()
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.LeaveRecordTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'LeaveRecord'`

- [ ] **Step 3: Implement** — append to `hr/models/leave.py`:
```python
class LeaveRecord(models.Model):
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_records')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='records')
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                               help_text='Working days; auto-computed from the range if left blank.')
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        indexes = [models.Index(fields=['employee', 'start_date'])]

    def __str__(self):
        return f"{self.employee.full_name} {self.leave_type.name} {self.start_date}..{self.end_date}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})

    def computed_days(self):
        from decimal import Decimal
        from hr.models import AttendanceSettings, Holiday
        from hr.work_calendar import count_working_days
        weekends = AttendanceSettings.load().weekend_day_set()
        holidays = set(Holiday.objects.filter(is_active=True).values_list('date', flat=True))
        return Decimal(count_working_days(self.start_date, self.end_date, weekends, holidays))

    def save(self, *args, **kwargs):
        if self.days is None:
            self.days = self.computed_days()
        super().save(*args, **kwargs)
```

Add `LeaveRecord` to `hr/models/__init__.py` imports + `__all__`.

- [ ] **Step 4: Make + run migration**; run tests.
Run: `python manage.py makemigrations hr && python manage.py migrate hr`
Run: `python manage.py test hr.tests.LeaveRecordTests hr.tests.LeaveEntitlementTests -v 2`
Expected: PASS (LeaveRecord 4 + LeaveEntitlement 3)

- [ ] **Step 5: Commit**
```
git add hr/models/ hr/migrations/ hr/tests.py
git commit -m "HR: LeaveRecord model (auto working-day count + validation)"
```

---

## Task A7: Annual-rule + year-entitlement generator

**Files:**
- Create `hr/leave_services.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:
```python
from hr.leave_services import annual_entitlement_for, generate_year_entitlements


class AnnualRuleTests(TestCase):
    def test_joining_year_is_25(self):
        self.assertEqual(annual_entitlement_for(_date(2025, 7, 1), 2025), Decimal('25'))

    def test_year_after_joining_is_30(self):
        self.assertEqual(annual_entitlement_for(_date(2025, 7, 1), 2026), Decimal('30'))
        self.assertEqual(annual_entitlement_for(_date(2025, 7, 1), 2027), Decimal('30'))

    def test_no_joining_date_defaults_30(self):
        self.assertEqual(annual_entitlement_for(None, 2026), Decimal('30'))


class GeneratorTests(TestCase):
    def setUp(self):
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)
        self.sick = LeaveType.objects.create(name='Sick', code='sick', default_annual_days=15)
        self.e_new = make_employee('A', 'New', _date(2026, 2, 1))   # joins 2026
        self.e_old = make_employee('B', 'Old', _date(2020, 1, 1))   # tenured
        make_employee('C', 'Inactive', _date(2019, 1, 1)).__class__.objects.filter(iqama_number='C').update(is_active=False)

    def test_generates_rows_for_active_employees_with_rule(self):
        created = generate_year_entitlements(2026)
        self.assertEqual(Decimal(self._ent(self.e_new, self.annual)), Decimal('25'))  # joining year
        self.assertEqual(Decimal(self._ent(self.e_old, self.annual)), Decimal('30'))  # tenured
        self.assertEqual(Decimal(self._ent(self.e_new, self.sick)), Decimal('15'))    # flat default
        # inactive employee gets none
        self.assertFalse(LeaveEntitlement.objects.filter(employee__iqama_number='C').exists())

    def test_does_not_overwrite_existing(self):
        LeaveEntitlement.objects.create(employee=self.e_old, leave_type=self.annual, year=2026, entitled_days=99)
        generate_year_entitlements(2026)
        self.assertEqual(self._ent(self.e_old, self.annual), Decimal('99'))

    def _ent(self, emp, lt):
        return LeaveEntitlement.objects.get(employee=emp, leave_type=lt, year=2026).entitled_days
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.AnnualRuleTests hr.tests.GeneratorTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'hr.leave_services'`

- [ ] **Step 3: Implement** — create `hr/leave_services.py`:
```python
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
```

- [ ] **Step 4: Run — expect pass**
Run: `python manage.py test hr.tests.AnnualRuleTests hr.tests.GeneratorTests -v 2`
Expected: PASS (AnnualRule 3 + Generator 2)

- [ ] **Step 5: Commit**
```
git add hr/leave_services.py hr/tests.py
git commit -m "HR: annual-rule + year-entitlement generator"
```

---

## Task A8: Leave type + holiday admin/CRUD, seed, admin registration

**Files:**
- Modify `hr/admin.py`, `hr/forms.py`, `hr/views.py`, `hr/urls.py`
- Create templates `templates/hr/leavetype_list.html`, `leavetype_form.html`, `holiday_list.html`, `holiday_form.html`
- Create seed migration for default leave types
- Test: `hr/tests.py`

- [ ] **Step 1: Register models in admin** — append to `hr/admin.py`:
```python
from .models import LeaveType, LeaveEntitlement, LeaveRecord, Holiday, AttendanceSettings, AttendanceRecord


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'default_annual_days', 'is_paid', 'is_active']

@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ['date', 'name', 'is_active']
    list_filter = ['is_active']

@admin.register(LeaveEntitlement)
class LeaveEntitlementAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'year', 'entitled_days']
    list_filter = ['year', 'leave_type']
    search_fields = ['employee__full_name']

@admin.register(LeaveRecord)
class LeaveRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'start_date', 'end_date', 'days']
    list_filter = ['leave_type']
    search_fields = ['employee__full_name']
```
(`AttendanceRecord` registration is added in Phase B; import it here only after B1 exists — for now import just the models that exist: remove `AttendanceRecord` from the import line until Phase B. **Action:** import `LeaveType, LeaveEntitlement, LeaveRecord, Holiday, AttendanceSettings` only.)

- [ ] **Step 2: Seed default leave types via data migration**
Run: `python manage.py makemigrations hr --empty --name seed_leave_types`, then edit it:
```python
from django.db import migrations

DEFAULTS = [
    ('Annual', 'annual', 30, True, 'primary'),
    ('Sick', 'sick', 30, True, 'warning'),
    ('Unpaid', 'unpaid', 0, False, 'secondary'),
]

def seed(apps, schema_editor):
    LeaveType = apps.get_model('hr', 'LeaveType')
    for name, code, days, paid, color in DEFAULTS:
        LeaveType.objects.get_or_create(code=code, defaults={
            'name': name, 'default_annual_days': days, 'is_paid': paid, 'color': color})

def unseed(apps, schema_editor):
    LeaveType = apps.get_model('hr', 'LeaveType')
    LeaveType.objects.filter(code__in=[d[1] for d in DEFAULTS]).delete()

class Migration(migrations.Migration):
    dependencies = [('hr', '<previous hr migration>')]  # set to the latest, e.g. 0010
    operations = [migrations.RunPython(seed, unseed)]
```
Run `python manage.py migrate hr`.

- [ ] **Step 3: Forms** — append to `hr/forms.py`:
```python
from .models import LeaveType, Holiday, LeaveRecord, LeaveEntitlement


class LeaveTypeForm(forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ['name', 'code', 'default_annual_days', 'is_paid', 'color', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'default_annual_days': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'color': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['date', 'name', 'is_active']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
```

- [ ] **Step 4: Views** — append to `hr/views.py` (reuse the existing `AdminRequiredMixin`, already defined in this file):
```python
from .models import LeaveType, LeaveEntitlement, LeaveRecord, Holiday, AttendanceSettings
from .forms import LeaveTypeForm, HolidayForm
from .leave_services import generate_year_entitlements


class LeaveTypeListView(AdminRequiredMixin, ListView):
    model = LeaveType
    template_name = 'hr/leavetype_list.html'
    context_object_name = 'leave_types'

class LeaveTypeCreateView(AdminRequiredMixin, CreateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'hr/leavetype_form.html'
    success_url = reverse_lazy('hr:leavetype_list')

class LeaveTypeUpdateView(AdminRequiredMixin, UpdateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'hr/leavetype_form.html'
    success_url = reverse_lazy('hr:leavetype_list')


class HolidayListView(AdminRequiredMixin, ListView):
    model = Holiday
    template_name = 'hr/holiday_list.html'
    context_object_name = 'holidays'

class HolidayCreateView(AdminRequiredMixin, CreateView):
    model = Holiday
    form_class = HolidayForm
    template_name = 'hr/holiday_form.html'
    success_url = reverse_lazy('hr:holiday_list')

class HolidayUpdateView(AdminRequiredMixin, UpdateView):
    model = Holiday
    form_class = HolidayForm
    template_name = 'hr/holiday_form.html'
    success_url = reverse_lazy('hr:holiday_list')

class HolidayDeleteView(AdminRequiredMixin, DeleteView):
    model = Holiday
    template_name = 'hr/holiday_confirm_delete.html'
    success_url = reverse_lazy('hr:holiday_list')
```

- [ ] **Step 5: URLs** — add to `hr/urls.py` `urlpatterns`:
```python
    path('leave-types/', views.LeaveTypeListView.as_view(), name='leavetype_list'),
    path('leave-types/create/', views.LeaveTypeCreateView.as_view(), name='leavetype_create'),
    path('leave-types/<int:pk>/edit/', views.LeaveTypeUpdateView.as_view(), name='leavetype_update'),
    path('holidays/', views.HolidayListView.as_view(), name='holiday_list'),
    path('holidays/create/', views.HolidayCreateView.as_view(), name='holiday_create'),
    path('holidays/<int:pk>/edit/', views.HolidayUpdateView.as_view(), name='holiday_update'),
    path('holidays/<int:pk>/delete/', views.HolidayDeleteView.as_view(), name='holiday_delete'),
```

- [ ] **Step 6: Templates** — create the four templates following the verbatim `employee_list.html`/`employee_form.html` pattern (extend base, `{% block content %}`, Bootstrap table/form, inline errors). `leavetype_list.html` table cols: Name, Code, Default days, Paid, Active, Edit. `holiday_list.html`: Date, Name, Active, Edit/Delete, plus an Add button to `hr:holiday_create`. Forms render `{{ form.<field> }}` with labels and `invalid-feedback`. `holiday_confirm_delete.html` mirrors `employee_confirm_delete.html`.

- [ ] **Step 7: Test the views**
```python
class LeaveAdminViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()

    def test_leavetype_list_ok(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('hr:leavetype_list')).status_code, 200)

    def test_holiday_create(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('hr:holiday_create'),
                                {'date': '2026-07-17', 'name': 'Eid', 'is_active': 'on'})
        self.assertEqual(Holiday.objects.filter(name='Eid').count(), 1)
```
Run: `python manage.py test hr.tests.LeaveAdminViewTests -v 2` → PASS. Then `python manage.py check`.

- [ ] **Step 8: Commit**
```
git add hr/admin.py hr/forms.py hr/views.py hr/urls.py hr/migrations/ templates/hr/ hr/tests.py
git commit -m "HR: leave-type + holiday admin/CRUD + seed default leave types"
```

---

## Task A9: Leave records + per-employee leave summary + year generator action

**Files:**
- Modify `hr/forms.py`, `hr/views.py`, `hr/urls.py`
- Create `templates/hr/leave_summary.html`, `leaverecord_form.html`, `entitlement_year.html`
- Modify `templates/base.html` (Leave nav links)
- Test: `hr/tests.py`

- [ ] **Step 1: LeaveRecordForm** — append to `hr/forms.py`:
```python
class LeaveRecordForm(forms.ModelForm):
    class Meta:
        model = LeaveRecord
        fields = ['employee', 'leave_type', 'start_date', 'end_date', 'days', 'note']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'leave_type': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'days': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5',
                                             'placeholder': 'Auto if blank'}),
            'note': forms.TextInput(attrs={'class': 'form-control'}),
        }
```

- [ ] **Step 2: Views** — append to `hr/views.py`:
```python
from django.utils import timezone
from .forms import LeaveRecordForm


class LeaveRecordCreateView(AdminRequiredMixin, CreateView):
    model = LeaveRecord
    form_class = LeaveRecordForm
    template_name = 'hr/leaverecord_form.html'

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.days = form.cleaned_data.get('days') or None  # let model compute if blank
        messages.success(self.request, 'Leave recorded.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('hr:leave_summary', kwargs={'pk': self.object.employee_id})


class LeaveRecordDeleteView(AdminRequiredMixin, DeleteView):
    model = LeaveRecord
    template_name = 'hr/leaverecord_confirm_delete.html'
    def get_success_url(self):
        return reverse_lazy('hr:leave_summary', kwargs={'pk': self.object.employee_id})


class EmployeeLeaveSummaryView(AdminRequiredMixin, DetailView):
    model = Employee
    template_name = 'hr/leave_summary.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year = int(self.request.GET.get('year') or timezone.now().year)
        ctx['year'] = year
        ctx['entitlements'] = LeaveEntitlement.objects.filter(
            employee=self.object, year=year).select_related('leave_type')
        ctx['records'] = self.object.leave_records.filter(
            start_date__year=year).select_related('leave_type')
        return ctx


@login_required
def entitlement_year(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    year = int(request.GET.get('year') or timezone.now().year)
    if request.method == 'POST':
        created = generate_year_entitlements(int(request.POST.get('year') or year), actor=request.user)
        messages.success(request, f'Generated {created} entitlement row(s) for {request.POST.get("year")}.')
        return redirect(f"{reverse_lazy('hr:entitlement_year')}?year={request.POST.get('year') or year}")
    entitlements = LeaveEntitlement.objects.filter(year=year).select_related('employee', 'leave_type')
    return render(request, 'hr/entitlement_year.html', {'year': year, 'entitlements': entitlements})
```

- [ ] **Step 3: URLs** — add:
```python
    path('leave/record/create/', views.LeaveRecordCreateView.as_view(), name='leave_record_create'),
    path('leave/record/<int:pk>/delete/', views.LeaveRecordDeleteView.as_view(), name='leave_record_delete'),
    path('leave/entitlements/', views.entitlement_year, name='entitlement_year'),
    path('<int:pk>/leave/', views.EmployeeLeaveSummaryView.as_view(), name='leave_summary'),
```

- [ ] **Step 4: Templates** — `leave_summary.html`: show the `entitled | taken | remaining` table from `entitlements` (use `e.entitled_days`, `e.taken_days`, `e.remaining_days`), a year selector (GET form), the year's `records` table with a delete link, and an "Add leave" button to `hr:leave_record_create`. `entitlement_year.html`: year selector + a POST form with a year input and a "Generate entitlements" button + a table of existing entitlements. `leaverecord_form.html` and `leaverecord_confirm_delete.html` follow the employee form/delete patterns.

- [ ] **Step 5: Sidebar nav** — in `templates/base.html`, inside the HR nav section (after the Employees `<li>`, ≈ line 1020), add a "Leave" collapsible group with links to `hr:entitlement_year` (Entitlements), `hr:leavetype_list` (Leave Types), `hr:holiday_list` (Holidays). Match the existing Assets-submenu markup pattern.

- [ ] **Step 6: Tests** — append:
```python
class LeaveRecordViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)

    def test_create_leave_autocomputes_days(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:leave_record_create'), {
            'employee': self.emp.pk, 'leave_type': self.annual.pk,
            'start_date': '2026-07-05', 'end_date': '2026-07-09', 'days': '', 'note': ''})
        rec = LeaveRecord.objects.get(employee=self.emp)
        self.assertEqual(rec.days, Decimal('5'))

    def test_summary_shows_balance(self):
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=30)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:leave_summary', kwargs={'pk': self.emp.pk}) + '?year=2026')
        self.assertEqual(resp.status_code, 200)

    def test_generate_entitlements_action(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:entitlement_year'), {'year': '2026'})
        self.assertTrue(LeaveEntitlement.objects.filter(employee=self.emp, year=2026).exists())
```
Run: `python manage.py test hr.tests.LeaveRecordViewTests -v 2` → PASS. Then `python manage.py check`.

- [ ] **Step 7: Commit**
```
git add hr/forms.py hr/views.py hr/urls.py templates/ hr/tests.py
git commit -m "HR: leave records, per-employee balance summary, year-entitlement action"
```

- [ ] **Step 8: Phase A regression**
Run: `python manage.py test hr -v 1` (all pass) and `python manage.py check`.

---

# PHASE B — Daily Attendance

## Task B1: `AttendanceRecord` model + status derivation

**Files:**
- Modify `hr/models/attendance.py`, `hr/models/__init__.py`
- Create `hr/attendance_services.py` (status derivation)
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append to `hr/tests.py`:
```python
from datetime import time
from hr.models import AttendanceRecord
from hr.attendance_services import derive_status


class AttendanceStatusTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)

    def test_leave_beats_everything(self):
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13), days=Decimal('1'))
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 13), time(8, 0))[0], 'leave')

    def test_holiday(self):
        Holiday.objects.create(date=_date(2026, 7, 14), name='X')
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 14), None)[0], 'holiday')

    def test_weekend(self):
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 10), None)[0], 'weekend')  # Friday

    def test_present_with_hours(self):
        status, hours = derive_status(self.emp, _date(2026, 7, 13), time(8, 0), time(17, 30))
        self.assertEqual(status, 'present')
        self.assertEqual(hours, Decimal('9.5'))

    def test_absent_when_no_checkin_on_workday(self):
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 13), None)[0], 'absent')
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.AttendanceStatusTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'AttendanceRecord'`

- [ ] **Step 3: Implement model** — append to `hr/models/attendance.py`:
```python
class AttendanceRecord(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'), ('absent', 'Absent'), ('leave', 'Leave'),
        ('holiday', 'Holiday'), ('weekend', 'Weekend'),
    ]
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='attendance')
    date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='absent')
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
        ordering = ['-date']
        indexes = [models.Index(fields=['date', 'status']), models.Index(fields=['employee', 'date'])]

    def __str__(self):
        return f"{self.employee.full_name} {self.date} {self.status}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.check_in and self.check_out and self.check_out < self.check_in:
            raise ValidationError({'check_out': 'Check-out cannot be before check-in.'})
```
Add `AttendanceRecord` to `hr/models/__init__.py` + `__all__`.

- [ ] **Step 4: Implement derivation** — create `hr/attendance_services.py`:
```python
"""Attendance status derivation: leave > holiday > weekend > present > absent."""
from decimal import Decimal
from datetime import datetime


def _hours_between(check_in, check_out):
    if not (check_in and check_out):
        return None
    base = datetime(2000, 1, 1)
    delta = datetime.combine(base, check_out) - datetime.combine(base, check_in)
    return Decimal(round(delta.total_seconds() / 3600, 2))


def derive_status(employee, d, check_in, check_out=None):
    """Return (status, hours_worked). Reads leave records, holidays, weekend config."""
    from hr.models import LeaveRecord, Holiday, AttendanceSettings
    on_leave = LeaveRecord.objects.filter(
        employee=employee, start_date__lte=d, end_date__gte=d).exists()
    if on_leave:
        return 'leave', None
    if Holiday.objects.filter(date=d, is_active=True).exists():
        return 'holiday', None
    weekends = AttendanceSettings.load().weekend_day_set()
    if d.weekday() in weekends:
        return 'weekend', None
    if check_in:
        return 'present', _hours_between(check_in, check_out)
    return 'absent', None
```

- [ ] **Step 5: Make + run migration**; run tests.
Run: `python manage.py makemigrations hr && python manage.py migrate hr`
Run: `python manage.py test hr.tests.AttendanceStatusTests -v 2` → PASS (5 tests)

- [ ] **Step 6: Register in admin** — add `AttendanceRecord` to the admin import + a simple `@admin.register(AttendanceRecord)` with `list_display = ['employee','date','status','check_in','check_out','hours_worked']`, `list_filter = ['status','date']`.

- [ ] **Step 7: Commit**
```
git add hr/models/ hr/attendance_services.py hr/admin.py hr/migrations/ hr/tests.py
git commit -m "HR: AttendanceRecord model + status derivation"
```

---

## Task B2: Daily attendance grid (render + bulk save)

**Files:**
- Modify `hr/views.py`, `hr/urls.py`
- Create `templates/hr/attendance_grid.html`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:
```python
class AttendanceGridTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)

    def test_grid_get_lists_active_employees(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_grid') + '?date=2026-07-13')  # Monday
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.emp.full_name)

    def test_grid_post_saves_present(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:attendance_grid') + '?date=2026-07-13', {
            'date': '2026-07-13',
            f'check_in_{self.emp.pk}': '08:00',
            f'check_out_{self.emp.pk}': '17:30',
        })
        rec = AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 13))
        self.assertEqual(rec.status, 'present')
        self.assertEqual(rec.hours_worked, Decimal('9.5'))

    def test_grid_post_marks_leave_day(self):
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13), days=Decimal('1'))
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:attendance_grid') + '?date=2026-07-13', {'date': '2026-07-13'})
        rec = AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 13))
        self.assertEqual(rec.status, 'leave')
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.AttendanceGridTests -v 2`
Expected: FAIL — `NoReverseMatch: 'attendance_grid'`

- [ ] **Step 3: Implement view** — append to `hr/views.py`:
```python
from datetime import datetime as _dt
from .models import AttendanceRecord
from .attendance_services import derive_status


def _parse_date(s):
    try:
        return _dt.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return timezone.now().date()


@login_required
def attendance_grid(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')

    day = _parse_date(request.GET.get('date') or (request.POST.get('date') if request.method == 'POST' else None))
    employees = list(Employee.objects.filter(is_active=True).order_by('full_name'))

    if request.method == 'POST':
        for emp in employees:
            ci = request.POST.get(f'check_in_{emp.pk}') or None
            co = request.POST.get(f'check_out_{emp.pk}') or None
            ci_t = _dt.strptime(ci, '%H:%M').time() if ci else None
            co_t = _dt.strptime(co, '%H:%M').time() if co else None
            status, hours = derive_status(emp, day, ci_t, co_t)
            AttendanceRecord.objects.update_or_create(
                employee=emp, date=day,
                defaults={'check_in': ci_t, 'check_out': co_t, 'status': status,
                          'hours_worked': hours, 'created_by': request.user})
        messages.success(request, f'Attendance saved for {day:%Y-%m-%d}.')
        return redirect(f"{reverse_lazy('hr:attendance_grid')}?date={day:%Y-%m-%d}")

    existing = {r.employee_id: r for r in AttendanceRecord.objects.filter(date=day)}
    rows = []
    for emp in employees:
        rec = existing.get(emp.pk)
        # derive a preview status for employees with no record yet (leave/holiday/weekend lock)
        preview_status, _ = derive_status(emp, day, rec.check_in if rec else None,
                                           rec.check_out if rec else None)
        locked = preview_status in ('leave', 'holiday', 'weekend')
        rows.append({'employee': emp, 'record': rec, 'status': rec.status if rec else preview_status,
                     'locked': locked})
    return render(request, 'hr/attendance_grid.html', {'day': day, 'rows': rows})
```

- [ ] **Step 4: URL** — add:
```python
    path('attendance/', views.attendance_grid, name='attendance_grid'),
```

- [ ] **Step 5: Template** — create `templates/hr/attendance_grid.html`: a date picker GET form (`<input type="date" name="date">` defaulting to `{{ day|date:'Y-m-d' }}`), then a POST `<form>` with a hidden `date` field and a table: columns Employee | Check-in | Check-out | Status. For each `row`: if `row.locked`, render the status as a badge (color by status) and NO inputs; else render `<input type="time" name="check_in_{{ row.employee.pk }}">` / `check_out_...` pre-filled from `row.record.check_in|time:'H:i'`. A single "Save all" submit button. Use `{% csrf_token %}`. Follow the Bootstrap table conventions from `employee_list.html`.

- [ ] **Step 6: Run — expect pass**
Run: `python manage.py test hr.tests.AttendanceGridTests -v 2` → PASS (3 tests). Then `python manage.py check`.

- [ ] **Step 7: Commit**
```
git add hr/views.py hr/urls.py templates/hr/attendance_grid.html hr/tests.py
git commit -m "HR: daily attendance grid (bulk render + save, leave/holiday-aware)"
```

---

## Task B3: Per-employee attendance history + monthly summary

**Files:**
- Modify `hr/views.py`, `hr/urls.py`
- Create `templates/hr/attendance_history.html`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:
```python
class AttendanceHistoryTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()

    def test_history_summary_counts(self):
        AttendanceRecord.objects.create(employee=self.emp, date=_date(2026, 7, 13), status='present', hours_worked=Decimal('8'))
        AttendanceRecord.objects.create(employee=self.emp, date=_date(2026, 7, 14), status='absent')
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_history', kwargs={'pk': self.emp.pk}) + '?year=2026&month=7')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['summary']['present'], 1)
        self.assertEqual(resp.context['summary']['absent'], 1)
        self.assertEqual(resp.context['summary']['total_hours'], Decimal('8'))
```

- [ ] **Step 2: Run — expect failure**
Run: `python manage.py test hr.tests.AttendanceHistoryTests -v 2`
Expected: FAIL — `NoReverseMatch: 'attendance_history'`

- [ ] **Step 3: Implement** — append to `hr/views.py`:
```python
from django.db.models import Sum, Count


class AttendanceHistoryView(AdminRequiredMixin, DetailView):
    model = Employee
    template_name = 'hr/attendance_history.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        year = int(self.request.GET.get('year') or now.year)
        month = int(self.request.GET.get('month') or now.month)
        qs = self.object.attendance.filter(date__year=year, date__month=month).order_by('date')
        counts = {row['status']: row['n'] for row in qs.values('status').annotate(n=Count('id'))}
        total_hours = qs.aggregate(s=Sum('hours_worked'))['s'] or 0
        ctx.update({
            'year': year, 'month': month, 'records': qs,
            'summary': {
                'present': counts.get('present', 0), 'absent': counts.get('absent', 0),
                'leave': counts.get('leave', 0), 'holiday': counts.get('holiday', 0),
                'weekend': counts.get('weekend', 0), 'total_hours': total_hours,
            },
        })
        return ctx
```

- [ ] **Step 4: URL** — add:
```python
    path('<int:pk>/attendance/', views.AttendanceHistoryView.as_view(), name='attendance_history'),
```

- [ ] **Step 5: Template** — `attendance_history.html`: year+month GET selector; a summary card row (Present/Absent/Leave/Holiday/Weekend counts + total hours from `summary`); a table of `records` (Date | Check-in | Check-out | Status badge | Hours).

- [ ] **Step 6: Run — expect pass**
Run: `python manage.py test hr.tests.AttendanceHistoryTests -v 2` → PASS. Then `python manage.py check`.

- [ ] **Step 7: Commit**
```
git add hr/views.py hr/urls.py templates/hr/attendance_history.html hr/tests.py
git commit -m "HR: per-employee attendance history + monthly summary"
```

---

## Task B4: Attendance settings form + regenerate-day action + nav links

**Files:**
- Modify `hr/forms.py`, `hr/views.py`, `hr/urls.py`, `templates/base.html`
- Create `templates/hr/attendance_settings.html`
- Test: `hr/tests.py`

- [ ] **Step 1: AttendanceSettingsForm** — append to `hr/forms.py`:
```python
from .models import AttendanceSettings

WEEKDAY_CHOICES = [(0, 'Mon'), (1, 'Tue'), (2, 'Wed'), (3, 'Thu'), (4, 'Fri'), (5, 'Sat'), (6, 'Sun')]


class AttendanceSettingsForm(forms.Form):
    weekend_days = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES, required=False,
        widget=forms.CheckboxSelectMultiple)

    def initial_from(self, settings_obj):
        self.initial['weekend_days'] = [str(x) for x in sorted(settings_obj.weekend_day_set())]
```

- [ ] **Step 2: Views** — append to `hr/views.py`:
```python
from .forms import AttendanceSettingsForm


@login_required
def attendance_settings(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    obj = AttendanceSettings.load()
    if request.method == 'POST':
        form = AttendanceSettingsForm(request.POST)
        if form.is_valid():
            obj.weekend_days = ','.join(form.cleaned_data['weekend_days'])
            obj.save()
            messages.success(request, 'Attendance settings saved.')
            return redirect('hr:attendance_settings')
    else:
        form = AttendanceSettingsForm()
        form.initial_from(obj)
    return render(request, 'hr/attendance_settings.html', {'form': form})


@login_required
def attendance_regenerate(request):
    """Re-derive stored status for all records on a given date (after leave/holiday edits)."""
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    day = _parse_date(request.POST.get('date'))
    n = 0
    for rec in AttendanceRecord.objects.filter(date=day).select_related('employee'):
        status, hours = derive_status(rec.employee, day, rec.check_in, rec.check_out)
        AttendanceRecord.objects.filter(pk=rec.pk).update(status=status, hours_worked=hours)
        n += 1
    messages.success(request, f'Regenerated {n} record(s) for {day:%Y-%m-%d}.')
    return redirect(f"{reverse_lazy('hr:attendance_grid')}?date={day:%Y-%m-%d}")
```

- [ ] **Step 3: URLs** — add:
```python
    path('attendance/settings/', views.attendance_settings, name='attendance_settings'),
    path('attendance/regenerate/', views.attendance_regenerate, name='attendance_regenerate'),
```

- [ ] **Step 4: Templates + nav** — `attendance_settings.html`: a POST form rendering the weekend checkboxes + save. In the attendance grid template, add a small POST form ("Regenerate this day") posting `date` to `hr:attendance_regenerate`. In `templates/base.html` HR nav, add an "Attendance" link group: Daily grid (`hr:attendance_grid`) and Settings (`hr:attendance_settings`).

- [ ] **Step 5: Test** — append:
```python
class AttendanceSettingsViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()

    def test_update_weekend(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:attendance_settings'), {'weekend_days': ['5', '6']})
        from hr.models import AttendanceSettings
        self.assertEqual(AttendanceSettings.load().weekend_day_set(), {5, 6})
```
Run: `python manage.py test hr.tests.AttendanceSettingsViewTests -v 2` → PASS. Then `python manage.py check`.

- [ ] **Step 6: Commit**
```
git add hr/forms.py hr/views.py hr/urls.py templates/ hr/tests.py
git commit -m "HR: attendance settings (weekend) + regenerate-day action + nav"
```

---

## Task B5: Full regression

- [ ] **Step 1: Full HR suite** — `python manage.py test hr -v 1` → all pass.
- [ ] **Step 2: Whole project** — `python manage.py test -v 1` → no new failures.
- [ ] **Step 3: System + migration check** — `python manage.py check` (clean) and `python manage.py makemigrations --check --dry-run` (No changes detected).
- [ ] **Step 4: Manual smoke** — as an admin: create a Holiday; run "Generate entitlements" for the current year; add a leave record (confirm balance updates on the employee leave summary); open the attendance grid for a weekday and save check-in/out (confirm Present + hours); confirm a leave-covered day shows Leave (locked) in the grid.
- [ ] **Step 5: Final commit (if stragglers)** — `git add -A && git commit -m "HR: leave + attendance phase complete"`.

---

## Self-review notes (addressed)

- **Spec coverage:** models split (A1), calendar (A2), LeaveType/Holiday/Settings (A3/A4), Entitlement+balance (A5), LeaveRecord auto-count (A6), Leap annual rule + generator (A7), leave/holiday CRUD + seed (A8), records+summary+generate action (A9); AttendanceRecord+derivation (B1), daily grid (B2), history/summary (B3), settings+regenerate (B4), regression (B5). Permissions reuse `AdminRequiredMixin` everywhere. Leave days exclude weekend+holiday (A2/A6). Status precedence leave>holiday>weekend>present>absent (B1).
- **Naming consistency:** `count_working_days`, `is_working_day`, `AttendanceSettings.load()/weekend_day_set()`, `annual_entitlement_for`, `generate_year_entitlements`, `derive_status`, `taken_days`/`remaining_days`, `LeaveRecord.computed_days()` used identically across tasks.
- **Migration safety:** A1 verifies the package split produces no migration; each model task generates exactly one new migration; the seed is a separate data migration with a real `dependencies` entry (set to the latest hr migration at that point).
- **Known follow-ups (out of scope):** half-day leave, approval workflow, break/overtime, per-region weekends/holidays, payroll linkage, gating via the new capability system.
