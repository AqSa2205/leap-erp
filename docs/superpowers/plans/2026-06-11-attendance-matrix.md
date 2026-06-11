# Attendance Matrix (Weekly/Monthly Register) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an all-employees weekly/monthly attendance register (matrix) where HR can mark any employee on leave by clicking a cell.

**Architecture:** A batched, unit-testable `build_matrix(employees, start, end)` helper computes the grid in ~4 queries; a function view renders it with week/month + prev/next navigation; two admin-gated AJAX endpoints create/remove a 1-day `LeaveRecord` and keep the day's `AttendanceRecord` consistent. No new models/migrations — reuses `AttendanceRecord`, `LeaveRecord`, `Holiday`, `AttendanceSettings`, `derive_status`.

**Tech Stack:** Django, Django test runner (`python manage.py test hr`), Bootstrap 5 table, vanilla-JS AJAX with `{{ csrf_token }}`.

**Spec:** `docs/superpowers/specs/2026-06-11-attendance-matrix-design.md`

**Conventions (already in the hr app):**
- `AdminRequiredMixin` / function-view admin gate: `if not (request.user.is_super_admin_user or request.user.is_admin_user): messages.error(...); return redirect('hr:hr_dashboard')`.
- `_parse_date(s)` helper already in `hr/views.py` (returns today on bad input).
- `derive_status(employee, d, check_in, check_out=None)` in `hr/attendance_services.py` → `(status, hours)`.
- `AttendanceSettings.load().weekend_day_set()` → set of weekday ints (Mon=0..Sun=6; default {4,5}).
- Models: `AttendanceRecord(employee, date, check_in, check_out, status, hours_worked)` unique (employee,date); `LeaveRecord(employee, leave_type, start_date, end_date, days)`; `LeaveType(is_active, name)`; `Holiday(date, is_active)`.
- `@require_POST` and `from django.db import transaction` — `require_POST` is already imported in `hr/views.py` (added for `attendance_regenerate`). Add `transaction` import if missing.
- Test admin-user setUp pattern (from existing hr tests):
  ```python
  from accounts.models import Role, User
  role, _ = Role.objects.get_or_create(name=Role.ADMIN)
  self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
  ```
- `make_employee(iqama='E1', name='Ali', joining=None)` helper exists in `hr/tests.py`.
- Test runner: `python manage.py test hr -v 2`.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `hr/attendance_matrix.py` | `period_range`, `build_matrix`, `display_status_no_record` (pure, batched, testable) | Create |
| `hr/views.py` | `attendance_matrix`, `attendance_mark_leave`, `attendance_unmark_leave` | Modify |
| `hr/urls.py` | 3 routes | Modify |
| `templates/hr/attendance_matrix.html` | register table + toolbar + click-to-mark JS | Create |
| `templates/base.html` | "Register" nav link under Attendance submenu | Modify |
| `hr/tests.py` | tests | Modify |

---

## Task M1: Matrix computation helpers

**Files:**
- Create: `hr/attendance_matrix.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append to `hr/tests.py` (reuse `make_employee`, `_date`, `Decimal`, `LeaveType`, `LeaveRecord`, `Holiday`, `AttendanceRecord` already imported):

```python
from hr.attendance_matrix import period_range, build_matrix, display_status_no_record


class MatrixHelperTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def test_week_range_starts_sunday(self):
        # 2026-07-15 is a Wednesday; its week (Sun-start) is 12 Jul (Sun) .. 18 Jul (Sat)
        start, end = period_range('week', _date(2026, 7, 15))
        self.assertEqual(start, _date(2026, 7, 12))
        self.assertEqual(end, _date(2026, 7, 18))

    def test_month_range_full_month(self):
        start, end = period_range('month', _date(2026, 7, 15))
        self.assertEqual(start, _date(2026, 7, 1))
        self.assertEqual(end, _date(2026, 7, 31))

    def test_stored_record_status_wins(self):
        AttendanceRecord.objects.create(employee=self.emp, date=_date(2026, 7, 13), status='present', hours_worked=Decimal('8'))
        days, rows = build_matrix([self.emp], _date(2026, 7, 13), _date(2026, 7, 13))
        self.assertEqual(rows[0]['cells'][0]['status'], 'present')

    def test_leave_holiday_weekend_blank_precedence(self):
        # Mon 13 = leave (single-day record), Tue 14 = holiday, Fri 10 = weekend, Thu 16 = blank
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13), days=Decimal('1'))
        Holiday.objects.create(date=_date(2026, 7, 14), name='X')
        days, rows = build_matrix([self.emp], _date(2026, 7, 10), _date(2026, 7, 16))
        by_date = {c['date']: c for c in rows[0]['cells']}
        self.assertEqual(by_date[_date(2026, 7, 13)]['status'], 'leave')
        self.assertEqual(by_date[_date(2026, 7, 13)]['leave_record_id'],
                         LeaveRecord.objects.get(employee=self.emp).pk)  # single-day -> removable
        self.assertEqual(by_date[_date(2026, 7, 14)]['status'], 'holiday')
        self.assertEqual(by_date[_date(2026, 7, 10)]['status'], 'weekend')  # Friday
        self.assertEqual(by_date[_date(2026, 7, 16)]['status'], '')         # Thursday, no record

    def test_multiday_leave_has_no_removable_id(self):
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 15), days=Decimal('3'))
        days, rows = build_matrix([self.emp], _date(2026, 7, 13), _date(2026, 7, 15))
        for c in rows[0]['cells']:
            self.assertEqual(c['status'], 'leave')
            self.assertIsNone(c['leave_record_id'])  # multi-day -> not cell-removable

    def test_display_status_no_record(self):
        Holiday.objects.create(date=_date(2026, 7, 14), name='X')
        self.assertEqual(display_status_no_record(_date(2026, 7, 14)), 'holiday')
        self.assertEqual(display_status_no_record(_date(2026, 7, 10)), 'weekend')  # Friday
        self.assertEqual(display_status_no_record(_date(2026, 7, 16)), '')          # Thursday
```

- [ ] **Step 2: Run — expect failure**

Run: `python manage.py test hr.tests.MatrixHelperTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'hr.attendance_matrix'`

- [ ] **Step 3: Implement** — create `hr/attendance_matrix.py`:

```python
"""Batched computation for the attendance register (matrix).

Pure helpers (no HTTP) so the grid logic is unit-testable. Cell precedence
mirrors derive_status: a stored AttendanceRecord.status wins; else
leave > holiday > weekend; else '' (blank = not yet recorded).
"""
import calendar
from datetime import timedelta


def period_range(period, anchor):
    """Return (start, end) dates for the week (Sun-start) or month containing `anchor`."""
    if period == 'week':
        days_since_sunday = (anchor.weekday() + 1) % 7   # Mon=0..Sun=6 -> Sun-start
        start = anchor - timedelta(days=days_since_sunday)
        end = start + timedelta(days=6)
    else:  # 'month'
        start = anchor.replace(day=1)
        last_day = calendar.monthrange(anchor.year, anchor.month)[1]
        end = anchor.replace(day=last_day)
    return start, end


def display_status_no_record(d):
    """Cell status for a day with no AttendanceRecord and no leave: holiday/weekend/''."""
    from hr.models import Holiday, AttendanceSettings
    if Holiday.objects.filter(date=d, is_active=True).exists():
        return 'holiday'
    if d.weekday() in AttendanceSettings.load().weekend_day_set():
        return 'weekend'
    return ''


def build_matrix(employees, start, end):
    """Return (days, rows). `days` is the list of dates; `rows` is
    [{'employee', 'cells': [{'date','status','leave_record_id','locked'}]}].
    Batched: ~4 queries regardless of grid size."""
    from hr.models import AttendanceRecord, LeaveRecord, Holiday, AttendanceSettings

    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)

    emp_ids = [e.pk for e in employees]

    rec_status = {
        (r.employee_id, r.date): r.status
        for r in AttendanceRecord.objects.filter(employee_id__in=emp_ids, date__range=(start, end))
    }

    # (emp_id, date) -> leave_record pk if that record is single-day (removable), else None
    leave_cell = {}
    for lr in LeaveRecord.objects.filter(
            employee_id__in=emp_ids, start_date__lte=end, end_date__gte=start):
        removable_pk = lr.pk if lr.start_date == lr.end_date else None
        dd = max(lr.start_date, start)
        last = min(lr.end_date, end)
        while dd <= last:
            leave_cell[(lr.employee_id, dd)] = removable_pk
            dd += timedelta(days=1)

    holidays = set(Holiday.objects.filter(
        is_active=True, date__range=(start, end)).values_list('date', flat=True))
    weekends = AttendanceSettings.load().weekend_day_set()

    rows = []
    for emp in employees:
        cells = []
        for day in days:
            key = (emp.pk, day)
            leave_pk = None
            if key in rec_status:
                status = rec_status[key]
                if status == 'leave':
                    leave_pk = leave_cell.get(key)
            elif key in leave_cell:
                status = 'leave'
                leave_pk = leave_cell[key]
            elif day in holidays:
                status = 'holiday'
            elif day.weekday() in weekends:
                status = 'weekend'
            else:
                status = ''
            cells.append({
                'date': day, 'status': status,
                'leave_record_id': leave_pk,
                'locked': status in ('weekend', 'holiday'),
            })
        rows.append({'employee': emp, 'cells': cells})
    return days, rows
