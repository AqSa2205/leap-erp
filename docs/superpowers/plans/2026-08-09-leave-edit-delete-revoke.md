# Leave Request / Team Exception Edit, Delete & Revoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the creator of a still-pending, undecided Leave Request or Team (Attendance) Exception edit or delete it; let an approved request be revoked (voided, without deleting history) either directly by someone with override access, or via an employee-initiated revoke request that the normal decision-makers approve/reject.

**Architecture:** Two independent-but-parallel tracks (Leave Requests, Team Exceptions) share the same shape: new `revoked` terminal status + `revoked_by`/`revoked_at`/`revoke_reason` fields on the existing request model, plus a new one-row-per-request `*RevokeRequest` model for the employee-initiated path. All mutations go through service-layer functions (mirroring the existing `hr/leave_approval_services.py` / `hr/attendance_exception_services.py` pattern) — views stay thin dispatchers. Edit/Delete/Request-Revoke happen inline on **My Profile** (the only page a plain employee/manager can reach for their own requests); Direct Revoke and revoke-request decisions happen on the existing HR/manager queue pages (Leave Approval Queue, Team Exceptions) — no new pages.

**Tech Stack:** Django 5 (function + class-based views), plain `forms.Form` (not ModelForm) for input validation, SQLite (dev/test) — no DB-specific features used, Bootstrap 5 templates.

## Global Constraints

