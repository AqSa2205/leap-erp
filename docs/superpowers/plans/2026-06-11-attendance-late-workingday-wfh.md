# Attendance: Late + Working-Day Exceptions + WFH — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Late status (derived from a threshold), a WorkingDay exception calendar (inverse of Holidays), and WFH (multi-day record + per-day grid button) to HR attendance.

**Architecture:** Two new statuses (`late`, `wfh`) on `AttendanceRecord`; an `expected_in_by` time on the `AttendanceSettings` singleton; new `WorkingDay` and `WFHRecord` models; updated `derive_status` and `build_matrix` precedence; grid + matrix + settings UI. No leave-balance changes.

**Tech Stack:** Django, Django test runner (`python manage.py test hr`), Bootstrap 5, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-06-11-attendance-late-workingday-wfh-design.md`

**Current code (verbatim, for reference):**

`hr/attendance_services.py` `derive_status`:
```python
def derive_status(employee, d, check_in, check_out=None):
    from hr.models import LeaveRecord, Holiday, AttendanceSettings
    on_leave = LeaveRecord.objects.filter(employee=employee, start_date__lte=d, end_date__gte=d).exists()
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

`AttendanceRecord.STATUS_CHOICES` currently: present/absent/leave/holiday/weekend.
`AttendanceSettings`: singleton with `weekend_days` (str) + `load()` + `weekend_day_set()`.
Latest hr migration: `0012_attendancerecord`. Conventions: `AdminRequiredMixin`, Bootstrap forms,
`make_employee()` + admin-user setUp in `hr/tests.py`. Holiday CRUD + LeaveRecord form are the
patterns to mirror.

---

## File Structure

| File | Responsibility | Phase |
|---|---|---|
| `hr/models/attendance.py` | `expected_in_by` field, status choices, `WorkingDay`, `WFHRecord` | 1 & 2 |
| `hr/attendance_services.py` | `derive_status` precedence (late, workingday, wfh) | 1 & 2 |
| `hr/attendance_matrix.py` | `build_matrix` workingday + wfh | 1 & 2 |
| `hr/views.py` | WorkingDay CRUD, WFH form/list, settings field, grid WFH reconcile, summary counts | 1 & 2 |
| `hr/forms.py` | `AttendanceSettingsForm` (+expected_in_by), `WorkingDayForm`, `WFHRecordForm` | 1 & 2 |
| `hr/urls.py` | routes | 1 & 2 |
| `hr/admin.py` | register WorkingDay, WFHRecord | 1 & 2 |
| `templates/hr/*` | workingday CRUD, wfh form, grid WFH button, legends/badges | 1 & 2 |
| `templates/base.html` | `.badge-late`/`.badge-wfh` CSS + nav links | 1 & 2 |
| `hr/tests.py` | tests | 1 & 2 |

---

# PHASE 1 — Late + Working-Day Exceptions

## Task 1: AttendanceSettings.expected_in_by + late/wfh status choices

**Files:** `hr/models/attendance.py`, migration, `hr/tests.py`

- [ ] **Step 1: Failing test** — append to `hr/tests.py`:
```python
from datetime import time as _time


class AttendanceExtrasModelTests(TestCase):
    def test_expected_in_by_default(self):
        from hr.models import AttendanceSettings
        self.assertEqual(AttendanceSettings.load().expected_in_by, _time(8, 30))

    def test_record_accepts_late_and_wfh(self):
        from hr.models import AttendanceRecord
        codes = dict(AttendanceRecord.STATUS_CHOICES)
        self.assertIn('late', codes)
        self.assertIn('wfh', codes)
```

- [ ] **Step 2: Run** `python manage.py test hr.tests.AttendanceExtrasModelTests -v 2` → FAIL (no `expected_in_by` / choices).

- [ ] **Step 3: Implement** — in `hr/models/attendance.py`:
  - Add `from datetime import time` at the top (if not present).
  - On `AttendanceSettings`, add the field:
    ```python
    expected_in_by = models.TimeField(
        default=time(8, 30),
        help_text='Check-ins after this time are marked Late.')
    ```
  - On `AttendanceRecord.STATUS_CHOICES`, add two entries so it reads:
    ```python
    STATUS_CHOICES = [
        ('present', 'Present'), ('absent', 'Absent'), ('leave', 'Leave'),
        ('holiday', 'Holiday'), ('weekend', 'Weekend'),
        ('late', 'Late'), ('wfh', 'WFH'),
    ]
    ```
  - `status` is `max_length=10` already — fits 'present'/'holiday'/'weekend'/'wfh'/'late' (all ≤ 10).

- [ ] **Step 4: Migrate** — `python manage.py makemigrations hr` (e.g. `0013_*`) → adds the field + alters the choices. `python manage.py migrate hr`.

- [ ] **Step 5: Run** the test → PASS (2). Then `python manage.py check`.