```

- [ ] **Step 4: Run — expect pass**

Run: `python manage.py test hr.tests.MatrixHelperTests -v 2`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```
git add hr/attendance_matrix.py hr/tests.py
git commit -m "HR: attendance matrix computation helpers (batched)"
```

---

## Task M2: Matrix view + URL + template + nav

**Files:**
- Modify: `hr/views.py`, `hr/urls.py`, `templates/base.html`
- Create: `templates/hr/attendance_matrix.html`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:

```python
class MatrixViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee(name='Zara Tester')

    def test_matrix_month_renders(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_matrix') + '?period=month&date=2026-07-15')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Zara Tester')
        self.assertContains(resp, 'July')        # period heading
        self.assertEqual(len(resp.context['days']), 31)

    def test_matrix_week_has_7_days(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_matrix') + '?period=week&date=2026-07-15')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['days']), 7)

    def test_matrix_requires_admin(self):
        from accounts.models import Role, User
        rep, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        u = User.objects.create_user('rep', password='x'); u.role = rep; u.save()
        self.client.force_login(u)
        resp = self.client.get(reverse('hr:attendance_matrix'))
        self.assertEqual(resp.status_code, 302)  # admin-gate redirect to hr_dashboard
```

- [ ] **Step 2: Run — expect failure**

Run: `python manage.py test hr.tests.MatrixViewTests -v 2`
Expected: FAIL — `NoReverseMatch: 'attendance_matrix'`

- [ ] **Step 3: Implement the view** — append to `hr/views.py` (imports needed at TOP: `from .attendance_matrix import period_range, build_matrix`; `Employee`, `LeaveType`, `timezone`, `_parse_date`, `messages` already available; add `from datetime import timedelta` if not present):

```python
@login_required
def attendance_matrix(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        messages.error(request, 'Admin access required.')
        return redirect('hr:hr_dashboard')
    period = request.GET.get('period') if request.GET.get('period') in ('week', 'month') else 'month'
    anchor = _parse_date(request.GET.get('date'))
    start, end = period_range(period, anchor)
    employees = list(Employee.objects.filter(is_active=True).order_by('full_name'))
    days, rows = build_matrix(employees, start, end)
    prev_anchor = start - timedelta(days=1)
    next_anchor = end + timedelta(days=1)
    return render(request, 'hr/attendance_matrix.html', {
        'period': period, 'anchor': anchor, 'start': start, 'end': end,
        'days': days, 'rows': rows,
        'prev_anchor': prev_anchor, 'next_anchor': next_anchor,
        'today': timezone.now().date(),
        'leave_types': LeaveType.objects.filter(is_active=True).order_by('name'),
    })
```

- [ ] **Step 4: Add the URL** — in `hr/urls.py`, add (keep with the other attendance routes):

```python
    path('attendance/matrix/', views.attendance_matrix, name='attendance_matrix'),
```

- [ ] **Step 5: Create the template** — `templates/hr/attendance_matrix.html`:

```html
{% extends 'base.html' %}
{% block title %}HR - Attendance Register{% endblock %}
{% block content %}
<div class="container-fluid">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <div>
            <h1 class="h3 mb-1">Attendance Register</h1>
            <p class="text-muted mb-0">{{ start|date:"d M Y" }} — {{ end|date:"d M Y" }}</p>
        </div>
        <a href="{% url 'hr:leave_record_create' %}" class="btn btn-outline-primary">
            <i class="bi bi-calendar-plus"></i> Mark leave (range)
        </a>
    </div>

    <div class="card mb-3"><div class="card-body py-2 d-flex flex-wrap gap-2 align-items-center">
        <div class="btn-group">
            <a class="btn btn-sm {% if period == 'week' %}btn-primary{% else %}btn-outline-primary{% endif %}"
               href="?period=week&date={{ anchor|date:'Y-m-d' }}">Week</a>
            <a class="btn btn-sm {% if period == 'month' %}btn-primary{% else %}btn-outline-primary{% endif %}"
               href="?period=month&date={{ anchor|date:'Y-m-d' }}">Month</a>
        </div>
        <div class="btn-group ms-2">
            <a class="btn btn-sm btn-outline-secondary" href="?period={{ period }}&date={{ prev_anchor|date:'Y-m-d' }}">&lsaquo; Prev</a>
            <a class="btn btn-sm btn-outline-secondary" href="?period={{ period }}&date={{ today|date:'Y-m-d' }}">Today</a>
            <a class="btn btn-sm btn-outline-secondary" href="?period={{ period }}&date={{ next_anchor|date:'Y-m-d' }}">Next &rsaquo;</a>
        </div>
        <div class="ms-auto d-flex align-items-center gap-2">
            <label for="leaveType" class="small text-muted mb-0">Mark-leave type</label>
            <select id="leaveType" class="form-select form-select-sm" style="width:auto;">
                {% for lt in leave_types %}<option value="{{ lt.pk }}">{{ lt.name }}</option>{% endfor %}
            </select>
        </div>
    </div></div>

    <div class="card"><div class="card-body p-0"><div class="table-responsive">
        <table class="table table-bordered table-sm align-middle mb-0" style="white-space:nowrap;">
            <thead class="table-light">
                <tr>
                    <th class="sticky-start ps-3" style="position:sticky;left:0;background:#f8f9fa;z-index:2;">Employee</th>
                    {% for d in days %}
                    <th class="text-center small {% if d.weekday == 4 or d.weekday == 5 %}table-secondary{% endif %}">
                        {{ d|date:"D" }}<br>{{ d|date:"j" }}
                    </th>
                    {% endfor %}
                </tr>
            </thead>
            <tbody>
                {% for row in rows %}
                <tr>
                    <td class="ps-3 fw-semibold" style="position:sticky;left:0;background:#fff;z-index:1;">{{ row.employee.full_name }}</td>
                    {% for c in row.cells %}
                    <td class="text-center attend-cell {% if c.locked %}table-secondary{% endif %}"
                        data-emp="{{ row.employee.pk }}" data-date="{{ c.date|date:'Y-m-d' }}"
                        data-status="{{ c.status }}" data-leave-id="{{ c.leave_record_id|default_if_none:'' }}"
                        {% if not c.locked %}role="button"{% endif %}>
                        {% include 'hr/_attend_cell.html' with status=c.status %}
                    </td>
                    {% endfor %}
                </tr>
                {% empty %}
                <tr><td colspan="{{ days|length|add:1 }}" class="text-center text-muted py-4">No active employees.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div></div></div>
    <div class="small text-muted mt-2">
        Click a working cell to mark the selected leave type; click a single-day leave to remove it.
        <span class="badge bg-success">P</span> Present
        <span class="badge bg-danger">A</span> Absent
        <span class="badge bg-info text-dark">L</span> Leave
        <span class="badge bg-primary">H</span> Holiday
        <span class="badge bg-secondary">W</span> Weekend
    </div>
</div>

<script>
(function () {
    var csrfToken = '{{ csrf_token }}';
    var MARK_URL = "{% url 'hr:attendance_mark_leave' %}";
    var UNMARK_URL = "{% url 'hr:attendance_unmark_leave' %}";
    var BADGE = {present:'<span class="badge bg-success">P</span>', absent:'<span class="badge bg-danger">A</span>',
                 leave:'<span class="badge bg-info text-dark">L</span>', holiday:'<span class="badge bg-primary">H</span>',
                 weekend:'<span class="badge bg-secondary">W</span>', '':'<span class="text-muted">—</span>'};
    function post(url, body) {
        return fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken}, body:JSON.stringify(body)})
            .then(function (r) { return r.json().then(function (j) { return {ok:r.ok, j:j}; }); });
    }
    function render(cell, status, leaveId) {
        cell.dataset.status = status;
        cell.dataset.leaveId = leaveId || '';
        cell.innerHTML = BADGE[status] !== undefined ? BADGE[status] : BADGE[''];
    }
    document.querySelectorAll('.attend-cell').forEach(function (cell) {
        if (cell.classList.contains('table-secondary')) return;  // weekend/holiday locked
        cell.addEventListener('click', function () {
            var status = cell.dataset.status, leaveId = cell.dataset.leaveId;
            if (status === 'leave') {
                if (!leaveId) { alert('Part of a multi-day leave — edit it from the leave summary.'); return; }
                post(UNMARK_URL, {leave_record_id: parseInt(leaveId, 10)}).then(function (res) {
                    if (!res.ok) { alert(res.j.error || 'Failed'); return; }
                    render(cell, res.j.status, null);
                });
            } else {
                var lt = document.getElementById('leaveType').value;
                post(MARK_URL, {employee: parseInt(cell.dataset.emp, 10), date: cell.dataset.date, leave_type: parseInt(lt, 10)})
                    .then(function (res) {
                        if (!res.ok) { alert(res.j.error || 'Failed'); return; }
                        render(cell, 'leave', res.j.leave_record_id);
                    });
            }
        });
    });
})();
</script>
{% endblock %}
```

Also create the tiny partial `templates/hr/_attend_cell.html`:
```html
{% if status == 'present' %}<span class="badge bg-success">P</span>
{% elif status == 'absent' %}<span class="badge bg-danger">A</span>
{% elif status == 'leave' %}<span class="badge bg-info text-dark">L</span>
{% elif status == 'holiday' %}<span class="badge bg-primary">H</span>
{% elif status == 'weekend' %}<span class="badge bg-secondary">W</span>
{% else %}<span class="text-muted">—</span>{% endif %}
```

- [ ] **Step 6: Nav link** — in `templates/base.html`, inside the **Attendance** submenu (added in B4: it has Daily Grid + Settings), add a Register link:
```html
<li class="nav-item">
    <a class="nav-link py-1 {% if request.resolver_match.url_name == 'attendance_matrix' %}active{% endif %}" href="{% url 'hr:attendance_matrix' %}">
        <i class="bi bi-grid-3x3"></i> <span>Register</span>
    </a>
