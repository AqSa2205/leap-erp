# Manager-Logged Leave Requests (Org-Chart-Linked) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a direct manager (per the live `Employee.main_manager` Org Chart relationship — not a named Role) log a leave request for one direct report at a time, through the existing Log Request form, with zero change to balance handling or approval routing.

**Architecture:** A new `is_direct_manager_of_anyone(user)` check widens `LeaveRequestCreateView`'s access gate; the employee dropdown queryset becomes the union of the existing Role-based scope and the user's own active direct reports. Everything downstream (`submit_leave_request`, balance validation, held/warned requests, the Super-Admin-only grant shortcut) is untouched — a manager is just a new kind of caller of code that already exists. A new "My Team" section on My Profile is the discovery entry point.

**Tech Stack:** Django (existing repo patterns only).

## Global Constraints

- Per the spec (`docs/superpowers/specs/2026-08-06-manager-leave-logging-design.md`): **direct reports only**, never the downstream subtree — `is_direct_manager_of_anyone`/the dropdown scoping must never call `get_downstream_employee_ids`.
- Never widen `scoped_employee_ids()`, `scope_asset_queryset()`, or `can_manage_hr_scoped()` — this feature is leave-logging-specific. A manager's new power must not leak into Asset/Attendance/Team-Exceptions visibility as a side effect.
- A manager-logged request must always go through the normal `LeaveDashboardAccess` approver roster — never auto-approved, never treated differently from an HR-logged request once created.
- The Super-Admin-only "Log Anyway" balance-exception-grant shortcut (`has_override_access` gate in `LeaveRequestCreateView.form_valid`) must remain exactly as restrictive as today — a plain manager must never reach it.
- Before generating any migration (none are expected — this plan adds no model fields): `git fetch origin && git log --oneline HEAD..origin/dev` to confirm nothing has landed upstream since this branch was cut from `feat/sarah-leave-entitlement-exceptions`.
- After the shared view/form logic in Task 1 is touched, run the **full** `hr` test suite, not just this feature's new tests.

---

### Task 1: Widen `LeaveRequestCreateView` for direct managers

**Files:**
- Modify: `hr/views.py:47-58` (`HRScopedAccessMixin` — read only, not modified) and `hr/views.py:2110-2136` (`LeaveRequestCreateView`)
- Test: `hr/tests.py`

**Interfaces:**
- Produces: `is_direct_manager_of_anyone(user) -> bool` (new function in `hr/views.py`, placed near `can_view_team_exceptions`).
- Consumes: `scoped_employee_ids` (`hr/scoping.py`, already imported in `hr/views.py`), `Employee.main_reports` (existing related_name on `main_manager`).

- [ ] **Step 1: Write the failing tests**