- [ ] **Step 6: Commit**
```
git add hr/models/ hr/migrations/ hr/tests.py
git commit -m "HR: expected_in_by setting + late/wfh status choices"
```

---

## Task 2: WorkingDay model (inverse-holiday)

**Files:** `hr/models/attendance.py`, `hr/models/__init__.py`, migration, `hr/admin.py`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class WorkingDayModelTests(TestCase):
    def test_unique_date_and_str(self):
        from django.db import IntegrityError
        from hr.models import WorkingDay
        wd = WorkingDay.objects.create(date=_date(2026, 7, 18), name='Working Saturday')
        self.assertIn('2026-07-18', str(wd))
        with self.assertRaises(IntegrityError):
            WorkingDay.objects.create(date=_date(2026, 7, 18), name='dup')
```

- [ ] **Step 2: Run** `python manage.py test hr.tests.WorkingDayModelTests -v 2` → FAIL (ImportError WorkingDay).

- [ ] **Step 3: Implement** — append to `hr/models/attendance.py`:
```python
class WorkingDay(models.Model):
    """A normally-weekend date that is a working day (inverse of Holiday)."""
    date = models.DateField(unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date:%Y-%m-%d} {self.name}"
```
Add `WorkingDay` to `hr/models/__init__.py` (`from .attendance import ...`) and `__all__`.

- [ ] **Step 4: Migrate** — `python manage.py makemigrations hr` (`0014_*`, WorkingDay only) + `migrate`.

- [ ] **Step 5: Admin** — in `hr/admin.py` add `WorkingDay` to the import and:
```python
@admin.register(WorkingDay)
class WorkingDayAdmin(admin.ModelAdmin):
    list_display = ['date', 'name', 'is_active']
    list_filter = ['is_active']