</li>
```
(Place it next to the existing "Daily Grid" link; do not alter the other items.)

- [ ] **Step 7: Run — expect pass** (the AJAX url names in the template require M3/M4 routes to exist; to keep M2 green on its own, add the M3/M4 url names now as the routes are added in those tasks. If `NoReverseMatch` for `attendance_mark_leave`/`attendance_unmark_leave` occurs, proceed to add the two `path(...)` lines from M3 Step 4 and M4 Step 4 now — the views can be added in M3/M4, but the URL names must resolve for the template to render.)

To avoid a chicken-and-egg, add BOTH url lines to `hr/urls.py` in this task:
```python
    path('attendance/mark-leave/', views.attendance_mark_leave, name='attendance_mark_leave'),
    path('attendance/unmark-leave/', views.attendance_unmark_leave, name='attendance_unmark_leave'),
```
and add minimal stub views at the end of `hr/views.py` that M3/M4 will flesh out:
```python
@login_required
@require_POST
def attendance_mark_leave(request):
    return JsonResponse({'error': 'not implemented'}, status=501)

@login_required
@require_POST
def attendance_unmark_leave(request):
    return JsonResponse({'error': 'not implemented'}, status=501)
```
(Confirm `from django.http import JsonResponse` is imported at the top of `hr/views.py`; add it if missing — `HttpResponse` is already imported, add `JsonResponse` alongside.)

Run: `python manage.py test hr.tests.MatrixViewTests -v 2` → 3 PASS. Then `python manage.py check`.

- [ ] **Step 8: Commit**

```
git add hr/views.py hr/urls.py templates/hr/attendance_matrix.html templates/hr/_attend_cell.html templates/base.html hr/tests.py
git commit -m "HR: attendance matrix view + register template + nav (mark endpoints stubbed)"
```

---

## Task M3: Mark-leave AJAX endpoint

**Files:**
- Modify: `hr/views.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:

```python
import json as _json


class MarkLeaveTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def _post(self, payload):
        return self.client.post(reverse('hr:attendance_mark_leave'),
                                data=_json.dumps(payload), content_type='application/json')

    def test_mark_creates_leave_and_attendance(self):
        self.client.force_login(self.admin)
        resp = self._post({'employee': self.emp.pk, 'date': '2026-07-13', 'leave_type': self.annual.pk})
        self.assertEqual(resp.status_code, 200)
        lr = LeaveRecord.objects.get(employee=self.emp)
        self.assertEqual(lr.start_date, _date(2026, 7, 13))
        self.assertEqual(lr.end_date, _date(2026, 7, 13))
        ar = AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 13))
        self.assertEqual(ar.status, 'leave')
        self.assertEqual(resp.json()['leave_record_id'], lr.pk)

    def test_mark_requires_admin(self):
        from accounts.models import Role, User
        rep, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        u = User.objects.create_user('rep', password='x'); u.role = rep; u.save()
        self.client.force_login(u)
        resp = self._post({'employee': self.emp.pk, 'date': '2026-07-13', 'leave_type': self.annual.pk})
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Run — expect failure**

Run: `python manage.py test hr.tests.MarkLeaveTests -v 2`
Expected: FAIL — stub returns 501 (`test_mark_creates_leave_and_attendance` fails) and admin test fails (stub doesn't 403).

- [ ] **Step 3: Implement** — replace the `attendance_mark_leave` stub in `hr/views.py` with (ensure `from django.db import transaction`, `from django.core.exceptions import PermissionDenied`, `import json`, `from .models import LeaveRecord, LeaveType` are imported at top; `AttendanceRecord`, `derive_status`, `_parse_date` already available):

```python
@login_required
@require_POST
def attendance_mark_leave(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        raise PermissionDenied
    try:
        payload = json.loads(request.body or '{}')
        emp_id = int(payload['employee'])
        day = datetime.strptime(payload['date'], '%Y-%m-%d').date()
        lt_id = int(payload['leave_type'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Bad payload'}, status=400)

    employee = Employee.objects.filter(pk=emp_id, is_active=True).first()
    leave_type = LeaveType.objects.filter(pk=lt_id, is_active=True).first()
    if employee is None or leave_type is None:
        return JsonResponse({'error': 'Unknown employee or leave type'}, status=400)

    with transaction.atomic():
        lr = LeaveRecord.objects.create(
            employee=employee, leave_type=leave_type,
            start_date=day, end_date=day, created_by=request.user)
        AttendanceRecord.objects.update_or_create(
            employee=employee, date=day,
            defaults={'status': 'leave', 'check_in': None, 'check_out': None,
                      'hours_worked': None, 'created_by': request.user})
    return JsonResponse({'ok': True, 'status': 'leave', 'leave_record_id': lr.pk})
```

`raise PermissionDenied` returns HTTP 403. Confirm `datetime` is imported (`from datetime import datetime, date` is at the top of hr/views.py).

- [ ] **Step 4: Run — expect pass**

Run: `python manage.py test hr.tests.MarkLeaveTests -v 2` → 2 PASS. Then `python manage.py check`.

- [ ] **Step 5: Commit**

```
git add hr/views.py hr/tests.py
git commit -m "HR: attendance_mark_leave AJAX (1-day leave + attendance sync)"
```

---

## Task M4: Unmark-leave AJAX endpoint (toggle)

**Files:**
- Modify: `hr/views.py`
- Test: `hr/tests.py`

- [ ] **Step 1: Write the failing test** — append:

```python
class UnmarkLeaveTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def _post(self, payload):
        return self.client.post(reverse('hr:attendance_unmark_leave'),
                                data=_json.dumps(payload), content_type='application/json')

    def test_unmark_single_day_deletes_and_rederives(self):
        self.client.force_login(self.admin)
        # mark first (creates a 1-day leave + an attendance row at 'leave')
        self.client.post(reverse('hr:attendance_mark_leave'),
                         data=_json.dumps({'employee': self.emp.pk, 'date': '2026-07-13', 'leave_type': self.annual.pk}),
                         content_type='application/json')
        lr = LeaveRecord.objects.get(employee=self.emp)
        resp = self._post({'leave_record_id': lr.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LeaveRecord.objects.filter(pk=lr.pk).exists())
        # 2026-07-13 is a Monday (working day), no check-in -> attendance row removed -> cell blank
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.emp, date=_date(2026, 7, 13)).exists())
        self.assertEqual(resp.json()['status'], '')

    def test_unmark_rejects_multiday(self):
        self.client.force_login(self.admin)
        lr = LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                        start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 15), days=Decimal('3'))
        resp = self._post({'leave_record_id': lr.pk})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(LeaveRecord.objects.filter(pk=lr.pk).exists())  # not deleted