```python
class DirectManagerLeaveLoggingAccessTests(TestCase):
    def setUp(self):
        self.manager_user = make_user('dmla-mgr', password='x')
        self.manager = Employee.objects.create(
            iqama_number='DMLA-MGR', full_name='Direct Manager', user=self.manager_user)
        self.report = Employee.objects.create(
            iqama_number='DMLA-RPT', full_name='Direct Report', main_manager=self.manager)
        self.other_manager_user = make_user('dmla-other', password='x')
        Employee.objects.create(
            iqama_number='DMLA-OTHERMGR', full_name='Other Manager', user=self.other_manager_user)
        self.stranger = Employee.objects.create(iqama_number='DMLA-STRANGER', full_name='Stranger')
        self.lt, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': Decimal('30')})
        LeaveEntitlement.objects.create(
            employee=self.report, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))

    def test_direct_manager_can_reach_the_log_request_page(self):
        self.client.login(username='dmla-mgr', password='x')
        resp = self.client.get(reverse('hr:leave_request_create'))
        self.assertEqual(resp.status_code, 200)

    def test_direct_manager_sees_only_their_report_in_the_dropdown(self):
        self.client.login(username='dmla-mgr', password='x')
        resp = self.client.get(reverse('hr:leave_request_create'))
        ids = set(resp.context['form'].fields['employee'].queryset.values_list('pk', flat=True))
        self.assertEqual(ids, {self.report.pk})

    def test_manager_with_no_reports_is_denied(self):
        self.client.login(username='dmla-other', password='x')
        resp = self.client.get(reverse('hr:leave_request_create'))
        self.assertEqual(resp.status_code, 403)

    def test_direct_manager_can_log_for_their_report(self):
        self.client.login(username='dmla-mgr', password='x')
        resp = self.client.post(reverse('hr:leave_request_create'), data={
            'employee': self.report.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-03-01', 'end_date': '2026-03-05',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(LeaveRequest.objects.filter(employee=self.report).exists())

    def test_manager_cannot_log_for_a_non_report(self):
        self.client.login(username='dmla-mgr', password='x')
        resp = self.client.post(reverse('hr:leave_request_create'), data={
            'employee': self.stranger.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-03-01', 'end_date': '2026-03-05',
        })
        self.assertEqual(resp.status_code, 200)  # form_invalid re-render, not a 302
        self.assertFalse(LeaveRequest.objects.filter(employee=self.stranger).exists())

    def test_manager_cannot_log_for_their_own_manager(self):
        # Upward abuse: DMLA-MGR reports to nobody here, but prove a manager
        # two levels up can't reach a peer/superior via this dropdown either.
        upline_user = make_user('dmla-upline', password='x')
        upline = Employee.objects.create(iqama_number='DMLA-UPLINE', full_name='Upline', user=upline_user)
        self.manager.main_manager = upline
        self.manager.save(update_fields=['main_manager'])
        self.client.login(username='dmla-mgr', password='x')
        resp = self.client.post(reverse('hr:leave_request_create'), data={
            'employee': upline.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-03-01', 'end_date': '2026-03-05',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LeaveRequest.objects.filter(employee=upline).exists())

    def test_manager_cannot_log_for_a_reports_report(self):
        sub_report = Employee.objects.create(
            iqama_number='DMLA-SUBRPT', full_name='Sub Report', main_manager=self.report)
        self.client.login(username='dmla-mgr', password='x')
        resp = self.client.post(reverse('hr:leave_request_create'), data={
            'employee': sub_report.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-03-01', 'end_date': '2026-03-05',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LeaveRequest.objects.filter(employee=sub_report).exists())

    def test_inactive_report_is_excluded_from_the_dropdown(self):
        self.report.is_active = False
        self.report.save(update_fields=['is_active'])
        self.client.login(username='dmla-mgr', password='x')
        resp = self.client.get(reverse('hr:leave_request_create'))
        ids = set(resp.context['form'].fields['employee'].queryset.values_list('pk', flat=True))
        self.assertNotIn(self.report.pk, ids)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv\Scripts\python.exe manage.py test hr.tests.DirectManagerLeaveLoggingAccessTests -v 2`
Expected: FAIL — `dmla-mgr` currently gets 403 on every request (no Role, `can_manage_hr_scoped` is False).

- [ ] **Step 3: Implement**

In `hr/views.py`, add near `can_view_team_exceptions` (after its closing line, ~129):
```python
def is_direct_manager_of_anyone(user):
    """True if `user` is the live main_manager of at least one active
    employee — the org-chart-derived counterpart to the Role-based
    scoped_employee_ids(). Deliberately checks direct reports only (never
    walks get_downstream_employee_ids) and deliberately does NOT touch
    scoped_employee_ids/scope_asset_queryset — a manager's leave-logging
    power must not leak into Asset/Attendance visibility as a side effect."""
    emp = getattr(user, 'employee_profile', None)
    return bool(emp and emp.main_reports.filter(is_active=True).exists())
```