```

- [ ] **Step 6: Run** the test → PASS. Then `python manage.py check`.

- [ ] **Step 7: Commit**
```
git add hr/models/ hr/migrations/ hr/admin.py hr/tests.py
git commit -m "HR: WorkingDay model (working-day exception calendar)"
```

---

## Task 3: derive_status — late threshold + working-day weekend override

**Files:** `hr/attendance_services.py`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class DeriveLateWorkingDayTests(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_on_time_is_present(self):
        from hr.attendance_services import derive_status
        # Mon 2026-07-13; default expected_in_by 08:30; check-in 08:20 -> present
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 13), _time(8, 20))[0], 'present')

    def test_late_after_threshold(self):
        from hr.attendance_services import derive_status
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 13), _time(9, 5))[0], 'late')

    def test_exactly_threshold_is_present(self):
        from hr.attendance_services import derive_status
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 13), _time(8, 30))[0], 'present')

    def test_working_day_overrides_weekend(self):
        from hr.models import WorkingDay
        from hr.attendance_services import derive_status
        # Fri 2026-07-10 is weekend; mark it a WorkingDay -> a check-in is present, not weekend
        WorkingDay.objects.create(date=_date(2026, 7, 10), name='WS')
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 10), _time(8, 0))[0], 'present')
        # and with no check-in -> absent (a working day), not weekend
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 10), None)[0], 'absent')

    def test_plain_weekend_still_weekend(self):
        from hr.attendance_services import derive_status
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 11), None)[0], 'weekend')  # Saturday
```
(`_time` is imported in Task 1's test addition; `_date` already imported.)

- [ ] **Step 2: Run** `python manage.py test hr.tests.DeriveLateWorkingDayTests -v 2` → FAIL (late not derived; weekend not overridden).

- [ ] **Step 3: Implement** — replace the body of `derive_status` in `hr/attendance_services.py` with:
```python
def derive_status(employee, d, check_in, check_out=None):
    """Return (status, hours_worked).

    Precedence: leave > holiday > weekend(unless WorkingDay) > wfh > late/present > absent.
    """
    from hr.models import LeaveRecord, Holiday, AttendanceSettings, WorkingDay
    if LeaveRecord.objects.filter(employee=employee, start_date__lte=d, end_date__gte=d).exists():
        return 'leave', None
    if Holiday.objects.filter(date=d, is_active=True).exists():
        return 'holiday', None
    settings = AttendanceSettings.load()
    is_weekend = d.weekday() in settings.weekend_day_set()
    if is_weekend and not WorkingDay.objects.filter(date=d, is_active=True).exists():
        return 'weekend', None
    # (WFH branch is added in Phase 2, Task 9, here — before the check_in branch.)
    if check_in:
        if check_in > settings.expected_in_by:
            return 'late', _hours_between(check_in, check_out)
        return 'present', _hours_between(check_in, check_out)
    return 'absent', None
```
Update the module docstring (line 1) to the new precedence.

- [ ] **Step 4: Run** the test → PASS (5). Then `python manage.py test hr -v 1` (no regressions — the existing weekend/present/absent tests still hold; a working day with no record still derives present/absent correctly) and `python manage.py check`.

- [ ] **Step 5: Commit**
```
git add hr/attendance_services.py hr/tests.py
git commit -m "HR: derive_status — Late threshold + WorkingDay weekend override"
```

---

## Task 4: build_matrix — working-day weekend override + shading data

**Files:** `hr/attendance_matrix.py`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class MatrixWorkingDayTests(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_workingday_cell_not_weekend(self):
        from hr.models import WorkingDay
        from hr.attendance_matrix import build_matrix
        WorkingDay.objects.create(date=_date(2026, 7, 11), name='WS')  # Saturday
        days, rows = build_matrix([self.emp], _date(2026, 7, 11), _date(2026, 7, 11))
        self.assertEqual(rows[0]['cells'][0]['status'], '')        # blank working day, not 'weekend'
        self.assertFalse(rows[0]['cells'][0]['locked'])

    def test_weekend_set_excludes_workingdays(self):
        from hr.models import WorkingDay
        from hr.attendance_matrix import build_matrix
        WorkingDay.objects.create(date=_date(2026, 7, 11), name='WS')
        days, rows, weekend_dates = build_matrix([self.emp], _date(2026, 7, 10), _date(2026, 7, 11), with_weekend_dates=True)
        self.assertIn(_date(2026, 7, 10), weekend_dates)     # Friday still weekend
        self.assertNotIn(_date(2026, 7, 11), weekend_dates)  # Saturday is a working day
```

- [ ] **Step 2: Run** `python manage.py test hr.tests.MatrixWorkingDayTests -v 2` → FAIL (workingday not honored; no `with_weekend_dates`).

- [ ] **Step 3: Implement** — in `hr/attendance_matrix.py` `build_matrix`:
  - Change the signature to `def build_matrix(employees, start, end, with_weekend_dates=False):`.
  - After the `holidays = set(...)` / `weekends = ...` lines, add the working-day set and compute the per-date weekend set (so the template can shade correctly):
    ```python
    working_days = set(WorkingDay.objects.filter(
        is_active=True, date__range=(start, end)).values_list('date', flat=True))
    weekend_dates = {d for d in days if d.weekday() in weekends and d not in working_days}
    ```
    (import `WorkingDay` in the local `from hr.models import ...` line.)
  - In the cell loop, replace the weekend test `elif day.weekday() in weekends:` with `elif day in weekend_dates:`.
  - At the end, return `(days, rows, weekend_dates) if with_weekend_dates else (days, rows)`.
  - In `display_status_no_record(d)`, update the weekend check to also honor WorkingDay:
    ```python
    if d.weekday() in AttendanceSettings.load().weekend_day_set() \
            and not Holiday.objects.filter(date=d, is_active=True).exists() \
            and not __import__('hr.models', fromlist=['WorkingDay']).WorkingDay.objects.filter(date=d, is_active=True).exists():
        return 'weekend'
    ```
    (Prefer a clean top `from hr.models import WorkingDay` inside the function over the `__import__`; the inline form is only to keep this step self-contained.)

- [ ] **Step 4: Update the matrix view + template for the new return value** — in `hr/views.py` `attendance_matrix`, change `days, rows = build_matrix(...)` to `days, rows, weekend_dates = build_matrix(employees, start, end, with_weekend_dates=True)` and add `'weekend_dates': weekend_dates` to the context. In `templates/hr/attendance_matrix.html`, change the header shading `{% if d.weekday in weekend_set %}` to `{% if d in weekend_dates %}` (the column shading now matches the cells; `weekend_set` context can be dropped).

- [ ] **Step 5: Run** `python manage.py test hr.tests.MatrixWorkingDayTests hr.tests.MatrixViewTests hr.tests.MatrixHelperTests -v 2` → all PASS. Then `python manage.py check`.

- [ ] **Step 6: Commit**
```
git add hr/attendance_matrix.py hr/views.py templates/hr/attendance_matrix.html hr/tests.py
git commit -m "HR: matrix honors WorkingDay (weekend override + column shading)"
```

---

## Task 5: WorkingDay CRUD + nav

**Files:** `hr/forms.py`, `hr/views.py`, `hr/urls.py`, `templates/hr/workingday_list.html`, `workingday_form.html`, `workingday_confirm_delete.html`, `templates/base.html`, `hr/tests.py`

- [ ] **Step 1: Form** — append to `hr/forms.py` (add `WorkingDay` to the top `from .models import ...`):
```python
class WorkingDayForm(forms.ModelForm):
    class Meta:
        model = WorkingDay
        fields = ['date', 'name', 'is_active']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
```

- [ ] **Step 2: Views** — append to `hr/views.py` (add `WorkingDay` to models import, `WorkingDayForm` to forms import):
```python
class WorkingDayListView(AdminRequiredMixin, ListView):
    model = WorkingDay
    template_name = 'hr/workingday_list.html'
    context_object_name = 'working_days'
    paginate_by = 25

class WorkingDayCreateView(AdminRequiredMixin, CreateView):
    model = WorkingDay
    form_class = WorkingDayForm
    template_name = 'hr/workingday_form.html'
    success_url = reverse_lazy('hr:workingday_list')
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs); ctx['title'] = 'Add Working Day'; ctx['button_text'] = 'Save'; return ctx

class WorkingDayUpdateView(AdminRequiredMixin, UpdateView):
    model = WorkingDay
    form_class = WorkingDayForm
    template_name = 'hr/workingday_form.html'
    success_url = reverse_lazy('hr:workingday_list')
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs); ctx['title'] = 'Edit Working Day'; ctx['button_text'] = 'Save Changes'; return ctx