```

- [ ] **Step 2: Run — expect failure**

Run: `python manage.py test hr.tests.UnmarkLeaveTests -v 2`
Expected: FAIL — stub returns 501.

- [ ] **Step 3: Implement** — replace the `attendance_unmark_leave` stub in `hr/views.py` with (uses `display_status_no_record` — add `from .attendance_matrix import period_range, build_matrix, display_status_no_record` to the top import, extending the M2 import):

```python
@login_required
@require_POST
def attendance_unmark_leave(request):
    if not (request.user.is_super_admin_user or request.user.is_admin_user):
        raise PermissionDenied
    try:
        lr_id = int(json.loads(request.body or '{}')['leave_record_id'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Bad payload'}, status=400)

    lr = LeaveRecord.objects.filter(pk=lr_id).first()
    if lr is None:
        return JsonResponse({'error': 'Not found'}, status=404)
    if lr.start_date != lr.end_date:
        return JsonResponse({'error': 'Part of a multi-day leave — edit from the leave summary.'}, status=400)

    emp_id, day = lr.employee_id, lr.start_date
    with transaction.atomic():
        lr.delete()
        ar = AttendanceRecord.objects.filter(employee_id=emp_id, date=day).first()
        if ar and (ar.check_in or ar.check_out):
            status, hours = derive_status(ar.employee, day, ar.check_in, ar.check_out)
            AttendanceRecord.objects.filter(pk=ar.pk).update(status=status, hours_worked=hours)
            new_status = status
        else:
            if ar:
                ar.delete()  # leave-only row -> restore blank/derived cell
            new_status = display_status_no_record(day)
    return JsonResponse({'ok': True, 'status': new_status})
```

- [ ] **Step 4: Run — expect pass**

Run: `python manage.py test hr.tests.UnmarkLeaveTests -v 2` → 2 PASS. Then `python manage.py check`.

- [ ] **Step 5: Commit**

```
git add hr/views.py hr/tests.py
git commit -m "HR: attendance_unmark_leave AJAX (single-day toggle + re-derive)"
```

---

## Task M5: Full regression + final review

- [ ] **Step 1: Full hr suite** — `python manage.py test hr -v 1` → all pass (existing 42 + ~13 new).
- [ ] **Step 2: Whole project** — `python manage.py test -v 1` → no new failures.
- [ ] **Step 3: Checks** — `python manage.py check` (clean) and `python manage.py makemigrations --check --dry-run` (No changes detected — this feature adds no models).
- [ ] **Step 4: Manual smoke** — as admin: open `/hr/attendance/matrix/?period=month`; toggle Week/Month and Prev/Today/Next; pick a leave type; click a working cell → it turns to **L** and a 1-day leave appears on that employee's leave summary (balance −1); click the same cell → it reverts; confirm weekend/holiday cells aren't clickable; confirm the daily grid for that date now shows the employee locked as Leave.
- [ ] **Step 5: Final commit (if stragglers)** — `git add -A && git commit -m "HR: attendance matrix complete"`.

---

## Self-review notes (addressed)

- **Spec coverage:** batched build + precedence + single/multi-day id (M1); view + week/month + nav + template + nav link (M2); mark endpoint with attendance sync (M3); unmark toggle + multi-day guard + re-derive (M4); regression (M5). Range leave reuses `hr:leave_record_create` (button in M2 template). Read-only-for-attendance honored (no check-in editing in the matrix).
- **Naming consistency:** `period_range`, `build_matrix`, `display_status_no_record`, `attendance_matrix`, `attendance_mark_leave`, `attendance_unmark_leave`, cell keys `status`/`leave_record_id`/`locked` used identically across helper, view, template, and tests.
- **No new migrations:** uses existing models; M5 asserts `makemigrations --check` clean.
- **Chicken-and-egg:** M2 adds the two AJAX url names + 501 stubs so the template's `{% url %}` resolves and M2 tests pass before M3/M4 flesh out the endpoints.
- **Known follow-ups (out of scope):** click-drag multi-cell selection, Excel export of the register, half-day leave.
```