- Edit/delete lock condition (Leave Requests): locks the moment **any** approver has recorded **any** decision (approved or disapproved) — `leave_request.approvals.exclude(decision='pending').exists()`.
- Edit/delete lock condition (Team Exceptions): `status == 'pending'` only (single-decision model, no approvals table).
- Who decides a revoke request: Leave Requests → the same roster as normal decisions (`LeaveDashboardAccess` holders + Super Admins). Team Exceptions → the assigned manager, with override-access holders as fallback (this is a flagged extrapolation in the spec, not explicitly reconfirmed by the user — implement as specified; it's the least-surprising choice and easy to widen later).
- Direct revoke gate: `has_override_access(request.user)` (the exact same permission already gating the balance-override "Log Anyway"/"Override" actions) AND `status == 'approved'`.
- Revoke mechanics (Leave): delete the linked `LeaveRecord` (so `taken_days`/`remaining_days` recompute live) — the `LeaveRequest` row stays, `status='revoked'`.
- Revoke mechanics (Team Exceptions): no linked record — just a status change to `'revoked'`; the attendance-outcome derivation must treat `revoked` the same as "not excused" (same as `rejected`/`expired`).
- Revoked status must render in **both** histories: the requester's own (My Profile) and the decider's queue History (Leave Approval Queue / Team Exceptions) — this falls out for free wherever the existing queryset already includes all non-pending statuses (`exclude(status='pending')` or no filter at all); it must be added explicitly wherever a queryset currently uses an include-list (`status__in=('approved', 'rejected')` in `TeamExceptionsView`).
- UI stays "not cluttered": no new pages. Edit/Delete are small icon-buttons on the existing row, only rendered when the gate passes for the viewer. Approved rows get exactly one of "Request Revoke" (employee) or "Revoke" (override-access holder) — never both to the same viewer. Pending revoke requests get a small badge count reusing the exact sidebar mechanism already built for Leave Requests/Team Exceptions pending counts (`hr/context_processors.py`'s `pending_counts`).
- Self-approval guardrail applies throughout: nobody decides a revoke request (direct or requested) on their own leave/exception, matching every existing decide/override path in this codebase.
- Full `hr` test suite (currently 543 tests, `python manage.py test hr`) must stay green after every task; run the whole app suite (`python manage.py test`) as the final gate, per this project's standing rule of testing the whole suite after a refactor, not just the new feature's own tests.

---

## File Structure

**New files:**
- `hr/migrations/0042_leaverequest_revoke_fields_leaverevokerequest.py` — Task 1
- `hr/migrations/0043_attendanceexception_revoke_fields_and_more.py` — Task 2

**Modified files (grouped by responsibility):**
- `hr/models/leave.py` — `LeaveRequest` revoke fields + `STATUS_CHOICES`; new `LeaveRevokeRequest` model (Task 1)
- `hr/models/attendance_exception.py` — `AttendanceException` revoke fields + `STATUS_CHOICES`; new `AttendanceExceptionRevokeRequest` model (Task 2)
- `hr/models/__init__.py` — export the two new models (Tasks 1, 2)
- `hr/leave_services.py` — `validate_leave_submission`/`preview_leave_shortfall` gain `exclude_request_id` (Task 3)
- `hr/forms.py` — `check_leave_balance`/`LeaveRequestForm` thread `exclude_request_id`; both forms gain an "editing" affordance (Tasks 3, 8)
- `hr/leave_approval_services.py` — `edit_leave_request`, `delete_leave_request` (Task 3); `request_leave_revoke`, `decide_leave_revoke_request`, `revoke_leave_request` (Task 6)
- `hr/attendance_exception_services.py` — `edit_attendance_exception`, `delete_attendance_exception` (Task 5); `request_attendance_exception_revoke`, `decide_attendance_exception_revoke_request`, `revoke_attendance_exception` (Task 7); `_apply_attendance_outcome` learns `revoked` (Task 2)
- `hr/views.py` — `my_profile()` gains 6 new POST actions (Tasks 3, 5, 6, 7); `LeaveRequestListView` gains `post()` (Tasks 6, 8); `LeaveRequestDetailView.post()` gains a `revoke` branch (Task 6); `TeamExceptionsView.post()` gains `revoke_direct`/`decide_revoke_request` branches (Task 7)
- `hr/context_processors.py` — `pending_counts` folds in pending revoke-request counts (Tasks 8, 9)
- `templates/hr/my_profile.html` — Edit/Delete buttons + Request-Revoke button + Revoked badges, on both the "My Leave Requests" table, the "My Reporting Structure" in-behalf list, and the "My Attendance Exceptions" table (Tasks 3, 5, 6, 7)
- `templates/hr/leave_request_list.html` — Direct-Revoke row control + Revoked badge in History; new "Pending Revoke Requests" card (Tasks 6, 8)
- `templates/hr/leave_request_detail.html` — Direct-Revoke control (Task 6)
- `templates/hr/team_exceptions.html` — Direct-Revoke row control + Revoked badge in History (+ include `revoked` in the decided-queryset filter); new "Pending Revoke Requests" card (Task 7, 9)
- `hr/tests.py` — new test classes per task, listed inline below

**Why this split:** each task lands one complete, independently-testable vertical slice (model → service → one surface's UI), matching this codebase's existing file organization (one small model file per concern, one services file per workflow, all views in `hr/views.py`, all templates under `templates/hr/`). Tasks 1–2 (data) must land before anything else; 3 before 8 (edit/delete UI needs the service); 6 before 8/12/14 (revoke UI needs the service); the two tracks (Leave vs. Team Exceptions) are otherwise independent of each other and could be built in either order.

---

### Task 1: Leave Request data model — revoke fields + `LeaveRevokeRequest`

**Files:**
- Modify: `hr/models/leave.py:261-267` (STATUS_CHOICES), `hr/models/leave.py:294-298` (after `override_reason`), `hr/models/leave.py:360-382` (new model, after `LeaveRequestApproval`)
- Modify: `hr/models/__init__.py`
- Create: `hr/migrations/0042_leaverequest_revoke_fields_leaverevokerequest.py`
- Test: `hr/tests.py`

**Interfaces:**
- Produces: `LeaveRequest.status` can now be `'revoked'`; `LeaveRequest.revoked_by` (User FK, nullable), `LeaveRequest.revoked_at` (DateTimeField, nullable), `LeaveRequest.revoke_reason` (TextField). New model `LeaveRevokeRequest` with fields `leave_request` (FK → `LeaveRequest`, `related_name='revoke_requests'`), `requested_by` (FK → User), `reason` (TextField), `status` (`'pending'|'approved'|'rejected'`, default `'pending'`), `decided_by` (FK → User, nullable), `decided_at` (DateTimeField, nullable), `decision_note` (TextField, blank), `created_at` (auto_now_add).

- [ ] **Step 1: Write the failing test**

Add to `hr/tests.py` (after `MyProfileHistoryCollapseTests`/near the other leave model tests — placement doesn't matter, this is additive):

```python
class LeaveRequestRevokeFieldsTests(TestCase):
    def test_revoked_is_a_valid_status_choice(self):
        self.assertIn(('revoked', 'Revoked'), LeaveRequest.STATUS_CHOICES)

    def test_revoke_fields_exist_and_default_empty(self):
        emp = make_employee(iqama='LRRF-1')
        lt, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        req = LeaveRequest.objects.create(
            employee=emp, leave_type=lt, start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 2))
        self.assertIsNone(req.revoked_by)
        self.assertIsNone(req.revoked_at)
        self.assertEqual(req.revoke_reason, '')


class LeaveRevokeRequestModelTests(TestCase):
    def test_create_and_defaults(self):
        emp = make_employee(iqama='LRRM-1')
        lt, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        req = LeaveRequest.objects.create(
            employee=emp, leave_type=lt, start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 2), status='approved')
        user = make_user('lrrm-user', password='x')
        revoke_req = LeaveRevokeRequest.objects.create(
            leave_request=req, requested_by=user, reason='Project emergency, cannot take leave.')
        self.assertEqual(revoke_req.status, 'pending')
        self.assertIsNone(revoke_req.decided_by)
        self.assertIsNone(revoke_req.decided_at)
        self.assertEqual(revoke_req.decision_note, '')
        self.assertEqual(list(req.revoke_requests.all()), [revoke_req])
```

Add `LeaveRevokeRequest` to the existing `from hr.models import (...)` import block near the top of `hr/tests.py` (find the line importing `LeaveRequest` and add it alongside).

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.LeaveRequestRevokeFieldsTests hr.tests.LeaveRevokeRequestModelTests -v 2`
Expected: FAIL / ERROR — `'revoked'` not in `STATUS_CHOICES`, `revoked_by` doesn't exist, `LeaveRevokeRequest` cannot be imported.

- [ ] **Step 3: Write the model changes**

In `hr/models/leave.py`, change the `LeaveRequest.STATUS_CHOICES` list (currently lines 262-267):

```python
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('disapproved', 'Disapproved'),
        ('cancelled', 'Cancelled'),
        ('revoked', 'Revoked'),
    ]
```

Add three fields right after `override_reason = models.TextField(blank=True)` (currently line 298), before `decided_at`:

```python
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='leave_requests_revoked')
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)
```

Add the new model right after `LeaveRequestApproval` ends (after its `__str__`, currently line 381, before `class LeaveRequestNote`):

```python
class LeaveRevokeRequest(models.Model):
    """An employee's request to void their own already-approved leave (e.g.
    a project emergency means they can no longer take it) — decided by the
    same roster that decides normal leave requests (LeaveDashboardAccess +
    Super Admins), NOT by the employee's manager specifically. Distinct from
    a Super Admin's direct revoke (see LeaveRequest.revoked_by/revoked_at/
    revoke_reason) — this model exists only to track the request-and-review
    step; applying the revoke itself still goes through the same mechanism
    (see hr.leave_approval_services.revoke_leave_request)."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='revoke_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(
        blank=True, help_text='Required when rejecting; optional when approving (the reason already explains why).')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Revoke request for leave request #{self.leave_request_id} ({self.status})"
```

In `hr/models/__init__.py`, add `LeaveRevokeRequest` to both the import line and `__all__`:

```python
from .leave import (LeaveType, LeaveEntitlement, LeaveRecord, LeaveExceptionGrant,
                    LeaveDashboardAccess, LeaveRequest, LeaveRequestApproval, LeaveRequestNote,
                    LeaveRevokeRequest,
                    OverrideAccessSettings, OverrideAccessRole, OverrideAccessEmployee)
```
```python
    'LeaveDashboardAccess', 'LeaveRequest', 'LeaveRequestApproval', 'LeaveRequestNote',
    'LeaveRevokeRequest',
```

- [ ] **Step 4: Generate and inspect the migration**

Run: `python manage.py makemigrations hr --name leaverequest_revoke_fields_leaverevokerequest`
Expected: creates `hr/migrations/0042_leaverequest_revoke_fields_leaverevokerequest.py` with `AddField` for `revoked_by`/`revoked_at`/`revoke_reason`, `AlterField` for `status` (updated choices), and `CreateModel` for `LeaveRevokeRequest`. Open the generated file and confirm those four operations are present — do not hand-edit it if Django generated it correctly.

- [ ] **Step 5: Run migration and test**

Run: `python manage.py migrate hr`
Run: `python manage.py test hr.tests.LeaveRequestRevokeFieldsTests hr.tests.LeaveRevokeRequestModelTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/models/leave.py hr/models/__init__.py hr/migrations/0042_leaverequest_revoke_fields_leaverevokerequest.py hr/tests.py
git commit -m "hr: add LeaveRequest revoked status/fields and LeaveRevokeRequest model"
```

---

### Task 2: Attendance Exception data model — revoke fields + `AttendanceExceptionRevokeRequest`

**Files:**
- Modify: `hr/models/attendance_exception.py` (STATUS_CHOICES, new fields, new model)
- Modify: `hr/models/__init__.py`
- Modify: `hr/attendance_exception_services.py:174-199` (`_apply_attendance_outcome`)
- Create: `hr/migrations/0043_attendanceexception_revoke_fields_and_more.py`
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: nothing from Task 1 (independent track).
- Produces: `AttendanceException.status` can be `'revoked'`; `revoked_by`/`revoked_at`/`revoke_reason` fields. New model `AttendanceExceptionRevokeRequest`, same shape as `LeaveRevokeRequest` but `attendance_exception` (FK → `AttendanceException`, `related_name='revoke_requests'`) instead of `leave_request`. `_apply_attendance_outcome` treats `'revoked'` as `'absent'` (day is no longer excused), same as `'rejected'`/`'expired'`.

- [ ] **Step 1: Write the failing test**

Add to `hr/tests.py`:

```python
class AttendanceExceptionRevokeFieldsTests(TestCase):
    def test_revoked_is_a_valid_status_choice(self):
        self.assertIn(('revoked', 'Revoked'), AttendanceException.STATUS_CHOICES)

    def test_revoke_fields_exist_and_default_empty(self):
        emp = make_employee(iqama='AERF-1')
        exc = _submit_aex(employee=emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
                           reason_category='site_visit', created_by=make_user('aerf-creator'))
        self.assertIsNone(exc.revoked_by)
        self.assertIsNone(exc.revoked_at)
        self.assertEqual(exc.revoke_reason, '')


class AttendanceExceptionRevokeRequestModelTests(TestCase):
    def test_create_and_defaults(self):
        emp = make_employee(iqama='AERRM-1')
        exc = _submit_aex(employee=emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
                           reason_category='site_visit', created_by=make_user('aerrm-creator'))
        exc.status = 'approved'
        exc.save(update_fields=['status'])
        user = make_user('aerrm-user', password='x')
        revoke_req = AttendanceExceptionRevokeRequest.objects.create(
            attendance_exception=exc, requested_by=user, reason='No longer needed.')
        self.assertEqual(revoke_req.status, 'pending')
        self.assertEqual(list(exc.revoke_requests.all()), [revoke_req])


class AttendanceOutcomeRevokedTests(TestCase):
    def test_revoked_exception_marks_day_absent(self):
        from hr.attendance_exception_services import _apply_attendance_outcome
        from hr.models import AttendanceRecord
        emp = make_employee(iqama='AORT-1')
        exc = _submit_aex(employee=emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
                           reason_category='site_visit', created_by=make_user('aort-creator'))
        exc.status = 'revoked'
        exc.save(update_fields=['status'])
        _apply_attendance_outcome(exc)
        record = AttendanceRecord.objects.get(employee=emp, date=_date(2026, 7, 20))
        self.assertEqual(record.status, 'absent')
```

Add `AttendanceExceptionRevokeRequest` to the existing `from hr.models import (...)` block that already imports `AttendanceException`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.AttendanceExceptionRevokeFieldsTests hr.tests.AttendanceExceptionRevokeRequestModelTests hr.tests.AttendanceOutcomeRevokedTests -v 2`
Expected: FAIL / ERROR

- [ ] **Step 3: Write the model changes**

In `hr/models/attendance_exception.py`, change `STATUS_CHOICES` (currently lines 28-33):

```python
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
        ('revoked', 'Revoked'),
    ]
```

Add fields right after `override_reason = models.TextField(blank=True)` (currently line 54), before `reminder_sent_at`:

```python
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)
```

Add the new model at the end of the file (after `time_left_seconds`, currently ending line 108):

```python


class AttendanceExceptionRevokeRequest(models.Model):
    """Employee-requested revoke of their own already-approved attendance
    exception — same shape and purpose as hr.models.leave.LeaveRevokeRequest,
    kept as a separate model rather than a shared generic-relation one: the
    two source models differ enough (multi- vs single-approver, linked
    LeaveRecord vs none) that a shared model would need nearly as much
    special-casing per type as just having two, for no real savings."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    attendance_exception = models.ForeignKey(AttendanceException, on_delete=models.CASCADE, related_name='revoke_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Revoke request for exception #{self.attendance_exception_id} ({self.status})"
```

In `hr/models/__init__.py`, add the new model:

```python
from .attendance_exception import AttendanceException, AttendanceExceptionRevokeRequest
```
```python
    'AttendanceException', 'AttendanceExceptionRevokeRequest',
```

In `hr/attendance_exception_services.py`, change `_apply_attendance_outcome` (currently lines 174-199) so the `elif` branch also covers `'revoked'`:

```python
    if exc.status == 'approved':
        # Excused — the Wi-Fi/manual attendance-status derivation layer
        # (attendance.services.sync_hr_attendance / hr.attendance_services.
        # derive_status) is what actually determines late-vs-not from real
        # check-in time when an approved exception exists for that day; this
        # upsert just marks the day as excused/present at decision time.
        new_status = 'present'
    elif exc.status in ('rejected', 'expired', 'revoked'):
        new_status = 'absent'
    else:  # pending — no-op
        return
```

- [ ] **Step 4: Generate and inspect the migration**

Run: `python manage.py makemigrations hr --name attendanceexception_revoke_fields_and_more`
Expected: `AddField` x3, `AlterField` for `status`, `CreateModel` for `AttendanceExceptionRevokeRequest`.

- [ ] **Step 5: Run migration and test**

Run: `python manage.py migrate hr`
Run: `python manage.py test hr.tests.AttendanceExceptionRevokeFieldsTests hr.tests.AttendanceExceptionRevokeRequestModelTests hr.tests.AttendanceOutcomeRevokedTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/models/attendance_exception.py hr/models/__init__.py hr/attendance_exception_services.py hr/migrations/0043_attendanceexception_revoke_fields_and_more.py hr/tests.py
git commit -m "hr: add AttendanceException revoked status/fields and AttendanceExceptionRevokeRequest model"
```

---

### Task 3: Leave Request Edit/Delete — service layer + `exclude_request_id` plumbing

**Files:**
- Modify: `hr/leave_services.py:4-78` (`validate_leave_submission`)
- Modify: `hr/forms.py:514-579` (`check_leave_balance`, `LeaveRequestForm`)
- Modify: `hr/leave_approval_services.py` (new functions)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `LeaveRequest` (Task 1).
- Produces: `validate_leave_submission(employee, leave_type, start_date, end_date, lock=False, exclude_request_id=None)`. `check_leave_balance(employee, leave_type, start_date, end_date, exclude_request_id=None)`. `LeaveRequestForm(..., fixed_employee=None, exclude_request_id=None)`. `edit_leave_request(leave_request, editing_user, *, leave_type, start_date, end_date, employee_reason='', document=None) -> LeaveRequest`. `delete_leave_request(leave_request, deleting_user) -> None`. Both raise `ValueError` with a human-readable message on any gate failure — same convention as every other function in this file.

- [ ] **Step 1: Write the failing tests**

Add to `hr/tests.py`:

```python
class EditDeleteLeaveRequestServiceTests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='EDLR-1')
        self.lt, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 5, 'is_accumulative': False})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=5)
        self.user = make_user('edlr-user', password='x')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.req = submit_leave_request(
            employee=self.emp, leave_type=self.lt, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2),
            created_by=self.user)

    def test_creator_can_edit_pending_undecided_request(self):
        from hr.leave_approval_services import edit_leave_request
        updated = edit_leave_request(
            self.req, self.user, leave_type=self.lt, start_date=date(2026, 8, 3), end_date=date(2026, 8, 4),
            employee_reason='Updated reason')
        self.assertEqual(updated.start_date, date(2026, 8, 3))
        self.assertEqual(updated.end_date, date(2026, 8, 4))
        self.assertEqual(updated.employee_reason, 'Updated reason')
        self.assertEqual(updated.days, 2)

    def test_non_creator_cannot_edit(self):
        from hr.leave_approval_services import edit_leave_request
        other = make_user('edlr-other', password='x')
        with self.assertRaises(ValueError):
            edit_leave_request(self.req, other, leave_type=self.lt, start_date=date(2026, 8, 3), end_date=date(2026, 8, 4))

    def test_cannot_edit_once_a_decision_is_recorded(self):
        from hr.leave_approval_services import edit_leave_request
        approver = make_user('edlr-approver', password='x')
        LeaveDashboardAccess.objects.create(user=approver, is_active=True)
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=approver, decision='approved')
        with self.assertRaises(ValueError):
            edit_leave_request(self.req, self.user, leave_type=self.lt, start_date=date(2026, 8, 3), end_date=date(2026, 8, 4))

    def test_edit_does_not_count_the_requests_own_prior_dates_against_itself(self):
        # Regression guard: without exclude_request_id, editing a request
        # would collide with its own still-pending row (self-overlap and
        # self-counted-toward-balance), making every edit fail.
        from hr.leave_approval_services import edit_leave_request
        updated = edit_leave_request(
            self.req, self.user, leave_type=self.lt, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2))
        self.assertEqual(updated.pk, self.req.pk)

    def test_creator_can_delete_pending_undecided_request(self):
        from hr.leave_approval_services import delete_leave_request
        pk = self.req.pk
        delete_leave_request(self.req, self.user)
        self.assertFalse(LeaveRequest.objects.filter(pk=pk).exists())

    def test_non_creator_cannot_delete(self):
        from hr.leave_approval_services import delete_leave_request
        other = make_user('edlr-other2', password='x')
        with self.assertRaises(ValueError):
            delete_leave_request(self.req, other)
        self.assertTrue(LeaveRequest.objects.filter(pk=self.req.pk).exists())

    def test_cannot_delete_once_a_decision_is_recorded(self):
        from hr.leave_approval_services import delete_leave_request
        approver = make_user('edlr-approver2', password='x')
        LeaveDashboardAccess.objects.create(user=approver, is_active=True)
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=approver, decision='disapproved')
        with self.assertRaises(ValueError):
            delete_leave_request(self.req, self.user)
        self.assertTrue(LeaveRequest.objects.filter(pk=self.req.pk).exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.EditDeleteLeaveRequestServiceTests -v 2`
Expected: FAIL / ERROR — `edit_leave_request`/`delete_leave_request` don't exist.

- [ ] **Step 3: Write the implementation**

In `hr/leave_services.py`, change `validate_leave_submission`'s signature and the two queries that need exclusion (currently lines 4, 44-47, 67-69):

```python
def validate_leave_submission(employee, leave_type, start_date, end_date, lock=False, exclude_request_id=None):
```

```python
    pending_qs = LeaveRequest.objects.filter(
        employee=employee, leave_type=leave_type, status='pending', start_date__year=start_date.year)
    if exclude_request_id is not None:
        pending_qs = pending_qs.exclude(pk=exclude_request_id)
    pending_days = sum((r.days or Decimal('0') for r in pending_qs), Decimal('0'))
```

```python
    overlap_qs = LeaveRequest.objects.filter(
        employee=employee, status='pending', start_date__lte=end_date, end_date__gte=start_date)
    if exclude_request_id is not None:
        overlap_qs = overlap_qs.exclude(pk=exclude_request_id)
    if overlap_qs.exists():
        raise ValueError(
            'This date range overlaps with another leave request you already have pending '
            '(regardless of leave type — you cannot be on two leaves at once).')
```

Add a one-line docstring note to `validate_leave_submission` (append to its existing docstring, don't replace it) explaining the new parameter:

```python
    `exclude_request_id`, when set, excludes that LeaveRequest's own pk from
    both the pending-balance sum and the overlap check — required when
    re-validating an in-place EDIT of an already-pending request, which
    would otherwise collide with its own unmodified row.
```

In `hr/forms.py`, update `check_leave_balance` (currently lines 514-532):

```python
def check_leave_balance(employee, leave_type, start_date, end_date, exclude_request_id=None):
    """Form-level fast-fail check ... (docstring unchanged) ..."""
    from .leave_services import validate_leave_submission
    try:
        validate_leave_submission(employee, leave_type, start_date, end_date, lock=False,
                                  exclude_request_id=exclude_request_id)
    except ValueError as exc:
        raise forms.ValidationError(str(exc))
```

Update `LeaveRequestForm.__init__` and `clean` (currently lines 548-579):

```python
    def __init__(self, *args, fixed_employee=None, exclude_request_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_employee = fixed_employee
        self.exclude_request_id = exclude_request_id
        if fixed_employee is not None:
            del self.fields['employee']
```

```python
            else:
                check_leave_balance(employee, leave_type, start_date, end_date,
                                    exclude_request_id=self.exclude_request_id)
        return cleaned
```

In `hr/leave_approval_services.py`, add two new functions at the end of the file (after `grant_exception_days`):

```python
def edit_leave_request(leave_request, editing_user, *, leave_type, start_date, end_date,
                       employee_reason='', document=None):
    """The creator edits their own still-pending, undecided request in
    place. Re-runs the exact same balance/overlap validation a fresh
    submission would (via exclude_request_id, so the request doesn't
    collide with its own unmodified row) — an edit is not exempt from the
    rules that applied to the original submission, and could newly become
    exceeds_balance if the new dates push it over the balance-hold
    threshold, exactly like any other submission.

    Does NOT reset the approver roster — the same LeaveDashboardAccess
    snapshot taken at original submission stays; this is safe because the
    lock condition below guarantees nobody has decided yet, so there's
    nothing to invalidate."""
    from hr.leave_services import validate_leave_submission

    if leave_request.created_by_id != editing_user.id:
        raise ValueError('Only the person who submitted this request can edit it.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be edited.')
    if leave_request.approvals.exclude(decision='pending').exists():
        raise ValueError('An approver has already recorded a decision on this request; it can no longer be edited.')

    with transaction.atomic():
        exceeds_balance = validate_leave_submission(
            leave_request.employee, leave_type, start_date, end_date, lock=True,
            exclude_request_id=leave_request.pk)
        leave_request.leave_type = leave_type
        leave_request.start_date = start_date
        leave_request.end_date = end_date
        leave_request.employee_reason = employee_reason
        if document is not None:
            leave_request.document = document
        leave_request.exceeds_balance = exceeds_balance
        leave_request.days = leave_request.computed_days()
        leave_request.save(update_fields=[
            'leave_type', 'start_date', 'end_date', 'employee_reason', 'document',
            'exceeds_balance', 'days', 'updated_at'])
    return leave_request


def delete_leave_request(leave_request, deleting_user):
    """The creator withdraws their own still-pending, undecided request.
    No LeaveRecord exists yet at this stage (only created on approval —
    see _finalize), so there's nothing else to clean up; approvals cascade-
    delete with the row."""
    if leave_request.created_by_id != deleting_user.id:
        raise ValueError('Only the person who submitted this request can delete it.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be deleted.')
    if leave_request.approvals.exclude(decision='pending').exists():
        raise ValueError('An approver has already recorded a decision on this request; it can no longer be deleted.')
    leave_request.delete()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.EditDeleteLeaveRequestServiceTests -v 2`
Expected: PASS

- [ ] **Step 5: Run the pre-existing balance/form tests to confirm the new parameter didn't change default behavior**

Run: `python manage.py test hr.tests.ValidateLeaveSubmissionHoldTests hr.tests.LogRequestBalanceWarningTests hr.tests.MyProfileLeaveRequestTests -v 2`
Expected: PASS (unchanged — `exclude_request_id` defaults to `None`, identical to today's behavior).

- [ ] **Step 6: Commit**

```bash
git add hr/leave_services.py hr/forms.py hr/leave_approval_services.py hr/tests.py
git commit -m "hr: add edit/delete service functions for pending leave requests"
```

---

### Task 4: Team Exception Edit/Delete — service layer

**Files:**
- Modify: `hr/attendance_exception_services.py` (new functions)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `AttendanceException` (Task 2).
- Produces: `edit_attendance_exception(exc, editing_user, *, event_date, event_start_time, reason_category, custom_reason='', employee_comment='') -> AttendanceException`. `delete_attendance_exception(exc, deleting_user) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `hr/tests.py`:

```python
class EditDeleteAttendanceExceptionServiceTests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='EDAE-1')
        self.user = _login_user('edae-user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.exc = _submit_aex(
            employee=self.emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
            reason_category='site_visit', created_by=self.user)

    def test_creator_can_edit_pending_exception(self):
        from hr.attendance_exception_services import edit_attendance_exception
        updated = edit_attendance_exception(
            self.exc, self.user, event_date=_date(2026, 7, 21), event_start_time=_time(10, 0),
            reason_category='outside_meeting')
        self.assertEqual(updated.event_date, _date(2026, 7, 21))
        self.assertEqual(updated.reason_category, 'outside_meeting')

    def test_non_creator_cannot_edit(self):
        from hr.attendance_exception_services import edit_attendance_exception
        other = make_user('edae-other', password='x')
        with self.assertRaises(ValueError):
            edit_attendance_exception(
                self.exc, other, event_date=_date(2026, 7, 21), event_start_time=_time(10, 0),
                reason_category='outside_meeting')

    def test_cannot_edit_once_decided(self):
        from hr.attendance_exception_services import edit_attendance_exception
        decide_attendance_exception(self.exc, make_user('edae-mgr', password='x'), 'approved')
        # decide_attendance_exception requires the assigned manager — bypass
        # by setting main_manager first isn't needed here since we only care
        # that a decided exception can't be edited; force status directly to
        # keep the test focused on the edit gate, not the decide gate.
        self.exc.status = 'approved'
        self.exc.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            edit_attendance_exception(
                self.exc, self.user, event_date=_date(2026, 7, 21), event_start_time=_time(10, 0),
                reason_category='outside_meeting')

    def test_creator_can_delete_pending_exception(self):
        from hr.attendance_exception_services import delete_attendance_exception
        pk = self.exc.pk
        delete_attendance_exception(self.exc, self.user)
        self.assertFalse(AttendanceException.objects.filter(pk=pk).exists())

    def test_non_creator_cannot_delete(self):
        from hr.attendance_exception_services import delete_attendance_exception
        other = make_user('edae-other2', password='x')
        with self.assertRaises(ValueError):
            delete_attendance_exception(self.exc, other)
        self.assertTrue(AttendanceException.objects.filter(pk=self.exc.pk).exists())

    def test_cannot_delete_once_decided(self):
        from hr.attendance_exception_services import delete_attendance_exception
        self.exc.status = 'expired'
        self.exc.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            delete_attendance_exception(self.exc, self.user)
```

Note: `test_cannot_edit_once_decided` calls `decide_attendance_exception` first only to exercise a realistic path, then force-sets `status` directly afterward — `decide_attendance_exception` requires `self.exc.employee.main_manager` to be the deciding user, which isn't set up in this test's minimal `setUp`; forcing the field directly keeps the test isolated to the edit/delete gate under test rather than also depending on the decide workflow's own preconditions. Simplify by removing the `decide_attendance_exception(...)` call entirely if it raises — the two lines that matter are setting `status` and asserting the edit is blocked.

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.EditDeleteAttendanceExceptionServiceTests -v 2`
Expected: FAIL / ERROR

- [ ] **Step 3: Write the implementation**

In `hr/attendance_exception_services.py`, add two functions after `send_pending_start_reminders` (end of file):

```python
def edit_attendance_exception(exc, editing_user, *, event_date, event_start_time, reason_category,
                              custom_reason='', employee_comment=''):
    """The creator edits their own still-pending, undecided exception in
    place. Assumes the caller (the view, via AttendanceExceptionForm) has
    already validated the fields — same trust level as
    submit_attendance_exception, which also doesn't re-validate
    reason_category/custom_reason itself."""
    if exc.created_by_id != editing_user.id:
        raise ValueError('Only the person who submitted this request can edit it.')
    exc.refresh_from_db()
    if exc.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be edited.')
    if AttendanceException.objects.filter(
            employee=exc.employee, event_date=event_date, event_start_time=event_start_time,
            status__in=('pending', 'expired')).exclude(pk=exc.pk).exists():
        raise ValueError(
            'An attendance exception for this exact event has already been submitted and is still awaiting a decision.')

    exc.event_date = event_date
    exc.event_start_time = event_start_time
    exc.reason_category = reason_category
    exc.custom_reason = custom_reason
    exc.employee_comment = employee_comment
    exc.save(update_fields=[
        'event_date', 'event_start_time', 'reason_category', 'custom_reason', 'employee_comment', 'updated_at'])
    return exc


def delete_attendance_exception(exc, deleting_user):
    """The creator withdraws their own still-pending, undecided exception."""
    if exc.created_by_id != deleting_user.id:
        raise ValueError('Only the person who submitted this request can delete it.')
    exc.refresh_from_db()
    if exc.status != 'pending':
        raise ValueError('This request has already been decided and can no longer be deleted.')
    exc.delete()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.EditDeleteAttendanceExceptionServiceTests -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hr/attendance_exception_services.py hr/tests.py
git commit -m "hr: add edit/delete service functions for pending attendance exceptions"
```

---

### Task 5: Leave Request Revoke — service layer (direct + requested)

**Files:**
- Modify: `hr/leave_approval_services.py` (new functions)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `LeaveRequest.revoked_by/revoked_at/revoke_reason`, `LeaveRevokeRequest` (Task 1). `has_override_access` (from `hr/views.py`, imported locally to avoid a views→services circular import, matching this file's existing local-import style, e.g. `_finalize`'s `from hr.models import LeaveRequest`).
- Produces: `revoke_leave_request(leave_request, revoking_user, reason) -> LeaveRequest` (direct revoke — caller checks `has_override_access` first, same separation-of-concerns as `override_finalize`). `request_leave_revoke(leave_request, requesting_user, reason) -> LeaveRevokeRequest`. `decide_leave_revoke_request(revoke_request, deciding_user, decision, decision_note='') -> LeaveRevokeRequest`.

- [ ] **Step 1: Write the failing tests**

Add to `hr/tests.py`:

```python
class RevokeLeaveRequestServiceTests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='RLR-1')
        self.lt, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': Decimal('30')})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        self.user = make_user('rlr-user', password='x')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.req = submit_leave_request(
            employee=self.emp, leave_type=self.lt, start_date=date(2026, 8, 1), end_date=date(2026, 8, 3),
            created_by=self.user)
        approver = make_user('rlr-approver', password='x')
        LeaveDashboardAccess.objects.create(user=approver, is_active=True)
        record_approver_decision(self.req, approver, 'approved')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')
        self.assertIsNotNone(self.req.leave_record_id)
        self.revoker = make_user('rlr-revoker', password='x')

    def test_direct_revoke_deletes_leave_record_and_sets_fields(self):
        from hr.leave_approval_services import revoke_leave_request
        record_id = self.req.leave_record_id
        updated = revoke_leave_request(self.req, self.revoker, 'Project emergency.')
        self.assertEqual(updated.status, 'revoked')
        self.assertEqual(updated.revoked_by, self.revoker)
        self.assertIsNotNone(updated.revoked_at)
        self.assertEqual(updated.revoke_reason, 'Project emergency.')
        self.assertFalse(LeaveRecord.objects.filter(pk=record_id).exists())

    def test_direct_revoke_requires_reason(self):
        from hr.leave_approval_services import revoke_leave_request
        with self.assertRaises(ValueError):
            revoke_leave_request(self.req, self.revoker, '')

    def test_cannot_revoke_a_non_approved_request(self):
        from hr.leave_approval_services import revoke_leave_request
        pending = submit_leave_request(
            employee=self.emp, leave_type=self.lt, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
            created_by=self.user)
        with self.assertRaises(ValueError):
            revoke_leave_request(pending, self.revoker, 'reason')

    def test_cannot_self_revoke(self):
        from hr.leave_approval_services import revoke_leave_request
        with self.assertRaises(ValueError):
            revoke_leave_request(self.req, self.user, 'reason')

    def test_employee_can_request_revoke_on_own_approved_request(self):
        from hr.leave_approval_services import request_leave_revoke
        revoke_req = request_leave_revoke(self.req, self.user, 'Plans changed.')
        self.assertEqual(revoke_req.status, 'pending')
        self.assertEqual(revoke_req.requested_by, self.user)

    def test_non_employee_cannot_request_revoke(self):
        from hr.leave_approval_services import request_leave_revoke
        with self.assertRaises(ValueError):
            request_leave_revoke(self.req, self.revoker, 'reason')

    def test_duplicate_pending_revoke_request_blocked(self):
        from hr.leave_approval_services import request_leave_revoke
        request_leave_revoke(self.req, self.user, 'First request.')
        with self.assertRaises(ValueError):
            request_leave_revoke(self.req, self.user, 'Second request.')

    def test_decide_revoke_request_approve_applies_the_revoke(self):
        from hr.leave_approval_services import request_leave_revoke, decide_leave_revoke_request
        revoke_req = request_leave_revoke(self.req, self.user, 'Plans changed.')
        decide_leave_revoke_request(revoke_req, self.revoker, 'approved')
        self.req.refresh_from_db()
        revoke_req.refresh_from_db()
        self.assertEqual(self.req.status, 'revoked')
        self.assertEqual(revoke_req.status, 'approved')
        self.assertEqual(revoke_req.decided_by, self.revoker)

    def test_decide_revoke_request_reject_leaves_original_untouched(self):
        from hr.leave_approval_services import request_leave_revoke, decide_leave_revoke_request
        revoke_req = request_leave_revoke(self.req, self.user, 'Plans changed.')
        decide_leave_revoke_request(revoke_req, self.revoker, 'rejected', decision_note='Coverage already arranged.')
        self.req.refresh_from_db()
        revoke_req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')  # unchanged
        self.assertEqual(revoke_req.status, 'rejected')
        self.assertEqual(revoke_req.decision_note, 'Coverage already arranged.')

    def test_reject_without_decision_note_is_rejected(self):
        from hr.leave_approval_services import request_leave_revoke, decide_leave_revoke_request
        revoke_req = request_leave_revoke(self.req, self.user, 'Plans changed.')
        with self.assertRaises(ValueError):
            decide_leave_revoke_request(revoke_req, self.revoker, 'rejected', decision_note='')

    def test_self_decision_on_own_revoke_request_blocked(self):
        from hr.leave_approval_services import request_leave_revoke, decide_leave_revoke_request
        revoke_req = request_leave_revoke(self.req, self.user, 'Plans changed.')
        with self.assertRaises(ValueError):
            decide_leave_revoke_request(revoke_req, self.user, 'approved')