class WorkingDayDeleteView(AdminRequiredMixin, DeleteView):
    model = WorkingDay
    template_name = 'hr/workingday_confirm_delete.html'
    success_url = reverse_lazy('hr:workingday_list')
```

- [ ] **Step 3: URLs** — add to `hr/urls.py`:
```python
    path('working-days/', views.WorkingDayListView.as_view(), name='workingday_list'),
    path('working-days/create/', views.WorkingDayCreateView.as_view(), name='workingday_create'),
    path('working-days/<int:pk>/edit/', views.WorkingDayUpdateView.as_view(), name='workingday_update'),
    path('working-days/<int:pk>/delete/', views.WorkingDayDeleteView.as_view(), name='workingday_delete'),
```

- [ ] **Step 4: Templates** — create the three templates mirroring `templates/hr/holiday_list.html`, `holiday_form.html`, `holiday_confirm_delete.html` exactly (read them first), swapping Holiday→WorkingDay, the url names to `hr:workingday_*`, columns Date | Name | Active | Edit/Delete, heading "Working Days", add button to `hr:workingday_create`, and a one-line note: "Dates here override the weekend (e.g. an occasional working Saturday)."

- [ ] **Step 5: Nav** — in `templates/base.html`, in the HR Attendance submenu (next to Settings), add:
```html
<li class="nav-item">
    <a class="nav-link py-1 {% if 'workingday' in request.resolver_match.url_name %}active{% endif %}" href="{% url 'hr:workingday_list' %}">
        <i class="bi bi-calendar2-week"></i> <span>Working Days</span>
    </a>
</li>
```

- [ ] **Step 6: Test** — append:
```python
class WorkingDayViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()

    def test_list_ok(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('hr:workingday_list')).status_code, 200)

    def test_create(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:workingday_create'), {'date': '2026-07-18', 'name': 'WS', 'is_active': 'on'})
        from hr.models import WorkingDay
        self.assertEqual(WorkingDay.objects.filter(name='WS').count(), 1)
```
Run: `python manage.py test hr.tests.WorkingDayViewTests -v 2` → 2 PASS. Then `python manage.py check`.

- [ ] **Step 7: Commit**
```
git add hr/forms.py hr/views.py hr/urls.py templates/ hr/tests.py
git commit -m "HR: WorkingDay CRUD + nav"
```

---

## Task 6: Late badge/CSS + settings field + legends

**Files:** `templates/base.html`, `hr/forms.py`, `hr/views.py`, `templates/hr/attendance_settings.html`, `templates/hr/_attend_cell.html`, `attendance_matrix.html`, `attendance_grid.html`, `attendance_history.html`, `hr/tests.py`

- [ ] **Step 1: CSS** — in `templates/base.html` `<style>`, add:
```css
.badge-late { background:#fd7e14; color:#fff; }
.badge-wfh { background:#6f42c1; color:#fff; }
```

- [ ] **Step 2: Settings form + view** — in `hr/forms.py`, change `AttendanceSettingsForm` to a `ModelForm`-style or add an `expected_in_by` field. Simplest: add a field to the existing `AttendanceSettingsForm`:
```python
    expected_in_by = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}, format='%H:%M'),
        input_formats=['%H:%M'])