Change `LeaveRequestCreateView` (replace the class in full):
```python
class LeaveRequestCreateView(HRScopedAccessMixin, FormView):
    # HRScopedAccessMixin covers the Role-based tiers (admin/super_admin/
    # erp_admin, plus site/project_manager, document_controller); test_func
    # is overridden below to also admit anyone who is the live main_manager
    # of at least one active employee, independent of Role. This view serves
    # the "Add Leave" buttons (Entitlements -> Leave Summary / Attendance
    # Matrix) and the My Profile "My Team" section. A submitter can only
    # *log* a request here — approving it in the queue stays with
    # designated approvers, unchanged for manager-logged requests too.
    form_class = LeaveRequestForm
    template_name = 'hr/leave_request_form.html'

    def test_func(self):
        return can_manage_hr_scoped(self.request.user) or is_direct_manager_of_anyone(self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if 'employee' not in form.fields:
            return form
        ids = scoped_employee_ids(self.request.user)
        emp = getattr(self.request.user, 'employee_profile', None)
        direct_report_ids = set(emp.main_reports.filter(is_active=True).values_list('pk', flat=True)) if emp else set()
        if ids is None:
            # Unrestricted (admin tiers) — direct_report_ids adds nothing new, leave as-is.
            pass
        else:
            form.fields['employee'].queryset = form.fields['employee'].queryset.filter(
                pk__in=(ids | direct_report_ids))
        return form

    def get_initial(self):
        initial = super().get_initial()
        employee_id = self.request.GET.get('employee')
        if employee_id:
            initial['employee'] = employee_id
        return initial

    def form_valid(self, form):
        from hr.leave_approval_services import submit_leave_request, grant_exception_days
        from hr.leave_services import preview_leave_shortfall

        employee = form.cleaned_data['employee']
        leave_type = form.cleaned_data['leave_type']
        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date']
        confirmed = bool(self.request.POST.get('confirm_grant_and_log'))
        can_grant = has_override_access(self.request.user)

        if can_grant and not confirmed:
            shortfall = preview_leave_shortfall(employee, leave_type, start_date, end_date)
            if shortfall:
                return self.render_to_response(self.get_context_data(
                    form=form, balance_shortfall=shortfall, balance_warning_employee=employee,
                    balance_warning_leave_type=leave_type))

        try:
            if confirmed:
                if not can_grant:
                    raise PermissionDenied
                shortfall = preview_leave_shortfall(employee, leave_type, start_date, end_date)
                if shortfall:
                    grant_exception_days(
                        employee=employee, leave_type=leave_type, year=start_date.year, days=shortfall,
                        granted_by=self.request.user,
                        reason=f'Auto-granted via Log Request by '
                               f'{self.request.user.get_full_name() or self.request.user.username} to allow a '
                               f'{start_date}–{end_date} request that exceeded the balance by {shortfall} day(s).')
            submit_leave_request(
                employee=employee, leave_type=leave_type, start_date=start_date, end_date=end_date,
                employee_reason=form.cleaned_data['employee_reason'], document=form.cleaned_data['document'],
                created_by=self.request.user,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, 'Leave request logged and sent for approval.')
        return redirect('hr:leave_request_list')
```
(This is the existing `form_valid` body, unchanged — only `test_func` is new and `get_form` is rewritten. Do not alter `form_valid`'s logic — Global Constraints require balance/approval handling to stay identical for every caller.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv\Scripts\python.exe manage.py test hr.tests.DirectManagerLeaveLoggingAccessTests -v 2`
Expected: PASS (9/9).

- [ ] **Step 5: Run the full hr suite (shared view/access-gate code touched)**

Run: `venv\Scripts\python.exe manage.py test hr`
Expected: no new failures beyond the pre-existing 0-failure baseline this branch inherited.

- [ ] **Step 6: Commit**

```bash
git add hr/views.py hr/tests.py
git commit -m "hr: let a direct manager log leave for their own reports"
```

---

### Task 2: Prove manager-logged requests get normal approval routing and balance handling

**Files:**
- Test only: `hr/tests.py` (no production code changes expected — this task exists to prove Task 1 didn't accidentally change shared behavior, per the Global Constraints)

**Interfaces:**
- Consumes: `submit_leave_request`, `LeaveRequestApproval`, `LeaveDashboardAccess`, `has_override_access` (all existing, untouched).

- [ ] **Step 1: Write the tests**

```python
class ManagerLoggedRequestRoutingTests(TestCase):
    def setUp(self):
        self.manager_user = make_user('mlrr-mgr', password='x')
        self.manager = Employee.objects.create(
            iqama_number='MLRR-MGR', full_name='Routing Manager', user=self.manager_user)
        self.report = Employee.objects.create(
            iqama_number='MLRR-RPT', full_name='Routing Report', main_manager=self.manager, work_location='site')
        self.lt, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': Decimal('30'), 'site_default_annual_days': Decimal('45')})
        LeaveEntitlement.objects.create(
            employee=self.report, leave_type=self.lt, year=2026, entitled_days=Decimal('45'))
        self.approver_user = make_user('mlrr-appr', password='x')
        LeaveDashboardAccess.objects.create(user=self.approver_user, is_active=True)

    def test_manager_logged_request_goes_to_the_normal_approver_roster(self):
        self.client.login(username='mlrr-mgr', password='x')
        self.client.post(reverse('hr:leave_request_create'), data={
            'employee': self.report.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-03-01', 'end_date': '2026-03-05',
        })
        req = LeaveRequest.objects.get(employee=self.report)
        self.assertEqual(req.status, 'pending')
        self.assertEqual(list(req.approvals.values_list('approver', flat=True)), [self.approver_user.pk])

    def test_manager_logged_request_is_not_auto_approved(self):
        self.client.login(username='mlrr-mgr', password='x')
        self.client.post(reverse('hr:leave_request_create'), data={
            'employee': self.report.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-03-01', 'end_date': '2026-03-05',
        })
        req = LeaveRequest.objects.get(employee=self.report)
        self.assertNotEqual(req.status, 'approved')

    def test_manager_logged_over_cap_request_is_held_not_blocked(self):
        self.client.login(username='mlrr-mgr', password='x')
        resp = self.client.post(reverse('hr:leave_request_create'), data={
            'employee': self.report.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-01-01', 'end_date': '2026-02-15',  # 46 days, 1 over the 45-day Site baseline
        })
        self.assertEqual(resp.status_code, 302)
        req = LeaveRequest.objects.get(employee=self.report)
        self.assertTrue(req.exceeds_balance)
        self.assertEqual(req.approvals.count(), 1)  # still the normal roster, not held-and-approver-less

    def test_manager_never_sees_the_log_anyway_grant_option(self):
        self.client.login(username='mlrr-mgr', password='x')
        resp = self.client.post(reverse('hr:leave_request_create'), data={
            'employee': self.report.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-01-01', 'end_date': '2026-02-15',
        })
        self.assertNotContains(resp, 'Log Anyway')
        self.assertEqual(LeaveEntitlement.objects.get(employee=self.report).exception_days, Decimal('0'))
```

- [ ] **Step 2: Run tests**

Run: `venv\Scripts\python.exe manage.py test hr.tests.ManagerLoggedRequestRoutingTests -v 2`
Expected: PASS (4/4) with zero production-code changes — if any of these fail, Task 1's `get_form`/`test_func` changes leaked into `form_valid`'s behavior and must be fixed before proceeding.

- [ ] **Step 3: Commit**

```bash
git add hr/tests.py
git commit -m "hr: prove manager-logged requests use normal approval and balance routing"
```

---

### Task 3: "My Team" section on My Profile

**Files:**
- Modify: `hr/views.py` (`my_profile` function, ~line 286-380 region)
- Modify: `templates/hr/my_profile.html`
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `Employee.main_reports` (existing).
- Produces: context key `my_team` (queryset of active direct reports, or `None`/empty when the viewer manages nobody).

- [ ] **Step 1: Write the failing test**

```python
class MyProfileTeamSectionTests(TestCase):
    def setUp(self):
        self.manager_user = make_user('mpts-mgr', password='x')
        self.manager = Employee.objects.create(
            iqama_number='MPTS-MGR', full_name='Team Section Manager', user=self.manager_user)
        self.report = Employee.objects.create(
            iqama_number='MPTS-RPT', full_name='Team Section Report', main_manager=self.manager)
        self.plain_user = make_user('mpts-plain', password='x')
        Employee.objects.create(iqama_number='MPTS-PLAIN', full_name='Plain Employee', user=self.plain_user)

    def test_manager_sees_my_team_section_with_report_and_log_leave_link(self):
        self.client.login(username='mpts-mgr', password='x')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, 'My Team')
        self.assertContains(resp, 'Team Section Report')
        self.assertContains(resp, f"{reverse('hr:leave_request_create')}?employee={self.report.pk}")

    def test_plain_employee_does_not_see_my_team_section(self):
        self.client.login(username='mpts-plain', password='x')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertNotContains(resp, 'My Team')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe manage.py test hr.tests.MyProfileTeamSectionTests -v 2`
Expected: FAIL — no "My Team" text anywhere yet.

- [ ] **Step 3: Implement**

In `hr/views.py`'s `my_profile` function, inside the `if emp:` block (alongside where `context['entitlements']` etc. are set — same indentation level as the `leave_total_exception` line), add:
```python
        context['my_team'] = emp.main_reports.filter(is_active=True).order_by('full_name')
```

In `templates/hr/my_profile.html`, add a new card. Place it as a new `col-lg-6` sibling right after the "Personal & ID" card's closing `</div>` (so it sits in the same top row) — find that closing point and insert:
```html
    {% if my_team %}
    <div class="col-lg-6">
      <div class="card h-100">
        <div class="card-header fw-semibold"><i class="bi bi-people me-1"></i> My Team</div>
        <div class="card-body">
          <table class="table table-sm mb-0">
            <thead class="table-light"><tr><th>Name</th><th>Designation</th><th></th></tr></thead>
            <tbody>
              {% for member in my_team %}
              <tr>
                <td>{{ member.full_name }}</td>
                <td>{{ member.designation|default:"-" }}</td>
                <td class="text-end">
                  <a href="{% url 'hr:leave_request_create' %}?employee={{ member.pk }}" class="btn btn-sm btn-outline-primary">
                    <i class="bi bi-calendar-plus"></i> Log Leave
                  </a>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    {% endif %}
```
(Read the exact current content around the "Personal & ID" card's closing tag first — insert this as a new sibling `col-lg-6` inside the same `row g-3`, not nested inside another card.)

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe manage.py test hr.tests.MyProfileTeamSectionTests -v 2`
Expected: PASS (2/2).

- [ ] **Step 5: Run the full hr suite**

Run: `venv\Scripts\python.exe manage.py test hr`
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add hr/views.py templates/hr/my_profile.html hr/tests.py
git commit -m "hr: add My Team section to My Profile with a Log Leave entry point"
```

---

### Task 4: Demo data and final verification

**Files:**
- Create: `hr/management/commands/seed_manager_leave_demo.py`

- [ ] **Step 1: Write the seed command**

```python
"""Seed local demo data for manually verifying manager-logged leave
requests (direct-manager Org Chart link, not a named Role):

- DEMO-MGRLEAVE-MANAGER: a plain employee (no special Role) with a login,
  set as the main_manager of the employee below.
- DEMO-MGRLEAVE-REPORT: their direct report, with a current-year Annual
  entitlement already set up.

Log in as demo.manager / DemoPass123!, open My Profile, and use the
"My Team" section's Log Leave link to log a request for the report —
it should land in the normal Leave Requests queue, waiting on whoever
holds LeaveDashboardAccess, exactly like any other request.

Safe to re-run: tagged with a "DEMO-MGRLEAVE-" iqama prefix and upserted.
Run ``python manage.py seed_manager_leave_demo --wipe`` to remove it.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from hr.models import Employee, LeaveType, LeaveEntitlement

User = get_user_model()
TAG_PREFIX = 'DEMO-MGRLEAVE-'


class Command(BaseCommand):
    help = 'Seed (or --wipe) demo data for manager-logged leave requests.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Remove the demo data instead of creating it.')

    def handle(self, *args, **options):
        existing = Employee.objects.filter(iqama_number__startswith=TAG_PREFIX)
        if existing.exists():
            existing.delete()
            self.stdout.write(f'Removed {existing.count()} existing demo employee(s).')
        User.objects.filter(username='demo.manager').delete()

        if options['wipe']:
            self.stdout.write(self.style.SUCCESS('Demo data wiped.'))
            return

        year = date.today().year
        lt, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'is_active': True, 'default_annual_days': Decimal('30')})

        manager_user = User.objects.create_user(username='demo.manager', password='DemoPass123!')
        manager = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}MANAGER', full_name='Layla Al-Zahrani (Manager)',
            is_active=True, user=manager_user)
        report = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}REPORT', full_name='Yousef Al-Qahtani (Report)',
            is_active=True, main_manager=manager)
        LeaveEntitlement.objects.create(employee=report, leave_type=lt, year=year, entitled_days=Decimal('30'))

        self.stdout.write(self.style.SUCCESS(
            f'Seeded manager-leave demo for year {year}: log in as demo.manager / DemoPass123!, '
            f'open My Profile, and use the My Team section to log leave for {report.full_name}.'
        ))
```

- [ ] **Step 2: Run it against the real database**

Run: `venv\Scripts\python.exe manage.py seed_manager_leave_demo`
Expected: success message with the login to use.

- [ ] **Step 3: Confirm no migration is needed**

Run: `venv\Scripts\python.exe manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 4: Run the complete cross-app suite once**

Run: `venv\Scripts\python.exe manage.py test`
Expected: same pre-existing, unrelated failures this branch already inherits (confirm the exact set by comparing against the last known-good baseline) — zero new failures.

- [ ] **Step 5: Commit**

```bash
git add hr/management/commands/seed_manager_leave_demo.py
git commit -m "hr: add demo seed command for manager-logged leave requests"
```