```

Add `record_approver_decision` and `LeaveRecord` to the test file's imports if not already present (both are already used/imported elsewhere in `hr/tests.py` — confirm rather than duplicate an import line).

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.RevokeLeaveRequestServiceTests -v 2`
Expected: FAIL / ERROR

- [ ] **Step 3: Write the implementation**

In `hr/leave_approval_services.py`, add at the end of the file:

```python
def revoke_leave_request(leave_request, revoking_user, reason):
    """Direct revoke by someone with override access — does NOT check
    has_override_access itself (that's the caller's/view's job, same
    separation of concerns as override_finalize). Deletes the linked
    LeaveRecord so taken_days/remaining_days recompute live; the
    LeaveRequest row stays, re-labeled status='revoked'."""
    if leave_request.employee.user_id and leave_request.employee.user_id == revoking_user.id:
        raise ValueError('You cannot revoke your own leave request.')
    if not reason or not reason.strip():
        raise ValueError('A revoke requires a written reason.')
    leave_request.refresh_from_db()
    if leave_request.status != 'approved':
        raise ValueError(f'This request is {leave_request.status}; only an approved request can be revoked.')

    with transaction.atomic():
        current = LeaveRequest.objects.select_for_update().get(pk=leave_request.pk)
        if current.status != 'approved':
            raise ValueError(f'This request is {current.status}; only an approved request can be revoked.')
        if current.leave_record_id:
            LeaveRecord.objects.filter(pk=current.leave_record_id).delete()
        current.status = 'revoked'
        current.revoked_by = revoking_user
        current.revoked_at = timezone.now()
        current.revoke_reason = reason.strip()
        current.leave_record = None
        current.save(update_fields=['status', 'revoked_by', 'revoked_at', 'revoke_reason', 'leave_record'])
        # Auto-close any pending employee-initiated revoke request for the
        # same leave rather than leaving it dangling — a direct revoke by
        # someone with override access always wins immediately.
        LeaveRevokeRequest.objects.filter(leave_request=current, status='pending').update(
            status='approved', decided_by=revoking_user, decided_at=timezone.now(),
            decision_note='Applied via a direct revoke before this request was reviewed.')
    if leave_request.employee.user_id:
        notify_users(
            recipients=[leave_request.employee.user],
            verb=f'Your approved {leave_request.leave_type.name} leave was revoked',
            actor=revoking_user, description=reason.strip())
    leave_request.refresh_from_db()
    return leave_request


def request_leave_revoke(leave_request, requesting_user, reason):
    """The employee themselves requests to void their own approved leave.
    HR/Super Admin use revoke_leave_request (direct) instead of this queue —
    requested_by is always the employee, never a manager acting on their
    behalf, even for a request the manager originally logged."""
    if not (leave_request.employee.user_id and leave_request.employee.user_id == requesting_user.id):
        raise ValueError('Only the employee themselves can request a revoke of their own leave.')
    if not reason or not reason.strip():
        raise ValueError('A reason is required to request a revoke.')
    leave_request.refresh_from_db()
    if leave_request.status != 'approved':
        raise ValueError(f'This request is {leave_request.status}; only an approved request can have its revoke requested.')
    if LeaveRevokeRequest.objects.filter(leave_request=leave_request, status='pending').exists():
        raise ValueError('A revoke request for this leave is already pending review.')

    revoke_request = LeaveRevokeRequest.objects.create(
        leave_request=leave_request, requested_by=requesting_user, reason=reason.strip())

    from django.contrib.auth import get_user_model
    from accounts.models import Role
    from hr.models import LeaveDashboardAccess
    User = get_user_model()
    recipients = set(g.user for g in LeaveDashboardAccess.objects.filter(is_active=True))
    recipients |= set(User.objects.filter(role__name=Role.SUPER_ADMIN))
    if recipients:
        notify_users(
            recipients=list(recipients),
            verb=f'{leave_request.employee.full_name} requested to revoke an approved leave',
            actor=requesting_user, description=reason.strip())
    return revoke_request


def decide_leave_revoke_request(revoke_request, deciding_user, decision, decision_note=''):
    """HR/Super Admin (the same roster that decides normal leave requests)
    approves or rejects an employee's revoke request. Approving applies the
    exact same mechanic as a direct revoke (revoke_leave_request) —
    reusing it keeps the LeaveRecord-deletion/notification logic in one
    place."""
    if decision not in ('approved', 'rejected'):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    revoke_request.refresh_from_db()
    if revoke_request.status != 'pending':
        raise ValueError(f'This revoke request is already {revoke_request.status}.')
    leave_request = revoke_request.leave_request
    if leave_request.employee.user_id and leave_request.employee.user_id == deciding_user.id:
        raise ValueError('You cannot decide a revoke request on your own leave.')
    if decision == 'rejected' and not (decision_note or '').strip():
        raise ValueError('Rejecting a revoke request requires a note explaining why.')

    with transaction.atomic():
        revoke_request.status = decision
        revoke_request.decided_by = deciding_user
        revoke_request.decided_at = timezone.now()
        revoke_request.decision_note = decision_note.strip()
        revoke_request.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        if decision == 'approved':
            revoke_leave_request(leave_request, deciding_user, revoke_request.reason)
        else:
            if leave_request.employee.user_id:
                notify_users(
                    recipients=[leave_request.employee.user],
                    verb=f'Your revoke request for a {leave_request.leave_type.name} leave was rejected',
                    actor=deciding_user, description=decision_note.strip())
    return revoke_request
```

Add the two new imports needed at the top of `hr/leave_approval_services.py` (currently only imports `LeaveRecord`):

```python
from hr.models import LeaveRecord, LeaveRequest, LeaveRevokeRequest
```

(`LeaveRequest` is already imported locally inside `_finalize` via `from hr.models import LeaveRequest` — leave that local import as-is to avoid touching unrelated code; the new top-level import is only strictly needed for `revoke_leave_request`'s `LeaveRequest.objects.select_for_update()` call. Using the top-level import there is consistent with how `LeaveRecord` is already imported at the top of this file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.RevokeLeaveRequestServiceTests -v 2`
Expected: PASS

- [ ] **Step 5: Run the pre-existing leave-approval suite to confirm nothing else broke**

Run: `python manage.py test hr.tests.LeaveApprovalServiceTests hr.tests.SelfApprovalPreventionTests hr.tests.LeaveCancellationRefundSafetyTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/leave_approval_services.py hr/tests.py
git commit -m "hr: add direct and requested revoke service functions for leave requests"
```

---

### Task 6: Team Exception Revoke — service layer (direct + requested)

**Files:**
- Modify: `hr/attendance_exception_services.py` (new functions)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `AttendanceException.revoked_by/revoked_at/revoke_reason`, `AttendanceExceptionRevokeRequest`, `_apply_attendance_outcome` (Task 2).
- Produces: `revoke_attendance_exception(exc, revoking_user, reason) -> AttendanceException`. `request_attendance_exception_revoke(exc, requesting_user, reason) -> AttendanceExceptionRevokeRequest`. `decide_attendance_exception_revoke_request(revoke_request, deciding_user, decision, decision_note='') -> AttendanceExceptionRevokeRequest`. Per the spec's flagged extrapolation, the decider for a requested revoke is the assigned manager OR an override-access holder (mirrors `can_decide_attendance_exception`, imported locally from `hr.views` the same way this file has no existing cross-import to that module yet — add the import inside the function to avoid a module-load-time circular import, matching `edit_approver_decision`'s local `from hr.models import LeaveRequestNote` style).

- [ ] **Step 1: Write the failing tests**

Add to `hr/tests.py`:

```python
class RevokeAttendanceExceptionServiceTests(TestCase):
    def setUp(self):
        self.manager = make_employee(iqama='RAE-MGR', name='Revoke Exc Manager')
        self.manager_user = _login_user('rae_mgr')
        self.manager.user = self.manager_user
        self.manager.save(update_fields=['user'])
        self.emp = make_employee(iqama='RAE-1')
        self.emp.main_manager = self.manager
        self.emp.save(update_fields=['main_manager'])
        self.user = _login_user('rae_user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.exc = _submit_aex(
            employee=self.emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
            reason_category='site_visit', created_by=self.user)
        decide_attendance_exception(self.exc, self.manager_user, 'approved')
        self.exc.refresh_from_db()
        self.assertEqual(self.exc.status, 'approved')
        self.revoker = _login_user('rae_revoker')

    def test_direct_revoke_sets_fields_and_marks_day_absent(self):
        from hr.attendance_exception_services import revoke_attendance_exception
        from hr.models import AttendanceRecord
        updated = revoke_attendance_exception(self.exc, self.revoker, 'No longer applicable.')
        self.assertEqual(updated.status, 'revoked')
        self.assertEqual(updated.revoked_by, self.revoker)
        self.assertEqual(updated.revoke_reason, 'No longer applicable.')
        record = AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 20))
        self.assertEqual(record.status, 'absent')

    def test_direct_revoke_requires_reason(self):
        from hr.attendance_exception_services import revoke_attendance_exception
        with self.assertRaises(ValueError):
            revoke_attendance_exception(self.exc, self.revoker, '')

    def test_cannot_revoke_non_approved_exception(self):
        from hr.attendance_exception_services import revoke_attendance_exception
        pending = _submit_aex(
            employee=self.emp, event_date=_date(2026, 7, 25), event_start_time=_time(9, 0),
            reason_category='site_visit', created_by=self.user)
        with self.assertRaises(ValueError):
            revoke_attendance_exception(pending, self.revoker, 'reason')

    def test_employee_can_request_revoke(self):
        from hr.attendance_exception_services import request_attendance_exception_revoke
        revoke_req = request_attendance_exception_revoke(self.exc, self.user, 'Plans changed.')
        self.assertEqual(revoke_req.status, 'pending')

    def test_duplicate_pending_revoke_request_blocked(self):
        from hr.attendance_exception_services import request_attendance_exception_revoke
        request_attendance_exception_revoke(self.exc, self.user, 'First.')
        with self.assertRaises(ValueError):
            request_attendance_exception_revoke(self.exc, self.user, 'Second.')

    def test_assigned_manager_can_decide_revoke_request(self):
        from hr.attendance_exception_services import request_attendance_exception_revoke, decide_attendance_exception_revoke_request
        revoke_req = request_attendance_exception_revoke(self.exc, self.user, 'Plans changed.')
        decide_attendance_exception_revoke_request(revoke_req, self.manager_user, 'approved')
        self.exc.refresh_from_db()
        self.assertEqual(self.exc.status, 'revoked')

    def test_unrelated_user_cannot_decide_revoke_request(self):
        from hr.attendance_exception_services import request_attendance_exception_revoke, decide_attendance_exception_revoke_request
        revoke_req = request_attendance_exception_revoke(self.exc, self.user, 'Plans changed.')
        stranger = _login_user('rae_stranger')
        with self.assertRaises(ValueError):
            decide_attendance_exception_revoke_request(revoke_req, stranger, 'approved')

    def test_reject_requires_decision_note(self):
        from hr.attendance_exception_services import request_attendance_exception_revoke, decide_attendance_exception_revoke_request
        revoke_req = request_attendance_exception_revoke(self.exc, self.user, 'Plans changed.')
        with self.assertRaises(ValueError):
            decide_attendance_exception_revoke_request(revoke_req, self.manager_user, 'rejected', decision_note='')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.RevokeAttendanceExceptionServiceTests -v 2`
Expected: FAIL / ERROR

- [ ] **Step 3: Write the implementation**

In `hr/attendance_exception_services.py`, add at the end of the file:

```python
def revoke_attendance_exception(exc, revoking_user, reason):
    """Direct revoke by someone with override access or upstream hierarchy
    authority — does NOT check who is allowed to call it (caller's job, same
    separation of concerns as override_attendance_exception). No linked
    record to delete; the read path (_apply_attendance_outcome) already
    treats 'revoked' as not-excused."""
    if exc.employee.user_id and exc.employee.user_id == revoking_user.id:
        raise ValueError('You cannot revoke your own attendance exception.')
    if not reason or not reason.strip():
        raise ValueError('A revoke requires a written reason.')
    exc.refresh_from_db()
    if exc.status != 'approved':
        raise ValueError(f'This request is {exc.status}; only an approved request can be revoked.')

    with transaction.atomic():
        locked = AttendanceException.objects.select_for_update().get(pk=exc.pk)
        if locked.status != 'approved':
            raise ValueError(f'This request is {locked.status}; only an approved request can be revoked.')
        locked.status = 'revoked'
        locked.revoked_by = revoking_user
        locked.revoked_at = timezone.now()
        locked.revoke_reason = reason.strip()
        locked.save()
        _apply_attendance_outcome(locked)
        AttendanceExceptionRevokeRequest.objects.filter(attendance_exception=locked, status='pending').update(
            status='approved', decided_by=revoking_user, decided_at=timezone.now(),
            decision_note='Applied via a direct revoke before this request was reviewed.')

    for field in ('status', 'revoked_by', 'revoked_by_id', 'revoked_at', 'revoke_reason'):
        if hasattr(locked, field):
            setattr(exc, field, getattr(locked, field))
    if exc.employee.user_id:
        notify_users(
            recipients=[exc.employee.user],
            verb=f'Your approved attendance exception for {exc.event_date} was revoked',
            actor=revoking_user, description=reason.strip())
    return exc


def request_attendance_exception_revoke(exc, requesting_user, reason):
    """The employee themselves requests to void their own approved
    exception."""
    if not (exc.employee.user_id and exc.employee.user_id == requesting_user.id):
        raise ValueError('Only the employee themselves can request a revoke of their own attendance exception.')
    if not reason or not reason.strip():
        raise ValueError('A reason is required to request a revoke.')
    exc.refresh_from_db()
    if exc.status != 'approved':
        raise ValueError(f'This request is {exc.status}; only an approved request can have its revoke requested.')
    if AttendanceExceptionRevokeRequest.objects.filter(attendance_exception=exc, status='pending').exists():
        raise ValueError('A revoke request for this exception is already pending review.')

    revoke_request = AttendanceExceptionRevokeRequest.objects.create(
        attendance_exception=exc, requested_by=requesting_user, reason=reason.strip())
    if exc.main_manager and exc.main_manager.user_id:
        notify_users(
            recipients=[exc.main_manager.user],
            verb=f'{exc.employee.full_name} requested to revoke an approved attendance exception',
            actor=requesting_user, description=reason.strip())
    return revoke_request


def decide_attendance_exception_revoke_request(revoke_request, deciding_user, decision, decision_note=''):
    """The assigned manager, or an override-access/upstream-hierarchy
    holder, approves or rejects an employee's revoke request — mirrors
    can_decide_attendance_exception's eligibility (imported locally to
    avoid a module-load-time circular import between this services module
    and hr.views, matching this file's existing local-import style)."""
    from hr.views import can_decide_attendance_exception

    if decision not in ('approved', 'rejected'):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    revoke_request.refresh_from_db()
    if revoke_request.status != 'pending':
        raise ValueError(f'This revoke request is already {revoke_request.status}.')
    exc = revoke_request.attendance_exception
    if exc.employee.user_id and exc.employee.user_id == deciding_user.id:
        raise ValueError('You cannot decide a revoke request on your own attendance exception.')
    if not can_decide_attendance_exception(deciding_user, exc):
        raise ValueError('You are not authorized to decide this revoke request.')
    if decision == 'rejected' and not (decision_note or '').strip():
        raise ValueError('Rejecting a revoke request requires a note explaining why.')

    with transaction.atomic():
        revoke_request.status = decision
        revoke_request.decided_by = deciding_user
        revoke_request.decided_at = timezone.now()
        revoke_request.decision_note = decision_note.strip()
        revoke_request.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        if decision == 'approved':
            revoke_attendance_exception(exc, deciding_user, revoke_request.reason)
        else:
            if exc.employee.user_id:
                notify_users(
                    recipients=[exc.employee.user],
                    verb='Your revoke request for an attendance exception was rejected',
                    actor=deciding_user, description=decision_note.strip())
    return revoke_request
```

Add `AttendanceExceptionRevokeRequest` to this file's model import (currently `from hr.models import AttendanceException, AttendanceRecord`):

```python
from hr.models import AttendanceException, AttendanceExceptionRevokeRequest, AttendanceRecord
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.RevokeAttendanceExceptionServiceTests -v 2`
Expected: PASS

- [ ] **Step 5: Run the pre-existing attendance-exception suite**

Run: `python manage.py test hr.tests.AttendanceExceptionServiceTests hr.tests.SelfApprovalGuardrailTests hr.tests.TeamExceptionsDecideOverrideTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/attendance_exception_services.py hr/tests.py
git commit -m "hr: add direct and requested revoke service functions for attendance exceptions"
```

---

### Task 7: My Profile — Edit/Delete UI for pending Leave Requests