```
and update `initial_from` to also set `self.initial['expected_in_by'] = settings_obj.expected_in_by`. In `hr/views.py` `attendance_settings`, in the POST branch set `obj.expected_in_by = form.cleaned_data.get('expected_in_by') or obj.expected_in_by` before `obj.save()`. In `templates/hr/attendance_settings.html`, render `{{ form.expected_in_by }}` with a label "Expected in-by (Late after)".

- [ ] **Step 3: Late badges** — add `late`/`wfh` branches to the three badge spots:
  - `templates/hr/_attend_cell.html` (matrix): add `{% elif status == 'late' %}<span class="badge badge-late">LT</span>{% elif status == 'wfh' %}<span class="badge badge-wfh">RM</span>` before the weekend branch.
  - `templates/hr/attendance_matrix.html` legend: add `<span class="badge badge-late">LT</span> Late` and `<span class="badge badge-wfh">RM</span> WFH`; and in the JS `BADGE` map add `late:'<span class="badge badge-late">LT</span>', wfh:'<span class="badge badge-wfh">RM</span>'`.
  - `templates/hr/attendance_grid.html` status cell (working rows): add `{% elif row.status == 'late' %}<span class="badge badge-late">Late</span>{% elif row.status == 'wfh' %}<span class="badge badge-wfh">WFH</span>` to the `{% if row.status == 'present' %}...{% endif %}` chain.
  - `templates/hr/attendance_history.html`: add `{% elif r.status == 'late' %}<span class="badge badge-late">Late</span>{% elif r.status == 'wfh' %}<span class="badge badge-wfh">WFH</span>`.

- [ ] **Step 4: Test** — append:
```python
class SettingsLateTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()

    def test_update_expected_in_by(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:attendance_settings'), {'weekend_days': ['4', '5'], 'expected_in_by': '09:00'})
        from hr.models import AttendanceSettings
        self.assertEqual(AttendanceSettings.load().expected_in_by, _time(9, 0))
```
Run: `python manage.py test hr.tests.SettingsLateTests -v 2` → PASS. Then `python manage.py check`.

- [ ] **Step 5: Commit**
```
git add templates/ hr/forms.py hr/views.py hr/tests.py
git commit -m "HR: Late badge/colors + expected_in_by setting + legends"
```

---

## Task 7: Phase 1 regression

- [ ] Run `python manage.py test hr -v 1` (all pass) and `python manage.py test -v 1` (no new failures) and `python manage.py check` and `python manage.py makemigrations --check --dry-run` (No changes detected).
- [ ] Manual smoke: set Expected in-by 08:30; on the grid, a check-in 09:00 → saves as **Late** (orange). Add a Working Day for a Saturday → the matrix column for it is unshaded and editable; mark someone present that Saturday.

---

# PHASE 2 — WFH

## Task 8: WFHRecord model

**Files:** `hr/models/leave.py` (or `attendance.py`), `hr/models/__init__.py`, migration, `hr/admin.py`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class WFHRecordModelTests(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_create_and_clean(self):
        from django.core.exceptions import ValidationError
        from hr.models import WFHRecord
        r = WFHRecord(employee=self.emp, start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 15))
        r.full_clean(); r.save()
        self.assertEqual(WFHRecord.objects.count(), 1)
        bad = WFHRecord(employee=self.emp, start_date=_date(2026, 7, 15), end_date=_date(2026, 7, 13))
        with self.assertRaises(ValidationError):
            bad.full_clean()
```

- [ ] **Step 2: Run** `python manage.py test hr.tests.WFHRecordModelTests -v 2` → FAIL (ImportError).

- [ ] **Step 3: Implement** — append to `hr/models/attendance.py`:
```python
class WFHRecord(models.Model):
    """A work-from-home period. Worked time (no leave balance)."""
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='wfh_records')
    start_date = models.DateField()
    end_date = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        indexes = [models.Index(fields=['employee', 'start_date'])]

    def __str__(self):
        return f"WFH {self.employee.full_name} {self.start_date}..{self.end_date}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})
```
(`settings` is already imported in `hr/models/attendance.py`.) Add `WFHRecord` to `__init__.py` + `__all__`.

- [ ] **Step 4: Migrate** `python manage.py makemigrations hr` (`0015_*`) + `migrate`. Admin: register with `list_display = ['employee', 'start_date', 'end_date']`, `search_fields = ['employee__full_name']`.

- [ ] **Step 5: Run** the test → PASS. `python manage.py check`.

- [ ] **Step 6: Commit**
```
git add hr/models/ hr/migrations/ hr/admin.py hr/tests.py
git commit -m "HR: WFHRecord model (multi-day work-from-home)"
```

---

## Task 9: derive_status — WFH branch

**Files:** `hr/attendance_services.py`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class DeriveWFHTests(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_wfh_record_makes_day_wfh(self):
        from hr.models import WFHRecord
        from hr.attendance_services import derive_status
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13))
        status, hours = derive_status(self.emp, _date(2026, 7, 13), _time(8, 0), _time(17, 0))
        self.assertEqual(status, 'wfh')
        self.assertEqual(hours, Decimal('9.00'))  # hours still counted

    def test_weekend_beats_wfh(self):
        from hr.models import WFHRecord
        from hr.attendance_services import derive_status
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 11), end_date=_date(2026, 7, 11))
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 11), None)[0], 'weekend')  # Saturday
```

- [ ] **Step 2: Run** → FAIL (no wfh).

- [ ] **Step 3: Implement** — in `derive_status` (`hr/attendance_services.py`), add the WFH branch right after the weekend block and before the `if check_in:` block:
```python
    from hr.models import WFHRecord
    if WFHRecord.objects.filter(employee=employee, start_date__lte=d, end_date__gte=d).exists():
        return 'wfh', _hours_between(check_in, check_out)
```
(Add `WFHRecord` to the existing local `from hr.models import ...` line instead of a second import.)

- [ ] **Step 4: Run** the test + `python manage.py test hr -v 1` → all PASS. `python manage.py check`.

- [ ] **Step 5: Commit**
```
git add hr/attendance_services.py hr/tests.py
git commit -m "HR: derive_status — WFH branch (record -> wfh, hours kept)"
```

---

## Task 10: build_matrix — WFH cell

**Files:** `hr/attendance_matrix.py`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class MatrixWFHTests(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_wfh_record_shows_in_matrix(self):
        from hr.models import WFHRecord
        from hr.attendance_matrix import build_matrix
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13))
        days, rows = build_matrix([self.emp], _date(2026, 7, 13), _date(2026, 7, 13))
        self.assertEqual(rows[0]['cells'][0]['status'], 'wfh')
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — in `build_matrix`:
  - Add `WFHRecord` to the local `from hr.models import ...`.
  - Build a wfh day-set after the leave_cell loop:
    ```python
    wfh_cells = set()
    for wr in WFHRecord.objects.filter(employee_id__in=emp_ids, start_date__lte=end, end_date__gte=start):
        dd = max(wr.start_date, start); last = min(wr.end_date, end)
        while dd <= last:
            wfh_cells.add((wr.employee_id, dd)); dd += timedelta(days=1)
    ```
  - In the cell precedence chain (after the holiday/weekend branches, before `else`), add:
    ```python
            elif key in wfh_cells:
                status = 'wfh'
    ```
    so the order is: stored record > leave > holiday > weekend(via weekend_dates) > wfh > ''.

- [ ] **Step 4: Run** the test + `MatrixWorkingDayTests` + `MatrixHelperTests` → PASS. `python manage.py check`.

- [ ] **Step 5: Commit**
```
git add hr/attendance_matrix.py hr/tests.py
git commit -m "HR: matrix shows WFH cells"
```

---

## Task 11: WFH multi-day form + list + delete + nav

**Files:** `hr/forms.py`, `hr/views.py`, `hr/urls.py`, `templates/hr/wfhrecord_form.html`, `wfhrecord_list.html`, `wfhrecord_confirm_delete.html`, `templates/base.html`, `hr/tests.py`

- [ ] **Step 1: Form** — append to `hr/forms.py` (add `WFHRecord` to top import):
```python
class WFHRecordForm(forms.ModelForm):
    class Meta:
        model = WFHRecord
        fields = ['employee', 'start_date', 'end_date', 'note']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'note': forms.TextInput(attrs={'class': 'form-control'}),
        }
```

- [ ] **Step 2: Views** — append to `hr/views.py` (mirror the LeaveRecord views):
```python
class WFHRecordListView(AdminRequiredMixin, ListView):
    model = WFHRecord
    template_name = 'hr/wfhrecord_list.html'
    context_object_name = 'wfh_records'
    paginate_by = 25
    def get_queryset(self):
        return WFHRecord.objects.select_related('employee').all()

class WFHRecordCreateView(AdminRequiredMixin, CreateView):
    model = WFHRecord
    form_class = WFHRecordForm
    template_name = 'hr/wfhrecord_form.html'
    success_url = reverse_lazy('hr:wfh_list')
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'WFH recorded.')
        return super().form_valid(form)
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs); ctx['title'] = 'Record WFH'; ctx['button_text'] = 'Save WFH'; return ctx

class WFHRecordDeleteView(AdminRequiredMixin, DeleteView):
    model = WFHRecord
    template_name = 'hr/wfhrecord_confirm_delete.html'
    success_url = reverse_lazy('hr:wfh_list')
```
Add `WFHRecord` to the models import and `WFHRecordForm` to the forms import (top of file).

- [ ] **Step 3: URLs**:
```python
    path('wfh/', views.WFHRecordListView.as_view(), name='wfh_list'),
    path('wfh/create/', views.WFHRecordCreateView.as_view(), name='wfh_create'),
    path('wfh/<int:pk>/delete/', views.WFHRecordDeleteView.as_view(), name='wfh_delete'),
```

- [ ] **Step 4: Templates** — create the three mirroring `leaverecord_form.html` / a simple list / `leaverecord_confirm_delete.html`. `wfhrecord_list.html`: table Employee | Start | End | Note | Delete + "Record WFH" button to `hr:wfh_create`. Heading "Work From Home".

- [ ] **Step 5: Nav** — add under HR Attendance submenu:
```html
<li class="nav-item">
    <a class="nav-link py-1 {% if 'wfh' in request.resolver_match.url_name %}active{% endif %}" href="{% url 'hr:wfh_list' %}">
        <i class="bi bi-house-door"></i> <span>Work From Home</span>
    </a>