**Files:**
- Modify: `hr/views.py:297-334` (`my_profile`, leave-request POST branch)
- Modify: `templates/hr/my_profile.html:178-230` (My Leave Requests table), `templates/hr/my_profile.html:430-490` (My Reporting Structure in-behalf list, exact lines depend on Task 8/9 of the prior session's edits — locate by searching for `in_behalf_requests`)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `edit_leave_request`, `delete_leave_request` (Task 3).
- Produces: `my_profile` handles `action in ('edit_leave_request', 'delete_leave_request')`. Each `LeaveRequest` object attached to `context['leave_requests']` and to `r.in_behalf_requests` (per report, in the reporting-structure block) gets a computed `req.can_edit_delete` boolean the template reads — computed once in the view rather than re-derived per-row in the template, matching this view's existing pattern for `r.in_behalf_requests`.

- [ ] **Step 1: Write the failing tests**

Add to `hr/tests.py`:

```python
class MyProfileLeaveEditDeleteUITests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='MPLED-1')
        self.lt, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 5, 'is_accumulative': False})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=5)
        self.user = _login_user('mpled_user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.req = submit_leave_request(
            employee=self.emp, leave_type=self.lt, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2),
            created_by=self.user)
        self.client.login(username='mpled_user', password='testpass123')

    def test_edit_delete_buttons_render_for_own_pending_request(self):
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, f'edit_leave_request_{self.req.pk}')
        self.assertContains(resp, f'delete_leave_request_{self.req.pk}')

    def test_no_edit_delete_buttons_once_decided(self):
        approver = make_user('mpled_approver', password='x')
        LeaveDashboardAccess.objects.create(user=approver, is_active=True)
        record_approver_decision(self.req, approver, 'approved')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertNotContains(resp, f'edit_leave_request_{self.req.pk}')

    def test_post_edit_updates_request(self):
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'edit_leave_request', 'request_id': self.req.pk,
            'leave_type': self.lt.pk, 'start_date': '2026-08-05', 'end_date': '2026-08-06',
            'employee_reason': 'Rescheduled',
        })
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.start_date, date(2026, 8, 5))
        self.assertEqual(self.req.employee_reason, 'Rescheduled')

    def test_post_delete_removes_request(self):
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'delete_leave_request', 'request_id': self.req.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(LeaveRequest.objects.filter(pk=self.req.pk).exists())

    def test_cannot_delete_someone_elses_request(self):
        other_emp = make_employee(iqama='MPLED-2')
        other_user = _login_user('mpled_other')
        other_emp.user = other_user
        other_emp.save(update_fields=['user'])
        other_req = submit_leave_request(
            employee=other_emp, leave_type=self.lt, start_date=date(2026, 8, 10), end_date=date(2026, 8, 11),
            created_by=other_user)
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'delete_leave_request', 'request_id': other_req.pk,
        }, follow=True)
        self.assertTrue(LeaveRequest.objects.filter(pk=other_req.pk).exists())


class MyProfileManagerInBehalfEditDeleteUITests(TestCase):
    """Edit/Delete for a manager's own in-behalf-logged pending request
    render in the Reporting Structure card, not the report's own table —
    the gate is request.user == created_by, and the manager (not the
    report) is the creator here."""

    def setUp(self):
        self.manager_user = User.objects.create_user(username='mpmied-mgr', password='x')
        self.manager = Employee.objects.create(
            iqama_number='MPMIED-MGR', full_name='InBehalf Edit Manager', user=self.manager_user)
        self.report_user = User.objects.create_user(username='mpmied-rpt', password='x')
        self.report = Employee.objects.create(
            iqama_number='MPMIED-RPT', full_name='InBehalf Edit Report',
            main_manager=self.manager, user=self.report_user)
        self.lt, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': Decimal('30')})
        LeaveEntitlement.objects.create(employee=self.report, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        self.client.login(username='mpmied-mgr', password='x')
        self.client.post(reverse('hr:leave_request_create'), data={
            'employee': self.report.pk, 'leave_type': self.lt.pk,
            'start_date': '2026-03-01', 'end_date': '2026-03-05',
        })
        self.req = LeaveRequest.objects.get(employee=self.report)

    def test_manager_sees_edit_delete_for_own_in_behalf_request(self):
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, f'delete_leave_request_{self.req.pk}')

    def test_report_does_not_see_edit_delete_on_their_own_profile(self):
        # The report views their OWN My Leave Requests table — they didn't
        # create this request (their manager did), so no Edit/Delete for
        # them, even though it's about their own leave.
        self.client.logout()
        self.client.login(username='mpmied-rpt', password='x')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertNotContains(resp, f'delete_leave_request_{self.req.pk}')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.MyProfileLeaveEditDeleteUITests hr.tests.MyProfileManagerInBehalfEditDeleteUITests -v 2`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, inside `my_profile()`, add two new POST branches right after the existing `is_leave_request_post` block (after the `else:` that sets `form = LeaveRequestForm(...)`, currently ending at line 334, before the attendance-exception block at line 336):

```python
    is_edit_leave_post = (
        emp and request.method == 'POST' and request.POST.get('action') == 'edit_leave_request')
    if is_edit_leave_post:
        from hr.leave_approval_services import edit_leave_request
        from hr.models import LeaveType
        target = LeaveRequest.objects.filter(pk=request.POST.get('request_id')).first()
        if target is None or target.created_by_id != request.user.id:
            return HttpResponse('You did not submit this request.', status=403)
        edit_form = LeaveRequestForm(
            request.POST, request.FILES, fixed_employee=target.employee, exclude_request_id=target.pk)
        if edit_form.is_valid():
            try:
                edit_leave_request(
                    target, request.user, leave_type=edit_form.cleaned_data['leave_type'],
                    start_date=edit_form.cleaned_data['start_date'], end_date=edit_form.cleaned_data['end_date'],
                    employee_reason=edit_form.cleaned_data['employee_reason'],
                    document=edit_form.cleaned_data['document'] or None)
                messages.success(request, 'Leave request updated.')
            except ValueError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, 'Could not update the request — please check the form.')
        return redirect('hr:my_profile')

    is_delete_leave_post = (
        emp and request.method == 'POST' and request.POST.get('action') == 'delete_leave_request')
    if is_delete_leave_post:
        from hr.leave_approval_services import delete_leave_request
        target = LeaveRequest.objects.filter(pk=request.POST.get('request_id')).first()
        if target is None or target.created_by_id != request.user.id:
            return HttpResponse('You did not submit this request.', status=403)
        try:
            delete_leave_request(target, request.user)
            messages.success(request, 'Leave request deleted.')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('hr:my_profile')
```

`HttpResponse` is already imported at the top of `hr/views.py` (used elsewhere, e.g. `LeaveRequestDetailView.post`'s `403` branches) — no new import needed.

Now attach `can_edit_delete` to each request the templates will render. Inside `my_profile()`'s `if emp:` block, change the `leave_requests` context line (currently line 387-390):

```python
        context['leave_requests'] = list(
            emp.leave_requests.select_related('leave_type', 'overridden_by')
            .prefetch_related('approvals__approver')
            .order_by('-created_at')[:10])
        for r in context['leave_requests']:
            r.can_edit_delete = bool(
                r.created_by_id == request.user.id and r.status == 'pending'
                and not r.approvals.exclude(decision='pending').exists())
```

(Changed from a queryset slice to `list(...)` so the per-row attribute assignment sticks — a bare queryset slice re-evaluates on each iteration otherwise, silently dropping the attribute; this matches how `direct_reports` was already materialized to a `list(...)` for the same reason.)

Find the block that builds `direct_reports`/`in_behalf_by_employee` (search `in_behalf_requests` in `hr/views.py`) and add the same `can_edit_delete` computation to each request appended there:

```python
        for req in (LeaveRequest.objects.filter(
                        employee_id__in=[r.pk for r in direct_reports],
                        created_by=request.user, logged_by_manager=True)
                    .select_related('leave_type').order_by('employee_id', '-created_at')):
            req.can_edit_delete = bool(
                req.status == 'pending' and not req.approvals.exclude(decision='pending').exists())
            in_behalf_by_employee[req.employee_id].append(req)
```

(`req.created_by == request.user` is already guaranteed by the queryset's `created_by=request.user` filter, so the in-behalf branch's `can_edit_delete` only needs the status/approvals check — unlike the top-level `leave_requests` list, which mixes both self-submitted and would-be manager-logged rows for this same employee and does need the full `created_by_id == request.user.id` check.)

In `templates/hr/my_profile.html`, add Edit/Delete controls to the "My Leave Requests" table. Change the `<td>{{ r.start_date }} – {{ r.end_date }}</td>` row (currently line 202) area — add a 4th column. First add a header cell:

```html
              <thead class="table-light"><tr><th>Type</th><th>Dates</th><th>Status</th><th></th></tr></thead>
```

Then add the new `<td>` right after the Status `<td>` closes (after line 219's `{% endif %}`, before `</tr>`):

```html
                  <td class="text-end">
                    {% if r.can_edit_delete %}
                    <button type="button" class="btn btn-sm btn-outline-secondary py-0" data-bs-toggle="collapse" data-bs-target="#editLeave{{ r.pk }}" id="edit_leave_request_{{ r.pk }}">
                      <i class="bi bi-pencil"></i>
                    </button>
                    <form method="post" class="d-inline" onsubmit="return confirm('Delete this leave request?');">
                      {% csrf_token %}
                      <input type="hidden" name="action" value="delete_leave_request">
                      <input type="hidden" name="request_id" value="{{ r.pk }}">
                      <button type="submit" class="btn btn-sm btn-outline-danger py-0" id="delete_leave_request_{{ r.pk }}">
                        <i class="bi bi-trash"></i>
                      </button>
                    </form>
                    {% endif %}
                  </td>
```

And add the (initially collapsed) edit form, right after the closing `</tr>` for this row, as its own full-width row:

```html
                {% if r.can_edit_delete %}
                <tr class="collapse" id="editLeave{{ r.pk }}">
                  <td colspan="4">
                    <form method="post" enctype="multipart/form-data" class="border rounded p-2">
                      {% csrf_token %}
                      <input type="hidden" name="action" value="edit_leave_request">
                      <input type="hidden" name="request_id" value="{{ r.pk }}">
                      <div class="row g-2">
                        <div class="col-md-3">
                          <label class="form-label small">Start</label>
                          <input type="date" name="start_date" class="form-control form-control-sm" value="{{ r.start_date|date:'Y-m-d' }}" required>
                        </div>
                        <div class="col-md-3">
                          <label class="form-label small">End</label>
                          <input type="date" name="end_date" class="form-control form-control-sm" value="{{ r.end_date|date:'Y-m-d' }}" required>
                        </div>
                        <div class="col-md-6">
                          <label class="form-label small">Reason</label>
                          <input type="text" name="employee_reason" class="form-control form-control-sm" value="{{ r.employee_reason }}">
                        </div>
                      </div>
                      <input type="hidden" name="leave_type" value="{{ r.leave_type_id }}">
                      <button type="submit" class="btn btn-sm btn-primary mt-2">Save Changes</button>
                    </form>
                  </td>
                </tr>
                {% endif %}
```

(The edit form keeps `leave_type` fixed via a hidden input rather than exposing a picker — simpler UI, matching "not cluttered"; a user who needs to change the leave TYPE, not just dates/reason, deletes and re-submits instead, which is already a one-click round trip.)

Apply the identical `{% if req.can_edit_delete %}` icon-buttons (no inline edit form needed here — same principle, but scope this task to Delete only for the in-behalf list to keep the surface small; Edit for in-behalf requests can reuse the same pattern in a follow-up if requested) to the in-behalf-requests block. Find the `<div>Your request: ...</div>` lines inside `{% for req in r.in_behalf_requests %}` (both the inline and the `+N more` collapsed copies) and add, right after the existing `{{ req.leave_type.name }}, {{ req.start_date }}–{{ req.end_date }}` line, in both copies:

```html
                  {% if req.can_edit_delete %}
                  <form method="post" class="d-inline ms-1" onsubmit="return confirm('Delete this leave request?');">
                    {% csrf_token %}
                    <input type="hidden" name="action" value="delete_leave_request">
                    <input type="hidden" name="request_id" value="{{ req.pk }}">
                    <button type="submit" class="btn btn-sm btn-link text-danger p-0" id="delete_leave_request_{{ req.pk }}">
                      <i class="bi bi-trash"></i>
                    </button>
                  </form>
                  {% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.MyProfileLeaveEditDeleteUITests hr.tests.MyProfileManagerInBehalfEditDeleteUITests -v 2`
Expected: PASS

- [ ] **Step 5: Run the broader My Profile suite**

Run: `python manage.py test hr.tests.MyProfileLeaveRequestTests hr.tests.ManagerSeesOnlyTheirOwnInBehalfStatusTests hr.tests.MyProfileHistoryCollapseTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/views.py templates/hr/my_profile.html hr/tests.py
git commit -m "hr: add Edit/Delete UI for pending leave requests on My Profile"
```

---

### Task 8: My Profile — Edit/Delete UI for pending Attendance Exceptions

**Files:**
- Modify: `hr/views.py:336-357` (`my_profile`, attendance-exception POST branch)
- Modify: `templates/hr/my_profile.html:330-367` (My Attendance Exceptions table)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `edit_attendance_exception`, `delete_attendance_exception` (Task 4).
- Produces: `my_profile` handles `action in ('edit_attendance_exception', 'delete_attendance_exception')`. Each `AttendanceException` in `context['my_attendance_exceptions']` gets `e.can_edit_delete`.

- [ ] **Step 1: Write the failing tests**

```python
class MyProfileExceptionEditDeleteUITests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='MPEED-1')
        self.user = _login_user('mpeed_user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.exc = _submit_aex(
            employee=self.emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
            reason_category='site_visit', created_by=self.user)
        self.client.login(username='mpeed_user', password='testpass123')

    def test_edit_delete_buttons_render_for_own_pending_exception(self):
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, f'edit_attendance_exception_{self.exc.pk}')
        self.assertContains(resp, f'delete_attendance_exception_{self.exc.pk}')

    def test_no_buttons_once_decided(self):
        self.exc.status = 'approved'
        self.exc.save(update_fields=['status'])
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertNotContains(resp, f'delete_attendance_exception_{self.exc.pk}')

    def test_post_edit_updates_exception(self):
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'edit_attendance_exception', 'request_id': self.exc.pk,
            'event_date': '2026-07-21', 'event_start_time': '10:00',
            'reason_category': 'outside_meeting', 'custom_reason': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.exc.refresh_from_db()
        self.assertEqual(self.exc.event_date, _date(2026, 7, 21))
        self.assertEqual(self.exc.reason_category, 'outside_meeting')

    def test_post_delete_removes_exception(self):
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'delete_attendance_exception', 'request_id': self.exc.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(AttendanceException.objects.filter(pk=self.exc.pk).exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.MyProfileExceptionEditDeleteUITests -v 2`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, inside `my_profile()`, add two branches right after the attendance-exception submit block (after `context['attendance_exception_form'] = exception_form`, currently line 357, before `if emp:` at line 359):

```python
    is_edit_exception_post = (
        emp and request.method == 'POST' and request.POST.get('action') == 'edit_attendance_exception')
    if is_edit_exception_post:
        from hr.attendance_exception_services import edit_attendance_exception
        target = AttendanceException.objects.filter(pk=request.POST.get('request_id')).first()
        if target is None or target.created_by_id != request.user.id:
            return HttpResponse('You did not submit this request.', status=403)
        edit_exc_form = AttendanceExceptionForm(request.POST)
        if edit_exc_form.is_valid():
            try:
                edit_attendance_exception(
                    target, request.user, event_date=edit_exc_form.cleaned_data['event_date'],
                    event_start_time=edit_exc_form.cleaned_data['event_start_time'],
                    reason_category=edit_exc_form.cleaned_data['reason_category'],
                    custom_reason=edit_exc_form.cleaned_data['custom_reason'],
                    employee_comment=edit_exc_form.cleaned_data['employee_comment'])
                messages.success(request, 'Attendance exception updated.')
            except ValueError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, 'Could not update the exception — please check the form.')
        return redirect('hr:my_profile')

    is_delete_exception_post = (
        emp and request.method == 'POST' and request.POST.get('action') == 'delete_attendance_exception')
    if is_delete_exception_post:
        from hr.attendance_exception_services import delete_attendance_exception
        target = AttendanceException.objects.filter(pk=request.POST.get('request_id')).first()
        if target is None or target.created_by_id != request.user.id:
            return HttpResponse('You did not submit this request.', status=403)
        try:
            delete_attendance_exception(target, request.user)
            messages.success(request, 'Attendance exception deleted.')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('hr:my_profile')
```

`AttendanceException` needs to be importable in this function's scope — `my_profile` already has `from .models import AttendanceRecord, Asset, Vehicle` inside `if emp:`; add `AttendanceException` to the top of the function instead, alongside the existing `from .forms import LeaveRequestForm` (so it's available before the `if emp:` block, since the new POST branches run before that block too):

```python
    from .forms import LeaveRequestForm, AttendanceExceptionForm
    from .models import AttendanceException
```

(`AttendanceExceptionForm` is likely already imported at module level in `hr/views.py` — check `hr/views.py`'s top-of-file imports; if `from .forms import ... AttendanceExceptionForm ...` is already present there, skip re-importing it here and only add the `AttendanceException` model import.)

Now attach `can_edit_delete`. Change the `my_attendance_exceptions` context line (currently line 392):

```python
        context['my_attendance_exceptions'] = list(emp.attendance_exceptions.order_by('-event_date')[:10])
        for e in context['my_attendance_exceptions']:
            e.can_edit_delete = bool(e.created_by_id == request.user.id and e.status == 'pending')
```

In `templates/hr/my_profile.html`, add a 4th column to the "My Attendance Exceptions" table. Header (currently line 341):

```html
            <thead class="table-light"><tr><th>Date</th><th>Reason</th><th>Status</th><th></th></tr></thead>
```

Add the new `<td>` after the Status `<td>` closes (after line 357's `{% endif %}` inside that cell, before `</tr>`):

```html
                <td class="text-end">
                  {% if e.can_edit_delete %}
                  <button type="button" class="btn btn-sm btn-outline-secondary py-0" data-bs-toggle="collapse" data-bs-target="#editException{{ e.pk }}" id="edit_attendance_exception_{{ e.pk }}">
                    <i class="bi bi-pencil"></i>
                  </button>
                  <form method="post" class="d-inline" onsubmit="return confirm('Delete this attendance exception?');">
                    {% csrf_token %}
                    <input type="hidden" name="action" value="delete_attendance_exception">
                    <input type="hidden" name="request_id" value="{{ e.pk }}">
                    <button type="submit" class="btn btn-sm btn-outline-danger py-0" id="delete_attendance_exception_{{ e.pk }}">
                      <i class="bi bi-trash"></i>
                    </button>
                  </form>
                  {% endif %}
                </td>
```

And, right after the row's closing `</tr>`, the collapsed edit form:

```html
              {% if e.can_edit_delete %}
              <tr class="collapse" id="editException{{ e.pk }}">
                <td colspan="4">
                  <form method="post" class="border rounded p-2">
                    {% csrf_token %}
                    <input type="hidden" name="action" value="edit_attendance_exception">
                    <input type="hidden" name="request_id" value="{{ e.pk }}">
                    <div class="row g-2">
                      <div class="col-md-4">
                        <label class="form-label small">Event Date</label>
                        <input type="date" name="event_date" class="form-control form-control-sm" value="{{ e.event_date|date:'Y-m-d' }}" required>
                      </div>
                      <div class="col-md-4">
                        <label class="form-label small">Event Start Time</label>
                        <input type="time" name="event_start_time" class="form-control form-control-sm" value="{{ e.event_start_time|time:'H:i' }}" required>
                      </div>
                      <div class="col-md-4">
                        <label class="form-label small">Reason</label>
                        <select name="reason_category" class="form-select form-select-sm">
                          {% for value, label in e.REASON_CHOICES %}
                          <option value="{{ value }}" {% if e.reason_category == value %}selected{% endif %}>{{ label }}</option>
                          {% endfor %}
                        </select>
                      </div>
                    </div>
                    <div class="mt-2">
                      <label class="form-label small">Custom Reason (only used if "Other" is selected)</label>
                      <input type="text" name="custom_reason" class="form-control form-control-sm" value="{{ e.custom_reason }}">
                    </div>
                    <button type="submit" class="btn btn-sm btn-primary mt-2">Save Changes</button>
                  </form>
                </td>
              </tr>
              {% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.MyProfileExceptionEditDeleteUITests -v 2`
Expected: PASS

- [ ] **Step 5: Run the broader My Profile + attendance-exception suite**

Run: `python manage.py test hr.tests.MyProfileAttendanceExceptionTests hr.tests.MyProfileExceptionDisplayTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/views.py templates/hr/my_profile.html hr/tests.py
git commit -m "hr: add Edit/Delete UI for pending attendance exceptions on My Profile"
```

---

### Task 9: My Profile — Request Revoke UI for approved Leave Requests + Revoked badge

**Files:**
- Modify: `hr/views.py` (`my_profile`, new POST branch)
- Modify: `templates/hr/my_profile.html:178-230` (My Leave Requests table — Request Revoke button + Revoked badge), `templates/hr/my_profile.html` in-behalf block (Revoked badge only — no Request-Revoke button there, per spec)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `request_leave_revoke` (Task 5).
- Produces: `my_profile` handles `action == 'request_leave_revoke'`.

- [ ] **Step 1: Write the failing tests**

```python
class MyProfileRequestRevokeLeaveUITests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='MPRRL-1')
        self.lt, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': Decimal('30')})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        self.user = _login_user('mprrl_user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.req = submit_leave_request(
            employee=self.emp, leave_type=self.lt, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2),
            created_by=self.user)
        approver = make_user('mprrl_approver', password='x')
        LeaveDashboardAccess.objects.create(user=approver, is_active=True)
        record_approver_decision(self.req, approver, 'approved')
        self.client.login(username='mprrl_user', password='testpass123')

    def test_request_revoke_button_renders_on_approved_own_request(self):
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, f'request_revoke_leave_{self.req.pk}')

    def test_post_creates_pending_revoke_request(self):
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'request_leave_revoke', 'request_id': self.req.pk, 'reason': 'Plans changed.',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(LeaveRevokeRequest.objects.filter(leave_request=self.req, status='pending').exists())

    def test_awaiting_review_note_shows_after_requesting(self):
        LeaveRevokeRequest.objects.create(leave_request=self.req, requested_by=self.user, reason='Plans changed.')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, 'Revoke requested')

    def test_revoked_badge_renders(self):
        from hr.leave_approval_services import revoke_leave_request
        revoker = make_user('mprrl_revoker', password='x')
        revoke_leave_request(self.req, revoker, 'Applied for testing.')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, 'Revoked')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.MyProfileRequestRevokeLeaveUITests -v 2`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, add a new POST branch inside `my_profile()`, right after the edit/delete branches added in Task 7:

```python
    is_request_revoke_leave_post = (
        emp and request.method == 'POST' and request.POST.get('action') == 'request_leave_revoke')
    if is_request_revoke_leave_post:
        from hr.leave_approval_services import request_leave_revoke
        target = LeaveRequest.objects.filter(pk=request.POST.get('request_id')).first()
        if target is None or not (target.employee.user_id and target.employee.user_id == request.user.id):
            return HttpResponse('This is not your leave request.', status=403)
        try:
            request_leave_revoke(target, request.user, request.POST.get('reason', ''))
            messages.success(request, 'Revoke requested — it will not take effect until reviewed.')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('hr:my_profile')
```

Attach a `pending_revoke_request` attribute alongside `can_edit_delete` on the `leave_requests` list built in Task 7 (extend that same loop):

```python
        for r in context['leave_requests']:
            r.can_edit_delete = bool(
                r.created_by_id == request.user.id and r.status == 'pending'
                and not r.approvals.exclude(decision='pending').exists())
            r.can_request_revoke = bool(
                r.employee.user_id == request.user.id and r.status == 'approved'
                and not r.revoke_requests.filter(status='pending').exists())
            r.pending_revoke_request = r.revoke_requests.filter(status='pending').first()
```

In `templates/hr/my_profile.html`'s "My Leave Requests" table, extend the status `<td>` block (Task 7 already added a 4th `<td>` for Edit/Delete — this task adds to the STATUS cell, currently lines 204-219):

```html
                  <td>
                    {% if r.status == 'approved' %}
                      <span class="badge bg-success">Approved</span>
                      {% if r.decided_by_display %}<div class="small text-muted">{{ r.decided_by_display }}</div>{% endif %}
                      {% if r.pending_revoke_request %}
                        <div class="small text-muted"><i class="bi bi-hourglass-split"></i> Revoke requested — awaiting review</div>
                      {% elif r.can_request_revoke %}
                        <button type="button" class="btn btn-sm btn-link text-danger p-0" data-bs-toggle="collapse" data-bs-target="#requestRevoke{{ r.pk }}" id="request_revoke_leave_{{ r.pk }}">
                          Request Revoke
                        </button>
                        <div class="collapse mt-1" id="requestRevoke{{ r.pk }}">
                          <form method="post" class="border rounded p-2">
                            {% csrf_token %}
                            <input type="hidden" name="action" value="request_leave_revoke">
                            <input type="hidden" name="request_id" value="{{ r.pk }}">
                            <textarea name="reason" class="form-control form-control-sm mb-1" rows="2" placeholder="Why does this leave need to be revoked? — required" required></textarea>
                            <button type="submit" class="btn btn-sm btn-outline-danger">Submit Revoke Request</button>
                          </form>
                        </div>
                      {% endif %}
                    {% elif r.status == 'revoked' %}
                      <span class="badge bg-secondary">Revoked</span>
                      <div class="small text-muted">
                        {% if r.revoked_by %}by {{ r.revoked_by.get_full_name|default:r.revoked_by.username }}{% endif %}
                        {% if r.revoked_at %} on {{ r.revoked_at|date:'d M Y' }}{% endif %}
                      </div>
                      {% if r.revoke_reason %}<div class="small text-muted fst-italic">"{{ r.revoke_reason }}"</div>{% endif %}
                    {% elif r.status == 'disapproved' %}
                      <span class="badge bg-danger">Disapproved</span>
                      {% if r.decided_by_display %}<div class="small text-muted">{{ r.decided_by_display }}</div>{% endif %}
                      {% if r.salary_deduction_applicable %}<div class="small text-danger">Taking this leave will result in a salary deduction.</div>{% endif %}
                    {% else %}
                      <span class="badge bg-warning text-dark">Pending</span>
                      <div class="small text-muted">
                        {% with waiting=r.pending_approvers %}
                          {% if waiting %}Waiting on {% for a in waiting %}{{ a.get_full_name|default:a.username }}{% if not forloop.last %}, {% endif %}{% endfor %}
                          {% else %}Waiting on a Super Admin to review (no other eligible approver is assigned){% endif %}
                        {% endwith %}
                      </div>
                    {% endif %}
                  </td>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.MyProfileRequestRevokeLeaveUITests -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hr/views.py templates/hr/my_profile.html hr/tests.py
git commit -m "hr: add Request Revoke UI and Revoked badge for approved leave requests"
```

---

### Task 10: My Profile — Request Revoke UI for approved Attendance Exceptions + Revoked badge

**Files:**
- Modify: `hr/views.py` (`my_profile`, new POST branch)
- Modify: `templates/hr/my_profile.html:330-367` (My Attendance Exceptions table)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `request_attendance_exception_revoke` (Task 6).
- Produces: `my_profile` handles `action == 'request_attendance_exception_revoke'`.

- [ ] **Step 1: Write the failing tests**

```python
class MyProfileRequestRevokeExceptionUITests(TestCase):
    def setUp(self):
        self.manager = make_employee(iqama='MPRRE-MGR', name='RRE Manager')
        self.manager_user = _login_user('mprre_mgr')
        self.manager.user = self.manager_user
        self.manager.save(update_fields=['user'])
        self.emp = make_employee(iqama='MPRRE-1')
        self.emp.main_manager = self.manager
        self.emp.save(update_fields=['main_manager'])
        self.user = _login_user('mprre_user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.exc = _submit_aex(
            employee=self.emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
            reason_category='site_visit', created_by=self.user)
        decide_attendance_exception(self.exc, self.manager_user, 'approved')
        self.exc.refresh_from_db()
        self.client.login(username='mprre_user', password='testpass123')

    def test_request_revoke_button_renders(self):
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, f'request_revoke_exception_{self.exc.pk}')

    def test_post_creates_pending_revoke_request(self):
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'request_attendance_exception_revoke', 'request_id': self.exc.pk, 'reason': 'No longer needed.',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(AttendanceExceptionRevokeRequest.objects.filter(
            attendance_exception=self.exc, status='pending').exists())

    def test_revoked_badge_renders(self):
        from hr.attendance_exception_services import revoke_attendance_exception
        revoker = _login_user('mprre_revoker')
        revoke_attendance_exception(self.exc, revoker, 'Applied for testing.')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertContains(resp, 'Revoked')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.MyProfileRequestRevokeExceptionUITests -v 2`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, add a POST branch inside `my_profile()`, after the exception edit/delete branches from Task 8:

```python
    is_request_revoke_exception_post = (
        emp and request.method == 'POST' and request.POST.get('action') == 'request_attendance_exception_revoke')
    if is_request_revoke_exception_post:
        from hr.attendance_exception_services import request_attendance_exception_revoke
        target = AttendanceException.objects.filter(pk=request.POST.get('request_id')).first()
        if target is None or not (target.employee.user_id and target.employee.user_id == request.user.id):
            return HttpResponse('This is not your attendance exception.', status=403)
        try:
            request_attendance_exception_revoke(target, request.user, request.POST.get('reason', ''))
            messages.success(request, 'Revoke requested — it will not take effect until reviewed.')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('hr:my_profile')
```

Extend the `my_attendance_exceptions` loop from Task 8:

```python
        for e in context['my_attendance_exceptions']:
            e.can_edit_delete = bool(e.created_by_id == request.user.id and e.status == 'pending')
            e.can_request_revoke = bool(
                e.employee.user_id == request.user.id and e.status == 'approved'
                and not e.revoke_requests.filter(status='pending').exists())
            e.pending_revoke_request = e.revoke_requests.filter(status='pending').first()
```

In `templates/hr/my_profile.html`'s "My Attendance Exceptions" table, extend the status `<td>` (currently lines 347-357):

```html
                <td>
                  {% if e.status == 'approved' %}<span class="badge bg-success">Approved</span>
                    {% if e.pending_revoke_request %}
                      <div class="small text-muted"><i class="bi bi-hourglass-split"></i> Revoke requested — awaiting review</div>
                    {% elif e.can_request_revoke %}
                      <button type="button" class="btn btn-sm btn-link text-danger p-0" data-bs-toggle="collapse" data-bs-target="#requestRevokeExc{{ e.pk }}" id="request_revoke_exception_{{ e.pk }}">
                        Request Revoke
                      </button>
                      <div class="collapse mt-1" id="requestRevokeExc{{ e.pk }}">
                        <form method="post" class="border rounded p-2">
                          {% csrf_token %}
                          <input type="hidden" name="action" value="request_attendance_exception_revoke">
                          <input type="hidden" name="request_id" value="{{ e.pk }}">
                          <textarea name="reason" class="form-control form-control-sm mb-1" rows="2" placeholder="Why does this need to be revoked? — required" required></textarea>
                          <button type="submit" class="btn btn-sm btn-outline-danger">Submit Revoke Request</button>
                        </form>
                      </div>
                    {% endif %}
                  {% elif e.status == 'revoked' %}
                    <span class="badge bg-secondary">Revoked</span>
                    <div class="small text-muted">
                      {% if e.revoked_by %}by {{ e.revoked_by.get_full_name|default:e.revoked_by.username }}{% endif %}
                      {% if e.revoked_at %} on {{ e.revoked_at|date:'d M Y' }}{% endif %}
                    </div>
                    {% if e.revoke_reason %}<div class="small text-muted fst-italic">"{{ e.revoke_reason }}"</div>{% endif %}
                  {% elif e.status == 'rejected' %}<span class="badge bg-danger">Rejected</span>
                  {% elif e.status == 'expired' %}<span class="badge bg-secondary">Expired</span>
                  {% else %}<span class="badge bg-warning text-dark">Pending</span>{% endif %}
                  {% if e.is_overridden %}
                    <span class="badge bg-info text-dark ms-1">
                      {% if e.overridden_by %}Overridden by {{ e.overridden_by.get_full_name|default:e.overridden_by.username }}{% else %}Overridden{% endif %}
                    </span>
                  {% endif %}
                </td>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.MyProfileRequestRevokeExceptionUITests -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hr/views.py templates/hr/my_profile.html hr/tests.py
git commit -m "hr: add Request Revoke UI and Revoked badge for approved attendance exceptions"
```

---

### Task 11: Leave Approval Queue — Direct Revoke UI + Revoked badge in History

**Files:**
- Modify: `hr/views.py:2110-2145` (`LeaveRequestListView` — add `post()`), `hr/views.py:2935-2981` (`LeaveRequestDetailView.post` — add `revoke` branch)
- Modify: `templates/hr/leave_request_list.html:59-97` (History card), `templates/hr/leave_request_detail.html:41-62` (Status block)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `revoke_leave_request` (Task 5), `has_override_access` (existing).
- Produces: `LeaveRequestListView.post()` handles `action == 'revoke'`. `LeaveRequestDetailView.post()` gains an `elif action == 'revoke':` branch.

- [ ] **Step 1: Write the failing tests**

```python
class DirectRevokeLeaveRequestQueueUITests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='DRLQ-1')
        self.lt, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': Decimal('30')})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        self.superadmin = make_user('drlq_super', password='x')
        self.superadmin.set_password('testpass123')
        from accounts.models import Role
        role, _ = Role.objects.get_or_create(name='super_admin')
        self.superadmin.role = role
        self.superadmin.save()
        req = submit_leave_request(
            employee=self.emp, leave_type=self.lt, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2),
            created_by=self.superadmin)
        approver = make_user('drlq_approver', password='x')
        LeaveDashboardAccess.objects.create(user=approver, is_active=True)
        record_approver_decision(req, approver, 'approved')
        self.req = req
        self.client.login(username='drlq_super', password='testpass123')

    def test_revoke_button_renders_in_history_for_override_access_holder(self):
        resp = self.client.get(reverse('hr:leave_request_list'))
        self.assertContains(resp, f'revoke_leave_{self.req.pk}')

    def test_post_revoke_via_list_view(self):
        resp = self.client.post(reverse('hr:leave_request_list'), {
            'action': 'revoke', 'request_id': self.req.pk, 'reason': 'No longer needed.',
        })
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'revoked')

    def test_post_revoke_via_detail_view(self):
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]), {
            'action': 'revoke', 'reason': 'No longer needed.',
        })
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'revoked')

    def test_non_override_user_cannot_revoke(self):
        grantee = make_user('drlq_grantee', password='x')
        grantee.set_password('testpass123')
        grantee.save()
        LeaveDashboardAccess.objects.create(user=grantee, is_active=True)
        self.client.logout()
        self.client.login(username='drlq_grantee', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_list'), {
            'action': 'revoke', 'request_id': self.req.pk, 'reason': 'Trying anyway.',
        })
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')  # unchanged

    def test_revoked_badge_renders_in_history(self):
        from hr.leave_approval_services import revoke_leave_request
        revoke_leave_request(self.req, self.superadmin, 'Applied for testing.')
        resp = self.client.get(reverse('hr:leave_request_list'))
        self.assertContains(resp, 'Revoked')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.DirectRevokeLeaveRequestQueueUITests -v 2`
Expected: FAIL (405 on POST to the list view — `ListView` has no `post()` yet — and no revoke branch on the detail view).

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, add a `post()` method to `LeaveRequestListView`, right after `get_context_data` (currently ending line 2143):

```python
    def post(self, request, *args, **kwargs):
        from hr.leave_approval_services import revoke_leave_request
        action = request.POST.get('action')
        if action == 'revoke':
            if not has_override_access(request.user):
                return HttpResponse('You do not have override access to revoke this request.', status=403)
            target = LeaveRequest.objects.filter(pk=request.POST.get('request_id')).first()
            if target is None:
                raise Http404('No such leave request.')
            try:
                revoke_leave_request(target, request.user, request.POST.get('reason', ''))
                messages.success(request, 'Leave request revoked.')
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect('hr:leave_request_list')
```

`Http404` needs importing at the top of `hr/views.py` if not already present (it's already used inside `TeamExceptionsView.post`, via a local `from django.http import Http404` — match that same local-import style here for consistency with the sibling view, rather than adding a new top-level import).

In `LeaveRequestDetailView.post()` (currently lines 2935-2981), add a new `elif` branch after the `elif action == 'override':` block (before `elif action == 'add_note':`):

```python
        elif action == 'revoke':
            if not has_override_access(request.user):
                return HttpResponse('You do not have override access to revoke this request.', status=403)
            from hr.leave_approval_services import revoke_leave_request
            try:
                revoke_leave_request(self.object, request.user, request.POST.get('reason', ''))
                messages.success(request, 'Leave request revoked.')
            except ValueError as exc:
                messages.error(request, str(exc))
```

In `templates/hr/leave_request_list.html`'s History card, add a Revoke control to each row and extend the status block (currently lines 80-86):

```html
              <td>
                {% if r.status == 'approved' %}<span class="badge bg-success">Approved</span>
                {% elif r.status == 'disapproved' %}<span class="badge bg-danger">Disapproved</span>
                {% elif r.status == 'revoked' %}<span class="badge bg-secondary">Revoked</span>
                {% else %}<span class="badge bg-secondary">{{ r.get_status_display }}</span>{% endif %}
                {% if r.is_overridden %}<span class="badge bg-info text-dark ms-1">Overridden</span>{% endif %}
                {% if r.status == 'revoked' %}
                  <div class="small text-muted">
                    {% if r.revoked_by %}by {{ r.revoked_by.get_full_name|default:r.revoked_by.username }}{% endif %}
                    {% if r.revoked_at %} on {{ r.revoked_at|date:'d M Y' }}{% endif %}
                  </div>
                  {% if r.revoke_reason %}<div class="small text-muted fst-italic">"{{ r.revoke_reason }}"</div>{% endif %}
                {% elif r.decided_by_display %}<div class="small text-muted">{{ r.decided_by_display }}</div>{% endif %}
                {% if r.status == 'approved' and can_revoke %}
                <div class="mt-1">
                  <button type="button" class="btn btn-sm btn-outline-danger" data-bs-toggle="collapse" data-bs-target="#revoke_{{ r.pk }}" id="revoke_leave_{{ r.pk }}">
                    Revoke
                  </button>
                  <div class="collapse mt-2" id="revoke_{{ r.pk }}">
                    <form method="post" class="border rounded p-2">
                      {% csrf_token %}
                      <input type="hidden" name="action" value="revoke">
                      <input type="hidden" name="request_id" value="{{ r.pk }}">
                      <textarea name="reason" class="form-control form-control-sm mb-1" rows="2" placeholder="Revoke reason — required" required></textarea>
                      <button type="submit" class="btn btn-sm btn-outline-danger">Confirm Revoke</button>
                    </form>
                  </div>
                </div>
                {% endif %}
              </td>
```

Add `can_revoke` to `LeaveRequestListView.get_context_data` (currently ending line 2143):

```python
        ctx['can_revoke'] = has_override_access(self.request.user)
```

In `templates/hr/leave_request_detail.html`, extend the status block (currently lines 41-49) and add a Revoke control near the existing Override form. Change the status `<p>` (lines 41-44):

```html
          <p class="mb-1"><strong>Status:</strong>
            {% if leave_request.status == 'approved' %}<span class="badge bg-success">Approved</span>
            {% elif leave_request.status == 'disapproved' %}<span class="badge bg-danger">Disapproved</span>
            {% elif leave_request.status == 'revoked' %}<span class="badge bg-secondary">Revoked</span>
            {% else %}<span class="badge bg-warning text-dark">Pending</span>{% endif %}
```

Add a revoked attribution block right after the existing `decided_by_display` line (currently line 56):

```html
            {% if leave_request.status == 'revoked' %}
              <div class="small text-muted mt-1">
                Revoked
                {% if leave_request.revoked_by %}by {{ leave_request.revoked_by.get_full_name|default:leave_request.revoked_by.username }}{% endif %}
                {% if leave_request.revoked_at %} on {{ leave_request.revoked_at|date:'d M Y, H:i' }}{% endif %}
                {% if leave_request.revoke_reason %} — "{{ leave_request.revoke_reason }}"{% endif %}
              </div>
            {% endif %}
```

Add a Revoke form after the existing Override form (currently lines 133-144, right before the closing `</div>` of the Approvals card body at line 145):

```html
          {% if can_override and leave_request.status == 'approved' %}
          <form method="post" class="border-top pt-3 mt-3" onsubmit="return confirm('Revoke this approved leave? It will no longer count toward taken days.');">
            {% csrf_token %}
            <input type="hidden" name="action" value="revoke">
            <div class="mb-2">
              <label class="form-label small">Revoke this approved leave</label>
              <textarea name="reason" class="form-control" rows="2" placeholder="Reason — required" required></textarea>
            </div>
            <button type="submit" class="btn btn-outline-danger btn-sm">Revoke</button>
          </form>
          {% endif %}
```

(Reuses `can_override`, already computed in `get_context_data` as `has_override_access(self.request.user) and not ctx['is_own_request']` — exactly the gate this button needs, no new context variable required here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.DirectRevokeLeaveRequestQueueUITests -v 2`
Expected: PASS

- [ ] **Step 5: Run the broader queue/detail suite**

Run: `python manage.py test hr.tests.LeaveRequestQueueViewTests hr.tests.LeaveRequestDetailViewTests hr.tests.LeaveQueueHistoryCollapseTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/views.py templates/hr/leave_request_list.html templates/hr/leave_request_detail.html hr/tests.py
git commit -m "hr: add direct-revoke UI and Revoked badge to the Leave Approval Queue"
```

---

### Task 12: Team Exceptions — Direct Revoke UI + Revoked badge in History

**Files:**
- Modify: `hr/views.py:3054-3069` (`TeamExceptionsView.get_context_data` — include `revoked` in the decided filter), `hr/views.py:3129-3176` (`TeamExceptionsView.post` — add `revoke_direct` branch)
- Modify: `templates/hr/team_exceptions.html:227-288` (History card)
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `revoke_attendance_exception` (Task 6), `has_override_access` (existing).
- Produces: `TeamExceptionsView.post()` handles `action == 'revoke_direct'`. `decided` queryset (line ~3067) includes `'revoked'`.

- [ ] **Step 1: Write the failing tests**

```python
class DirectRevokeTeamExceptionUITests(TestCase):
    def setUp(self):
        self.manager = make_employee(iqama='DRTE-MGR', name='DRTE Manager')
        self.manager_user = _login_user('drte_mgr')
        self.manager.user = self.manager_user
        self.manager.save(update_fields=['user'])
        self.emp = make_employee(iqama='DRTE-1')
        self.emp.main_manager = self.manager
        self.emp.save(update_fields=['main_manager'])
        self.creator = make_user('drte_creator')
        self.exc = _submit_aex(
            employee=self.emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
            reason_category='site_visit', created_by=self.creator)
        decide_attendance_exception(self.exc, self.manager_user, 'approved')
        self.exc.refresh_from_db()
        self.superadmin = make_user('drte_super', password='x')
        self.superadmin.set_password('testpass123')
        from accounts.models import Role
        role, _ = Role.objects.get_or_create(name='super_admin')
        self.superadmin.role = role
        self.superadmin.save()
        self.client.login(username='drte_super', password='testpass123')

    def test_revoke_button_renders_for_override_access_holder(self):
        resp = self.client.get(reverse('hr:team_exceptions'), {'tab': 'all'})
        self.assertContains(resp, f'revoke_exception_{self.exc.pk}')

    def test_post_revoke_direct(self):
        resp = self.client.post(reverse('hr:team_exceptions'), {
            'action': 'revoke_direct', 'exc_id': self.exc.pk, 'reason': 'No longer needed.', 'tab': 'all',
        })
        self.assertEqual(resp.status_code, 302)
        self.exc.refresh_from_db()
        self.assertEqual(self.exc.status, 'revoked')

    def test_revoked_row_included_in_history(self):
        from hr.attendance_exception_services import revoke_attendance_exception
        revoke_attendance_exception(self.exc, self.superadmin, 'Applied for testing.')
        resp = self.client.get(reverse('hr:team_exceptions'), {'tab': 'all'})
        self.assertContains(resp, 'Revoked')
        self.assertIn(self.exc, list(resp.context['decided_exceptions']))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.DirectRevokeTeamExceptionUITests -v 2`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, `TeamExceptionsView.get_context_data`, change the `decided` queryset (currently lines 3066-3069):

```python
        decided = list(
            self._tab_queryset(tab, user, emp).filter(status__in=('approved', 'rejected', 'revoked'))
            .select_related('employee', 'employee__main_manager', 'main_manager', 'decided_by', 'overridden_by')
            .order_by('-decided_at')[:50])
```

Add `ctx['can_revoke'] = has_override_access(user)` right after `ctx['show_all_tab'] = is_hr` (currently line 3119).

In `TeamExceptionsView.post()`, add a new branch after the existing `elif action == 'override':` block (currently ending line 3168), before the tab-redirect logic:

```python
        elif action == 'revoke_direct':
            if not has_override_access(request.user):
                return HttpResponse('You do not have override access to revoke this request.', status=403)
            if exc is None:
                raise Http404('No such attendance exception.')
            from hr.attendance_exception_services import revoke_attendance_exception
            try:
                revoke_attendance_exception(exc, request.user, request.POST.get('reason', ''))
                messages.success(request, 'Attendance exception revoked.')
            except ValueError as e:
                messages.error(request, str(e))
```

In `templates/hr/team_exceptions.html`'s History card, extend the status `<td>` (currently lines 247-275):

```html
            <td>
              {% if exc.status == 'approved' %}<span class="badge bg-success">Approved</span>
              {% elif exc.status == 'rejected' %}<span class="badge bg-danger">Rejected</span>
              {% elif exc.status == 'expired' %}<span class="badge bg-secondary">Expired</span>
              {% elif exc.status == 'revoked' %}<span class="badge bg-secondary">Revoked</span>
              {% else %}<span class="badge bg-warning text-dark">{{ exc.get_status_display }}</span>{% endif %}
              {% if exc.is_overridden %}
                <span class="badge bg-info text-dark ms-1">
                  Overridden{% if exc.overridden_by %} by {{ exc.overridden_by.get_full_name|default:exc.overridden_by.username }}{% endif %}
                </span>
              {% endif %}
              {% if exc.status == 'revoked' %}
                <div class="small text-muted">
                  {% if exc.revoked_by %}by {{ exc.revoked_by.get_full_name|default:exc.revoked_by.username }}{% endif %}
                  {% if exc.revoked_at %} on {{ exc.revoked_at|date:'d M Y' }}{% endif %}
                </div>
                {% if exc.revoke_reason %}<div class="small text-muted fst-italic">"{{ exc.revoke_reason }}"</div>{% endif %}
              {% endif %}
              {% if exc.can_override %}
              <button type="button" class="btn btn-sm btn-outline-warning ms-1" data-bs-toggle="collapse" data-bs-target="#override_{{ exc.pk }}">
                Override
              </button>
              <div class="collapse mt-2" id="override_{{ exc.pk }}">
                <form method="post" class="border rounded p-2">
                  {% csrf_token %}
                  <input type="hidden" name="action" value="override">
                  <input type="hidden" name="exc_id" value="{{ exc.pk }}">
                  <input type="hidden" name="tab" value="{{ tab }}">
                  <textarea name="reason" class="form-control form-control-sm mb-1" rows="2" placeholder="Override reason — required" required></textarea>
                  <div>
                    <button type="submit" name="decision" value="approved" class="btn btn-sm btn-outline-success">Override — Approve</button>
                    <button type="submit" name="decision" value="rejected" class="btn btn-sm btn-outline-danger">Override — Reject</button>
                  </div>
                </form>
              </div>
              {% endif %}
              {% if exc.status == 'approved' and can_revoke %}
              <button type="button" class="btn btn-sm btn-outline-danger ms-1" data-bs-toggle="collapse" data-bs-target="#revoke_{{ exc.pk }}" id="revoke_exception_{{ exc.pk }}">
                Revoke
              </button>
              <div class="collapse mt-2" id="revoke_{{ exc.pk }}">
                <form method="post" class="border rounded p-2" onsubmit="return confirm('Revoke this approved exception? The day will no longer be excused.');">
                  {% csrf_token %}
                  <input type="hidden" name="action" value="revoke_direct">
                  <input type="hidden" name="exc_id" value="{{ exc.pk }}">
                  <input type="hidden" name="tab" value="{{ tab }}">
                  <textarea name="reason" class="form-control form-control-sm mb-1" rows="2" placeholder="Revoke reason — required" required></textarea>
                  <button type="submit" class="btn btn-sm btn-outline-danger">Confirm Revoke</button>
                </form>
              </div>
              {% endif %}
            </td>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.DirectRevokeTeamExceptionUITests -v 2`
Expected: PASS

- [ ] **Step 5: Run the broader Team Exceptions suite**

Run: `python manage.py test hr.tests.TeamExceptionsHistoryTableTests hr.tests.TeamExceptionsDecideOverrideTests hr.tests.TeamExceptionsHistoryCollapseTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/views.py templates/hr/team_exceptions.html hr/tests.py
git commit -m "hr: add direct-revoke UI and Revoked badge to Team Exceptions"
```

---

### Task 13: Leave Approval Queue — Pending Revoke Requests section + sidebar badge

**Files:**
- Modify: `hr/views.py` (`LeaveRequestListView.get_context_data`, `post()`)
- Modify: `templates/hr/leave_request_list.html` (new card between Pending and History)
- Modify: `hr/context_processors.py:14-15`
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `decide_leave_revoke_request` (Task 5).
- Produces: `LeaveRequestListView.post()` handles `action == 'decide_revoke_request'`. `ctx['pending_leave_revoke_requests']`. `pending_counts`'s `leave_requests_pending_count` includes pending `LeaveRevokeRequest` rows.

- [ ] **Step 1: Write the failing tests**

```python
class PendingRevokeRequestsLeaveQueueUITests(TestCase):
    def setUp(self):
        self.emp = make_employee(iqama='PRRQ-1')
        self.lt, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': Decimal('30')})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.lt, year=2026, entitled_days=Decimal('30'))
        self.superadmin = make_user('prrq_super', password='x')
        self.superadmin.set_password('testpass123')
        from accounts.models import Role
        role, _ = Role.objects.get_or_create(name='super_admin')
        self.superadmin.role = role
        self.superadmin.save()
        self.user = _login_user('prrq_user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        req = submit_leave_request(
            employee=self.emp, leave_type=self.lt, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2),
            created_by=self.user)
        approver = make_user('prrq_approver', password='x')
        LeaveDashboardAccess.objects.create(user=approver, is_active=True)
        record_approver_decision(req, approver, 'approved')
        self.req = req
        self.revoke_req = LeaveRevokeRequest.objects.create(
            leave_request=req, requested_by=self.user, reason='Plans changed.')

    def test_pending_revoke_request_shows_on_queue_page(self):
        self.client.login(username='prrq_super', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_list'))
        self.assertContains(resp, 'Plans changed.')
        self.assertEqual(list(resp.context['pending_leave_revoke_requests']), [self.revoke_req])

    def test_approve_applies_the_revoke(self):
        self.client.login(username='prrq_super', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_list'), {
            'action': 'decide_revoke_request', 'revoke_request_id': self.revoke_req.pk, 'decision': 'approved',
        })
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'revoked')

    def test_reject_requires_decision_note(self):
        self.client.login(username='prrq_super', password='testpass123')
        self.client.post(reverse('hr:leave_request_list'), {
            'action': 'decide_revoke_request', 'revoke_request_id': self.revoke_req.pk, 'decision': 'rejected',
            'decision_note': '',
        })
        self.revoke_req.refresh_from_db()
        self.assertEqual(self.revoke_req.status, 'pending')  # blocked, unchanged

    def test_sidebar_badge_includes_pending_revoke_requests(self):
        self.client.login(username='prrq_super', password='testpass123')
        resp = self.client.get(reverse('hr:hr_dashboard'))
        # 1 pending revoke request; no pending LeaveRequest (the only one was
        # already approved in setUp) — the badge count is exactly 1.
        self.assertEqual(resp.context['leave_requests_pending_count'], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.PendingRevokeRequestsLeaveQueueUITests -v 2`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, `LeaveRequestListView.get_context_data`, add after `ctx['current_sort'] = ...` (currently ending line 2142):

```python
        ctx['pending_leave_revoke_requests'] = (
            LeaveRevokeRequest.objects.filter(status='pending')
            .select_related('leave_request__employee', 'leave_request__leave_type', 'requested_by')
            .order_by('created_at'))
```

Extend `LeaveRequestListView.post()` (added in Task 11) with a second branch:

```python
        elif action == 'decide_revoke_request':
            from hr.leave_approval_services import decide_leave_revoke_request
            revoke_req = LeaveRevokeRequest.objects.filter(pk=request.POST.get('revoke_request_id')).first()
            if revoke_req is None:
                raise Http404('No such revoke request.')
            try:
                decide_leave_revoke_request(
                    revoke_req, request.user, request.POST.get('decision'),
                    decision_note=request.POST.get('decision_note', ''))
                messages.success(request, 'Revoke request decided.')
            except ValueError as exc:
                messages.error(request, str(exc))
```

(No permission pre-check here beyond what `decide_leave_revoke_request` itself enforces — deciding a revoke request uses the same roster as normal leave decisions, and this view is already gated to `can_view_leave_dashboard`, i.e. exactly that roster plus override-access holders; the service function's own self-approval check covers the rest, matching how the `'decide'`/`'override'` actions elsewhere in this codebase don't duplicate a permission check the view-level gate already provides.)

In `hr/context_processors.py`, change `pending_counts` (currently lines 14-15):

```python
    if can_view_leave_dashboard(user):
        from .models import LeaveRevokeRequest
        ctx['leave_requests_pending_count'] = (
            LeaveRequest.objects.filter(status='pending').count()
            + LeaveRevokeRequest.objects.filter(status='pending').count())
```

In `templates/hr/leave_request_list.html`, add a new card between the Pending card (ending `</div>` at line 57) and the History card (starting line 59):

```html
  {% if pending_leave_revoke_requests %}
  <div class="card mb-4">
    <div class="card-header d-flex justify-content-between align-items-center">
      <h5 class="mb-0"><i class="bi bi-arrow-counterclockwise"></i> Pending Revoke Requests
        <span class="badge bg-warning text-dark ms-2">{{ pending_leave_revoke_requests|length }}</span></h5>
    </div>
    <div class="card-body p-0">
      <table class="table table-sm mb-0">
        <thead class="table-light"><tr><th>Employee</th><th>Leave</th><th>Requested By</th><th>Reason</th><th style="min-width:220px;">Decide</th></tr></thead>
        <tbody>
          {% for rr in pending_leave_revoke_requests %}
          <tr>
            <td>{{ rr.leave_request.employee.full_name }}</td>
            <td>{{ rr.leave_request.leave_type.name }}, {{ rr.leave_request.start_date }}–{{ rr.leave_request.end_date }}</td>
            <td class="small text-muted">{{ rr.requested_by.get_full_name|default:rr.requested_by.username }}</td>
            <td class="small">{{ rr.reason }}</td>
            <td>
              <form method="post" class="d-flex flex-column gap-1">
                {% csrf_token %}
                <input type="hidden" name="action" value="decide_revoke_request">
                <input type="hidden" name="revoke_request_id" value="{{ rr.pk }}">
                <textarea name="decision_note" class="form-control form-control-sm" rows="1" placeholder="Note (required if rejecting)"></textarea>
                <div class="d-flex gap-1">
                  <button type="submit" name="decision" value="approved" class="btn btn-sm btn-outline-success">Approve Revoke</button>
                  <button type="submit" name="decision" value="rejected" class="btn btn-sm btn-outline-danger">Reject</button>
                </div>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.PendingRevokeRequestsLeaveQueueUITests -v 2`
Expected: PASS

- [ ] **Step 5: Run the sidebar-badge suite**

Run: `python manage.py test hr.tests.PendingCountsContextProcessorTests hr.tests.SidebarBadgeMarkupTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/views.py hr/context_processors.py templates/hr/leave_request_list.html hr/tests.py
git commit -m "hr: add Pending Revoke Requests section and sidebar badge to the Leave Approval Queue"
```

---

### Task 14: Team Exceptions — Pending Revoke Requests section + sidebar badge

**Files:**
- Modify: `hr/views.py` (`TeamExceptionsView.get_context_data`, `post()`)
- Modify: `templates/hr/team_exceptions.html` (new card between Pending and History)
- Modify: `hr/context_processors.py:17-36`
- Test: `hr/tests.py`

**Interfaces:**
- Consumes: `decide_attendance_exception_revoke_request` (Task 6).
- Produces: `TeamExceptionsView.post()` handles `action == 'decide_revoke_request'`. `ctx['pending_exception_revoke_requests']`, scoped by the current tab the same way `pending_exceptions`/`decided_exceptions` already are.

- [ ] **Step 1: Write the failing tests**

```python
class PendingRevokeRequestsTeamExceptionsUITests(TestCase):
    def setUp(self):
        self.manager = make_employee(iqama='PRRTE-MGR', name='PRRTE Manager')
        self.manager_user = _login_user('prrte_mgr')
        self.manager.user = self.manager_user
        self.manager.save(update_fields=['user'])
        self.emp = make_employee(iqama='PRRTE-1')
        self.emp.main_manager = self.manager
        self.emp.save(update_fields=['main_manager'])
        self.user = _login_user('prrte_user')
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.exc = _submit_aex(
            employee=self.emp, event_date=_date(2026, 7, 20), event_start_time=_time(9, 0),
            reason_category='site_visit', created_by=self.user)
        decide_attendance_exception(self.exc, self.manager_user, 'approved')
        self.exc.refresh_from_db()
        self.revoke_req = AttendanceExceptionRevokeRequest.objects.create(
            attendance_exception=self.exc, requested_by=self.user, reason='No longer needed.')
        self.client.login(username='prrte_mgr', password='testpass123')

    def test_pending_revoke_request_shows_for_assigned_manager(self):
        resp = self.client.get(reverse('hr:team_exceptions'))
        self.assertContains(resp, 'No longer needed.')
        self.assertEqual(list(resp.context['pending_exception_revoke_requests']), [self.revoke_req])

    def test_approve_applies_the_revoke(self):
        resp = self.client.post(reverse('hr:team_exceptions'), {
            'action': 'decide_revoke_request', 'revoke_request_id': self.revoke_req.pk, 'decision': 'approved',
        })
        self.assertEqual(resp.status_code, 302)
        self.exc.refresh_from_db()
        self.assertEqual(self.exc.status, 'revoked')

    def test_sidebar_badge_includes_pending_revoke_requests(self):
        resp = self.client.get(reverse('hr:hr_dashboard'))
        self.assertEqual(resp.context['team_exceptions_direct_count'], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test hr.tests.PendingRevokeRequestsTeamExceptionsUITests -v 2`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `hr/views.py`, `TeamExceptionsView.get_context_data`, add after the `ctx['can_revoke'] = ...` line added in Task 12:

```python
        ctx['pending_exception_revoke_requests'] = (
            AttendanceExceptionRevokeRequest.objects.filter(
                status='pending', attendance_exception__in=self._tab_queryset(tab, user, emp))
            .select_related('attendance_exception__employee', 'requested_by')
            .order_by('created_at'))
```

Extend `TeamExceptionsView.post()` with a new branch:

```python
        elif action == 'decide_revoke_request':
            from hr.attendance_exception_services import decide_attendance_exception_revoke_request
            revoke_req = AttendanceExceptionRevokeRequest.objects.filter(
                pk=request.POST.get('revoke_request_id')).first()
            if revoke_req is None:
                raise Http404('No such revoke request.')
            try:
                decide_attendance_exception_revoke_request(
                    revoke_req, request.user, request.POST.get('decision'),
                    decision_note=request.POST.get('decision_note', ''))
                messages.success(request, 'Revoke request decided.')
            except ValueError as e:
                messages.error(request, str(e))
```

In `hr/context_processors.py`, `pending_counts`, fold pending revoke-request counts into `direct`/`secondary` (currently lines 17-29):

```python
    if can_view_team_exceptions(user):
        from .models import AttendanceExceptionRevokeRequest
        emp = getattr(user, 'employee_profile', None)
        is_hr = bool(user.is_super_admin_user or user.is_admin_user)
        active_qs = AttendanceException.objects.filter(status__in=('pending', 'expired'))
        revoke_qs = AttendanceExceptionRevokeRequest.objects.filter(status='pending')
        if emp:
            direct = active_qs.filter(employee__main_manager=emp).exclude(employee__user=user)
            secondary = active_qs.filter(employee__secondary_managers=emp).exclude(employee__user=user)
            direct_revokes = revoke_qs.filter(attendance_exception__employee__main_manager=emp)
            secondary_revokes = revoke_qs.filter(attendance_exception__employee__secondary_managers=emp)
        else:
            direct = active_qs.none()
            secondary = active_qs.none()
            direct_revokes = revoke_qs.none()
            secondary_revokes = revoke_qs.none()
        ctx['team_exceptions_direct_count'] = direct.count() + direct_revokes.count()
        ctx['team_exceptions_secondary_count'] = secondary.count() + secondary_revokes.count()
        total = ctx['team_exceptions_direct_count'] + ctx['team_exceptions_secondary_count']
        if is_hr:
            ctx['team_exceptions_all_count'] = active_qs.count() + revoke_qs.count()
            total = ctx['team_exceptions_all_count']
        ctx['team_exceptions_pending_count'] = total
```

In `templates/hr/team_exceptions.html`, add a new card between the Pending card (ending `</div>` at line 225) and the History card (starting line 227):

```html
  {% if pending_exception_revoke_requests %}
  <div class="card mb-4">
    <div class="card-header d-flex justify-content-between align-items-center">
      <h5 class="mb-0"><i class="bi bi-arrow-counterclockwise"></i> Pending Revoke Requests
        <span class="badge bg-warning text-dark ms-2">{{ pending_exception_revoke_requests|length }}</span></h5>
    </div>
    <div class="card-body p-0">
      <div class="table-responsive">
      <table class="table table-sm mb-0">
        <thead class="table-light"><tr><th>Employee</th><th>Event</th><th>Requested By</th><th>Reason</th><th style="min-width:220px;">Decide</th></tr></thead>
        <tbody>
          {% for rr in pending_exception_revoke_requests %}
          <tr>
            <td>{{ rr.attendance_exception.employee.full_name }}</td>
            <td class="small">{{ rr.attendance_exception.event_date }} {{ rr.attendance_exception.event_start_time|time:"H:i" }}</td>
            <td class="small text-muted">{{ rr.requested_by.get_full_name|default:rr.requested_by.username }}</td>
            <td class="small">{{ rr.reason }}</td>
            <td>
              <form method="post" class="d-flex flex-column gap-1">
                {% csrf_token %}
                <input type="hidden" name="action" value="decide_revoke_request">
                <input type="hidden" name="revoke_request_id" value="{{ rr.pk }}">
                <input type="hidden" name="tab" value="{{ tab }}">
                <textarea name="decision_note" class="form-control form-control-sm" rows="1" placeholder="Note (required if rejecting)"></textarea>
                <div class="d-flex gap-1">
                  <button type="submit" name="decision" value="approved" class="btn btn-sm btn-outline-success">Approve Revoke</button>
                  <button type="submit" name="decision" value="rejected" class="btn btn-sm btn-outline-danger">Reject</button>
                </div>
              </form>
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

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test hr.tests.PendingRevokeRequestsTeamExceptionsUITests -v 2`
Expected: PASS

- [ ] **Step 5: Run the sidebar-badge and Team Exceptions suite**

Run: `python manage.py test hr.tests.PendingCountsContextProcessorTests hr.tests.TeamExceptionsTabCountTests hr.tests.TeamExceptionsSidebarLinkTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hr/views.py hr/context_processors.py templates/hr/team_exceptions.html hr/tests.py
git commit -m "hr: add Pending Revoke Requests section and sidebar badge to Team Exceptions"
```

---

### Task 15: Final verification — full suite + updated user report

**Files:**
- None (verification + documentation only)
- Modify: none in `hr/` — this task produces a plain-English summary as a chat deliverable, not a repo file, matching how prior feature reports in this project were delivered (unless the user has since asked for it to be saved as a doc)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Run the full `hr` app suite**

Run: `python manage.py test hr -v 1`
Expected: OK, 0 failures (baseline before this feature: 543 tests, 1 pre-existing unrelated skip).

- [ ] **Step 2: Run the full cross-app suite**

Run: `python manage.py test -v 1`
Expected: OK, with only the same pre-existing, already-confirmed-unrelated failures this project has consistently seen all session (3 `costing`/`projects`/`drafts` static-manifest failures from PR #22, 1 `procurement` Arabic-text-shaping failure) — no NEW failures. If any new failure appears, stop and fix it before proceeding; do not treat it as pre-existing without first re-running that specific test class in isolation against current code (this project's own established lesson from earlier in this session — stale background-run snapshots have produced false alarms before).

- [ ] **Step 3: Manual smoke test via the dev server**

Run: `python manage.py runserver` and walk through, using the `seed_manager_leave_demo` demo accounts (or any account with an approved leave request):
1. As the employee (`demo.report`/`DemoPass123!`): edit a pending request, delete a pending request, request a revoke on an approved one — confirm the "Revoke requested — awaiting review" note appears and the button disappears.
2. As `demo.manager`: confirm Edit/Delete render on their own in-behalf-logged pending request in "My Reporting Structure", and nowhere else.
3. As a Super Admin: decide the pending revoke request from the Leave Approval Queue's new "Pending Revoke Requests" card; confirm the request flips to "Revoked" in History with the reason/who/when shown; confirm a direct "Revoke" on a different approved request works the same way.
4. Repeat 1–3 for Team Exceptions (report an exception, edit/delete it pending, get it approved by the manager, request a revoke, decide it as the manager, and try a direct revoke as Super Admin).
5. Confirm the sidebar "Leave"/attendance-exceptions badge counts go up when a revoke request is pending and down once decided.

- [ ] **Step 4: Deliver the updated report**

Per the user's standing request ("give me the updated report... if I missed something must be added tell me"), write a plain-English summary covering: where Edit/Delete live (My Profile only, gated to the request's own creator, only while pending and undecided), where Request-Revoke lives (My Profile, employee's own approved requests only), where Direct Revoke and revoke-request decisions live (Leave Approval Queue / Team Exceptions, gated to override-access holders — plus the assigned manager for Team Exceptions' revoke-request decisions specifically), and the one explicitly-flagged open question from the design spec (Team Exception revoke-request routing to the assigned manager, not confirmed by the user, easy to change to override-access-only if that's wrong).

---

## Self-Review Notes

**Spec coverage:** every numbered item in `docs/superpowers/specs/2026-08-09-leave-edit-delete-revoke-design.md` maps to a task above — data model changes (Tasks 1–2), edit/delete for both workflows (Tasks 3–4, 7–8), direct revoke for both (Tasks 5–6, 11–12), employee-requested revoke + decision roster for both (Tasks 5–6, 9–10, 13–14), the "revoked shows in both histories" requirement added by the user (explicitly threaded through Tasks 9–12 via badge rendering on both My Profile and the queue pages), sidebar badge counts (Tasks 13–14), and the edge-case table (duplicate pending revoke blocked — tested in Tasks 5–6; non-approved revoke rejected — tested in Tasks 5–6; direct-revoke-races-a-pending-request auto-closes it — implemented in `revoke_leave_request`/`revoke_attendance_exception`, Tasks 5–6; self-decision blocked — tested in Tasks 5–6; deleting a pending request with a document — handled by Django's default `FileField` behavior, no extra code needed, per spec).

**Placeholder scan:** no "TBD"/"handle appropriately" language; every step has real, complete code matching this codebase's actual current signatures (verified by reading the live files, not assumed).

**Type consistency:** `edit_leave_request`/`delete_leave_request` (Task 3) → called identically from `my_profile()` in Task 7. `request_leave_revoke`/`decide_leave_revoke_request`/`revoke_leave_request` (Task 5) → called identically from Tasks 9, 11, 13. Same pattern verified for the Attendance Exception track (Tasks 4, 6 → 8, 10, 12, 14). `can_edit_delete`/`can_request_revoke`/`pending_revoke_request` attribute names introduced in Task 7/9 for Leave and mirrored exactly for Attendance Exceptions in Task 8/10 — no drift.