</li>
```

- [ ] **Step 6: Test** — append:
```python
class WFHViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()

    def test_create_wfh(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:wfh_create'), {'employee': self.emp.pk,
                         'start_date': '2026-07-13', 'end_date': '2026-07-15', 'note': ''})
        from hr.models import WFHRecord
        self.assertEqual(WFHRecord.objects.filter(employee=self.emp).count(), 1)
```
Run → PASS. `python manage.py check`.

- [ ] **Step 7: Commit**
```
git add hr/forms.py hr/views.py hr/urls.py templates/ hr/tests.py
git commit -m "HR: WFH multi-day record form/list/delete + nav"
```

---

## Task 12: Grid per-day WFH button + bulk-save reconciliation

**Files:** `hr/views.py` (attendance_grid POST), `templates/hr/attendance_grid.html`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class GridWFHTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()

    def test_grid_wfh_flag_creates_record_and_status(self):
        from hr.models import WFHRecord, AttendanceRecord
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:attendance_grid') + '?date=2026-07-13', {
            'date': '2026-07-13',
            f'wfh_{self.emp.pk}': '1',
            f'check_in_{self.emp.pk}': '08:15',
            f'check_out_{self.emp.pk}': '18:00',
        })
        self.assertEqual(WFHRecord.objects.filter(employee=self.emp, start_date=_date(2026, 7, 13),
                                                  end_date=_date(2026, 7, 13)).count(), 1)
        self.assertEqual(AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 13)).status, 'wfh')

    def test_grid_unflag_wfh_removes_single_day_record(self):
        from hr.models import WFHRecord, AttendanceRecord
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13))
        self.client.force_login(self.admin)
        # save again WITHOUT the wfh flag (and with present times) -> remove the 1-day WFH, becomes present
        self.client.post(reverse('hr:attendance_grid') + '?date=2026-07-13', {
            'date': '2026-07-13',
            f'check_in_{self.emp.pk}': '08:00', f'check_out_{self.emp.pk}': '17:00',
        })
        self.assertFalse(WFHRecord.objects.filter(employee=self.emp, start_date=_date(2026, 7, 13),
                                                  end_date=_date(2026, 7, 13)).exists())
        self.assertEqual(AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 13)).status, 'present')

    def test_grid_does_not_touch_multiday_wfh(self):
        from hr.models import WFHRecord
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 15))
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:attendance_grid') + '?date=2026-07-13', {'date': '2026-07-13'})
        self.assertTrue(WFHRecord.objects.filter(employee=self.emp, start_date=_date(2026, 7, 13),
                                                 end_date=_date(2026, 7, 15)).exists())  # multi-day untouched
```

- [ ] **Step 2: Run** `python manage.py test hr.tests.GridWFHTests -v 2` → FAIL (wfh flag ignored).

- [ ] **Step 3: Implement** — in `hr/views.py` `attendance_grid`, in the POST loop, BEFORE `status, hours = derive_status(...)`, reconcile the per-day WFH flag (import `WFHRecord` at top if not already):
```python
        for emp in employees:
            ci = request.POST.get(f'check_in_{emp.pk}') or None
            co = request.POST.get(f'check_out_{emp.pk}') or None
            ci_t = datetime.strptime(ci, '%H:%M').time() if ci else None
            co_t = datetime.strptime(co, '%H:%M').time() if co else None
            # Per-day WFH flag -> create/remove a 1-day WFHRecord (never a multi-day one).
            wants_wfh = request.POST.get(f'wfh_{emp.pk}') == '1'
            if wants_wfh:
                WFHRecord.objects.get_or_create(
                    employee=emp, start_date=day, end_date=day,
                    defaults={'created_by': request.user})
            else:
                WFHRecord.objects.filter(employee=emp, start_date=day, end_date=day).delete()
            status, hours = derive_status(emp, day, ci_t, co_t)
            AttendanceRecord.objects.update_or_create(
                employee=emp, date=day,
                defaults={'check_in': ci_t, 'check_out': co_t, 'status': status,
                          'hours_worked': hours, 'created_by': request.user})
```
Note: `derive_status` already returns 'wfh' when a WFHRecord covers the day (Task 9), so status syncs automatically. The `.filter(...).delete()` only targets the **single-day** record (start==end==day) — multi-day records are untouched.

Also, in the GET row-build, expose whether each row is currently a single-day WFH so the template can pre-flag it. Replace the `locked = ...` / `rows.append(...)` lines with:
```python
        rec = existing.get(emp.pk)
        preview_status, _ph = derive_status(emp, day, rec.check_in if rec else None,
                                            rec.check_out if rec else None)
        # leave/holiday/weekend lock the row; wfh stays editable (it's a working day)
        locked = preview_status in ('leave', 'holiday', 'weekend')
        is_wfh = WFHRecord.objects.filter(employee=emp, start_date=day, end_date=day).exists()
        rows.append({'employee': emp, 'record': rec, 'status': rec.status if rec else preview_status,
                     'locked': locked, 'is_wfh': is_wfh})
```

- [ ] **Step 4: Template** — in `templates/hr/attendance_grid.html`, on each unlocked row add a hidden WFH flag + a WFH button beside Present/Absent:
```html
<input type="hidden" name="wfh_{{ row.employee.pk }}" id="wfhflag-{{ row.employee.pk }}" value="{% if row.is_wfh %}1{% else %}0{% endif %}">
<button type="button" class="btn btn-sm btn-outline-secondary wfh-btn" data-pk="{{ row.employee.pk }}"
        title="Mark Work From Home (fills default times)">
    <i class="bi bi-house-door"></i> WFH
</button>
```
In the grid JS, add `BADGE.wfh = '<span class="badge badge-wfh">WFH</span>'`, a `setFlag(pk, v)` that sets the hidden input, and:
- `wfh-btn` click → `fillRow(pk)` (default times) + `setFlag(pk, '1')` + `setBadge(pk, 'wfh')` + `markDirty()`.
- In `fillRow` (Present) and `clearRow` (Absent), also `setFlag(pk, '0')` so choosing Present/Absent clears the WFH flag.
Where `setBadge` only knows present/absent today — extend its `BADGE` map with `wfh` (done above).

- [ ] **Step 5: Run** `python manage.py test hr.tests.GridWFHTests hr.tests.AttendanceGridTests -v 2` → PASS. `python manage.py check`.

- [ ] **Step 6: Commit**
```
git add hr/views.py templates/hr/attendance_grid.html hr/tests.py
git commit -m "HR: grid per-day WFH button + bulk-save reconciliation"
```

---

## Task 13: WFH summary counts

**Files:** `hr/views.py` (AttendanceHistoryView), `templates/hr/attendance_history.html`, `hr/tests.py`

- [ ] **Step 1: Failing test** — append:
```python
class HistoryLateWFHSummaryTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()

    def test_summary_counts_late_and_wfh(self):
        from hr.models import AttendanceRecord
        AttendanceRecord.objects.create(employee=self.emp, date=_date(2026, 7, 13), status='late')
        AttendanceRecord.objects.create(employee=self.emp, date=_date(2026, 7, 14), status='wfh')
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_history', kwargs={'pk': self.emp.pk}) + '?year=2026&month=7')
        self.assertEqual(resp.context['summary']['late'], 1)
        self.assertEqual(resp.context['summary']['wfh'], 1)
```

- [ ] **Step 2: Run** → FAIL (KeyError late/wfh).

- [ ] **Step 3: Implement** — in `AttendanceHistoryView.get_context_data`, add `late` and `wfh` to the `summary` dict:
```python
                'late': counts.get('late', 0), 'wfh': counts.get('wfh', 0),
```
In `templates/hr/attendance_history.html`, add two summary cards (Late, WFH) using `{{ summary.late }}` / `{{ summary.wfh }}` with the new badge colors.

- [ ] **Step 4: Run** the test → PASS. `python manage.py check`.

- [ ] **Step 5: Commit**
```
git add hr/views.py templates/hr/attendance_history.html hr/tests.py
git commit -m "HR: attendance history summary includes Late + WFH"
```

---

## Task 14: Full regression + final review

- [ ] `python manage.py test hr -v 1` (all pass) and `python manage.py test -v 1` (no new failures).
- [ ] `python manage.py check` (clean) and `python manage.py makemigrations --check --dry-run` (No changes detected).
- [ ] Manual smoke: record a multi-day WFH → those days show RM in the register; on the grid, click WFH on a row → badge WFH, Save all → register shows RM, history counts it; a working-Saturday with someone marked late shows LT.
- [ ] Final commit if stragglers.

---

## Self-review notes (addressed)

- **Spec coverage:** expected_in_by + choices (T1); WorkingDay model (T2) + derivation (T3) + matrix (T4) + CRUD (T5); Late badge/settings/legends (T6); WFHRecord (T8) + derivation (T9) + matrix (T10) + multi-day form (T11) + grid button/reconcile (T12) + summary (T13). Precedence leave>holiday>weekend(unless WorkingDay)>wfh>late/present>absent implemented in T3+T9 and mirrored in build_matrix T4+T10.
- **Naming consistency:** `expected_in_by`, `WorkingDay`, `WFHRecord`, statuses `'late'`/`'wfh'`, codes `LT`/`RM`, classes `.badge-late`/`.badge-wfh`, url names `workingday_*` / `wfh_*`, `build_matrix(..., with_weekend_dates=False)` and `weekend_dates` used consistently across helper, view, template, tests.
- **Migration safety:** T1 (field+choices), T2 (WorkingDay), T8 (WFHRecord) each one migration; T7/T14 assert `makemigrations --check` clean.
- **Known follow-ups (out of scope):** matrix click-to-mark WFH toggle; per-role late thresholds; WFH balance.
```
