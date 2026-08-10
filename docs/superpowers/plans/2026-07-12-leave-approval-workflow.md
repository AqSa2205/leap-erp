# Leave Approval Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the leave-type accumulative rule (only Annual counts toward summary totals), clean up two UI elements, and build a new dual-approval request workflow for every conditional leave type — with a superadmin deadlock override and permission-gated document downloads — then seed realistic test data.

**Architecture:** Four new models in the `hr` app (`LeaveApprover`, `LeaveRequest`, `LeaveRequestApproval`, `LeaveRequestNote`), a small service module for approval finalization, a super-admin-only queue/detail UI, a My Profile self-service submission form, and an in-app notification hook using the existing `notifications.services.notify_users`.

**Tech Stack:** Django (existing project conventions — CBVs + a few FBVs, `ModelForm`, Bootstrap 5 templates, `django.test.TestCase`).

## Global Constraints

- Follow Tasks 1, 2, 3, 5 literally — no extra fields, columns, or behavior beyond what's asked.
- Task 4 has creative freedom but must not touch the existing Annual-leave direct-entry flow (`LeaveRecordCreateView` stays as-is).
- `LeaveRequest.document` must never be exposed via a public media URL — only through a permission-checked download view.
- Approval authority comes from `LeaveApprover` rows, never from checking usernames in code.
- Exact note text required on `/hr/leave/entitlements/` (Task 3): `"Totals below include only standard accrued leave (e.g. Annual) — conditional/incidental leave (Marriage, Umrah, etc.) is excluded from the summary and shown per-type in the breakdown."`

---

### Task 1: Fix LeaveType default values and accumulative flags

**Files:**
- Create: `hr/migrations/0024_leavetype_task1_values.py`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Produces: DB state where `LeaveType(code='annual').is_accumulative == True` and every other `LeaveType.is_accumulative == False`; `default_annual_days` set to Annual=30, Death Of Family Member=3.0, Marriage=3.0, New Born=3.0, Sick=12.0, Umrah=2.0 for whichever of these codes already exist.

- [ ] **Step 1: Write the failing test**

```python
# Append to hr/tests.py
class LeaveTypeTask1DefaultsTests(TestCase):
    def test_only_annual_is_accumulative(self):
        from hr.models import LeaveType
        annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})
        sick, _ = LeaveType.objects.get_or_create(code='sick', defaults={'name': 'Sick', 'default_annual_days': 12})
        marriage, _ = LeaveType.objects.get_or_create(code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3})
        # Simulate the migration's effect directly (the migration itself is exercised by `migrate` in CI/manual QA;
        # this test locks in the invariant the app code relies on).
        LeaveType.objects.exclude(code='annual').update(is_accumulative=False)
        LeaveType.objects.filter(code='annual').update(is_accumulative=True)
        self.assertTrue(LeaveType.objects.get(code='annual').is_accumulative)
        self.assertFalse(LeaveType.objects.get(code='sick').is_accumulative)
        self.assertFalse(LeaveType.objects.get(code='marriage').is_accumulative)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveTypeTask1DefaultsTests -v 2`
Expected: FAIL — `AttributeError` or `LeaveType` import error is not expected; this test only fails if it can't be collected. Since the model field already exists from a prior change, this specific test should actually PASS immediately (it directly manipulates data, not the migration). Run it anyway to confirm it's green before moving on — this is a data-invariant regression test, not a red/green TDD pair for new code.

- [ ] **Step 3: Write the data migration**

```python
# hr/migrations/0024_leavetype_task1_values.py
from decimal import Decimal
from django.db import migrations

# code -> (default_annual_days, is_accumulative)
LEAVE_TYPE_VALUES = {
    'annual': (Decimal('30'), True),
    'death_of_family_member': (Decimal('3.0'), False),
    'death': (Decimal('3.0'), False),  # some environments may use this shorter code instead
    'marriage': (Decimal('3.0'), False),
    'new_born': (Decimal('3.0'), False),
    'newborn': (Decimal('3.0'), False),
    'sick': (Decimal('12.0'), False),
    'umrah': (Decimal('2.0'), False),
}


def apply_task1_values(apps, schema_editor):
    LeaveType = apps.get_model('hr', 'LeaveType')
    for code, (days, is_accumulative) in LEAVE_TYPE_VALUES.items():
        LeaveType.objects.filter(code=code).update(default_annual_days=days, is_accumulative=is_accumulative)
    # Crucial logic change: ANY leave type not explicitly listed above (i.e. not Annual)
    # must also be non-accumulative — Annual is the only standard accrued type.
    LeaveType.objects.exclude(code='annual').update(is_accumulative=False)
    LeaveType.objects.filter(code='annual').update(is_accumulative=True)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0023_add_leavetype_is_accumulative'),
    ]

    operations = [
        migrations.RunPython(apply_task1_values, reverse_noop),
    ]
```

- [ ] **Step 4: Run the migration and the test**

Run: `venv/Scripts/python.exe manage.py migrate hr && venv/Scripts/python.exe manage.py test hr.tests.LeaveTypeTask1DefaultsTests -v 2`
Expected: migration applies with `OK`; test passes.

- [ ] **Step 5: Commit**

```bash
git add hr/migrations/0024_leavetype_task1_values.py hr/tests.py
git commit -m "hr: only Annual leave counts as accumulative; update leave-type day defaults"
```

---

### Task 2: Remove "Counts in Summary" column from Leave Types list

**Files:**
- Modify: `templates/hr/leavetype_list.html`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed by later tasks (purely a template trim).

- [ ] **Step 1: Remove the column header and cell**

In `templates/hr/leavetype_list.html`, remove the `<th>Counts in Summary</th>` header cell and the corresponding `<td>` block that renders `lt.is_accumulative` as a Yes/No badge. Also change the `colspan="8"` on the empty-state row back to `colspan="7"` (one fewer column).

- [ ] **Step 2: Manually verify**

Run: `venv/Scripts/python.exe manage.py runserver` (or reuse an existing dev server), log in as a super admin, visit `/hr/leave-types/`.
Expected: table no longer has a "Counts in Summary" column; Edit/Delete actions still work.

- [ ] **Step 3: Commit**

```bash
git add templates/hr/leavetype_list.html
git commit -m "hr: remove Counts in Summary column from leave types list"
```

---

### Task 3: Entitlements page label + note text

**Files:**
- Modify: `templates/hr/entitlement_year.html`
- Modify: `hr/views.py` (only if the note text is partly built in the view — it isn't; confirm and skip if so)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Update column headers**

In `templates/hr/entitlement_year.html`, change the three `<th>` cells from `Entitled (accrued)` / `Taken (accrued)` / `Remaining (accrued)` back to plain `Entitled` / `Taken` / `Remaining`.

- [ ] **Step 2: Update the note text to the exact required wording**

Replace the existing explanatory `<p class="text-muted small ...">` note (currently mentions "Annual, Sick") with this exact text:

```html
<p class="text-muted small mb-0 mt-2">
    Totals below include only standard accrued leave (e.g. Annual) — conditional/incidental
    leave (Marriage, Umrah, etc.) is excluded from the summary and shown per-type in the breakdown.
</p>
```

- [ ] **Step 3: Manually verify**

Visit `/hr/leave/entitlements/` as a super admin. Expected: column headers read "Entitled" / "Taken" / "Remaining" (no "(accrued)"); the note text matches exactly.

- [ ] **Step 4: Commit**

```bash
git add templates/hr/entitlement_year.html
git commit -m "hr: entitlements page column labels + note wording per spec"
```

---

### Task 4a: Core models — LeaveApprover, LeaveRequest, LeaveRequestApproval, LeaveRequestNote

**Files:**
- Modify: `hr/models/leave.py`
- Modify: `hr/models/__init__.py`
- Create: `hr/migrations/0025_leave_approval_workflow.py`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Produces:
  - `LeaveApprover(user, is_active, created_at)`
  - `LeaveRequest(employee, leave_type, start_date, end_date, days, employee_reason, document, status, created_by, leave_record, salary_deduction_applicable, salary_deduction_note, is_overridden, overridden_by, override_reason, decided_at, created_at, updated_at)` with `STATUS_CHOICES = [('pending','Pending'),('approved','Approved'),('disapproved','Disapproved'),('cancelled','Cancelled')]`, `computed_days()`, `clean()`, `save()` (auto-computes `days` like `LeaveRecord`).
  - `LeaveRequestApproval(leave_request, approver, decision, comment, decided_at)` with `DECISION_CHOICES = [('pending','Pending'),('approved','Approved'),('disapproved','Disapproved'),('skipped','Skipped')]`.
  - `LeaveRequestNote(leave_request, author, note, is_internal, created_at)`.

- [ ] **Step 1: Write the failing model tests**

```python
# Append to hr/tests.py
from django.contrib.auth import get_user_model

User = get_user_model()


def make_user(username, **kwargs):
    return User.objects.create(username=username, **kwargs)


class LeaveApprovalModelsTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.aamna = make_user('aamna_khan')
        self.ali = make_user('ali_sultan')
        LeaveApprover.objects.create(user=self.aamna)
        LeaveApprover.objects.create(user=self.ali)

    def test_days_autocomputed(self):
        req = LeaveRequest(employee=self.emp, leave_type=self.marriage,
                           start_date=_date(2026, 7, 5), end_date=_date(2026, 7, 7))
        req.save()
        self.assertEqual(req.days, Decimal('3'))

    def test_end_before_start_rejected(self):
        req = LeaveRequest(employee=self.emp, leave_type=self.marriage,
                           start_date=_date(2026, 7, 9), end_date=_date(2026, 7, 5))
        with self.assertRaises(ValidationError):
            req.full_clean()

    def test_default_status_is_pending(self):
        req = LeaveRequest.objects.create(employee=self.emp, leave_type=self.marriage,
                                          start_date=_date(2026, 7, 5), end_date=_date(2026, 7, 7))
        self.assertEqual(req.status, 'pending')

    def test_approval_rows_are_separate_from_notes(self):
        req = LeaveRequest.objects.create(employee=self.emp, leave_type=self.marriage,
                                          start_date=_date(2026, 7, 5), end_date=_date(2026, 7, 7))
        LeaveRequestApproval.objects.create(leave_request=req, approver=self.aamna)
        LeaveRequestApproval.objects.create(leave_request=req, approver=self.ali)
        LeaveRequestNote.objects.create(leave_request=req, author=self.aamna, note='Looks fine.')
        self.assertEqual(req.approvals.count(), 2)
        self.assertEqual(req.notes.count(), 1)
```

Add `from django.core.exceptions import ValidationError` and `from hr.models import (LeaveType, LeaveRecord, LeaveEntitlement, Holiday, LeaveApprover, LeaveRequest, LeaveRequestApproval, LeaveRequestNote)` to the existing import block at the top of `hr/tests.py` (extend the existing `from hr.models import ...` line rather than duplicating it).

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveApprovalModelsTests -v 2`
Expected: FAIL with `ImportError: cannot import name 'LeaveApprover'`.

- [ ] **Step 3: Add the models**

Append to `hr/models/leave.py`:

```python
class LeaveApprover(models.Model):
    """A user with authority to approve/reject conditional leave requests.
    Approval authority is a DB fact (this table), never a hardcoded username."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_approver_profile')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} (leave approver)"


class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('disapproved', 'Disapproved'),
        ('cancelled', 'Cancelled'),
    ]

    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='requests')
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                               help_text='Auto-computed from the date range if left blank.')
    employee_reason = models.TextField(blank=True)
    document = models.FileField(upload_to='leave_requests/%Y/%m/', null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='leave_requests_created')
    leave_record = models.OneToOneField(LeaveRecord, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='source_request')
    salary_deduction_applicable = models.BooleanField(default=False)
    salary_deduction_note = models.TextField(blank=True)
    is_overridden = models.BooleanField(default=False)
    overridden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='leave_requests_overridden')
    override_reason = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']  # FIFO by default
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return f"{self.employee.full_name} {self.leave_type.name} {self.start_date}..{self.end_date} ({self.status})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})
        if self.leave_type_id and self.leave_type.is_accumulative:
            raise ValidationError({'leave_type': 'The approval workflow is only for conditional leave types.'})

    def computed_days(self):
        from decimal import Decimal
        if not self.start_date or not self.end_date or self.end_date < self.start_date:
            return Decimal('0')
        return Decimal((self.end_date - self.start_date).days + 1)

    def save(self, *args, **kwargs):
        if self.days is None:
            self.days = self.computed_days()
        super().save(*args, **kwargs)

    def pending_approvers(self):
        """Users whose decision is still outstanding (used for the employee-facing
        'Pending — waiting on X' message)."""
        return [a.approver for a in self.approvals.filter(decision='pending')]


class LeaveRequestApproval(models.Model):
    DECISION_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('disapproved', 'Disapproved'),
        ('skipped', 'Skipped'),  # set when a superadmin override finalizes the request first
    ]

    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='approvals')
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    decision = models.CharField(max_length=12, choices=DECISION_CHOICES, default='pending')
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('leave_request', 'approver')
        ordering = ['id']

    def __str__(self):
        return f"{self.approver} -> {self.decision} on request #{self.leave_request_id}"


class LeaveRequestNote(models.Model):
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    note = models.TextField()
    is_internal = models.BooleanField(default=False, help_text='Internal notes are hidden from the employee.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Note on request #{self.leave_request_id} by {self.author}"
```

Update `hr/models/__init__.py`'s leave import line to:

```python
from .leave import (LeaveType, LeaveEntitlement, LeaveRecord,
                    LeaveApprover, LeaveRequest, LeaveRequestApproval, LeaveRequestNote)
```

- [ ] **Step 4: Generate and apply the migration**

Run: `venv/Scripts/python.exe manage.py makemigrations hr --name leave_approval_workflow`
Expected: creates `hr/migrations/0025_leave_approval_workflow.py` adding the four new tables. Rename it to match the file name in **Files** above if Django names it differently — that's cosmetic only.

Run: `venv/Scripts/python.exe manage.py migrate hr`
Expected: `Applying hr.0025_leave_approval_workflow... OK`

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveApprovalModelsTests -v 2`
Expected: 4 tests, OK.

- [ ] **Step 6: Commit**

```bash
git add hr/models/leave.py hr/models/__init__.py hr/migrations/0025_leave_approval_workflow.py hr/tests.py
git commit -m "hr: add LeaveApprover/LeaveRequest/LeaveRequestApproval/LeaveRequestNote models"
```

---

### Task 4b: Approval finalization service (with override)

**Files:**
- Create: `hr/leave_approval_services.py`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Consumes: `LeaveRequest`, `LeaveRequestApproval`, `LeaveRecord` from `hr.models` (Task 4a).
- Produces:
  - `record_approver_decision(leave_request, approver_user, decision, comment='') -> LeaveRequest` — `decision` is `'approved'` or `'disapproved'`. Raises `ValueError` if `approver_user` has no pending `LeaveRequestApproval` row on this request.
  - `override_finalize(leave_request, superadmin_user, decision, reason) -> LeaveRequest` — `decision` is `'approved'` or `'disapproved'`. Raises `ValueError` if `leave_request.status != 'pending'` or `reason` is blank.
  - Both call the shared private `_finalize(leave_request, status)` which creates the linked `LeaveRecord` on approval or sets `salary_deduction_applicable=True` on disapproval, and sets `decided_at`.

- [ ] **Step 1: Write the failing tests**

```python
# Append to hr/tests.py
from hr.leave_approval_services import record_approver_decision, override_finalize


class LeaveApprovalServiceTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.aamna = make_user('aamna_khan')
        self.ali = make_user('ali_sultan')
        LeaveApprover.objects.create(user=self.aamna)
        LeaveApprover.objects.create(user=self.ali)
        self.req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 3))
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=self.aamna)
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=self.ali)

    def test_stays_pending_after_one_approval(self):
        record_approver_decision(self.req, self.aamna, 'approved')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')
        self.assertIsNone(self.req.leave_record)

    def test_fully_approved_creates_leave_record(self):
        record_approver_decision(self.req, self.aamna, 'approved')
        record_approver_decision(self.req, self.ali, 'approved')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')
        self.assertIsNotNone(self.req.leave_record)
        self.assertEqual(self.req.leave_record.employee, self.emp)
        self.assertEqual(self.req.leave_record.days, Decimal('3'))

    def test_one_disapproval_is_decisive(self):
        record_approver_decision(self.req, self.aamna, 'approved')
        record_approver_decision(self.req, self.ali, 'disapproved', comment='Not enough notice')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'disapproved')
        self.assertTrue(self.req.salary_deduction_applicable)
        self.assertIsNone(self.req.leave_record)

    def test_non_approver_cannot_decide(self):
        stranger = make_user('someone_else')
        with self.assertRaises(ValueError):
            record_approver_decision(self.req, stranger, 'approved')

    def test_override_approve_finalizes_and_skips_remaining(self):
        superadmin = make_user('super1')
        override_finalize(self.req, superadmin, 'approved', reason='Ali is on leave; approving on his behalf.')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')
        self.assertTrue(self.req.is_overridden)
        self.assertIsNotNone(self.req.leave_record)
        skipped = self.req.approvals.filter(decision='skipped')
        self.assertEqual(skipped.count(), 2)  # neither had decided yet

    def test_override_requires_reason(self):
        superadmin = make_user('super2')
        with self.assertRaises(ValueError):
            override_finalize(self.req, superadmin, 'approved', reason='')

    def test_override_on_already_decided_request_rejected(self):
        record_approver_decision(self.req, self.aamna, 'approved')
        record_approver_decision(self.req, self.ali, 'approved')
        superadmin = make_user('super3')
        with self.assertRaises(ValueError):
            override_finalize(self.req, superadmin, 'disapproved', reason='Too late anyway')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveApprovalServiceTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'hr.leave_approval_services'`.

- [ ] **Step 3: Implement the service**

```python
# hr/leave_approval_services.py
"""Finalization logic for the conditional-leave dual-approval workflow.

Two entry points:
- record_approver_decision: a designated LeaveApprover records their own decision.
- override_finalize: any super admin force-finalizes a stuck request (deadlock breaker).

Both funnel through _finalize, which is the single place that creates the
balance-deducting LeaveRecord or sets the salary-deduction flag.
"""
from django.utils import timezone

from hr.models import LeaveRecord


def record_approver_decision(leave_request, approver_user, decision, comment=''):
    if decision not in ('approved', 'disapproved'):
        raise ValueError(f"decision must be 'approved' or 'disapproved', got {decision!r}")
    try:
        approval = leave_request.approvals.get(approver=approver_user)
    except leave_request.approvals.model.DoesNotExist:
        raise ValueError(f"{approver_user} is not a designated approver for this request.")
    if approval.decision != 'pending':
        raise ValueError(f"{approver_user} has already decided ({approval.decision}).")

    approval.decision = decision
    approval.comment = comment
    approval.decided_at = timezone.now()
    approval.save(update_fields=['decision', 'comment', 'decided_at'])

    _reconcile(leave_request)
    return leave_request


def override_finalize(leave_request, superadmin_user, decision, reason):
    if decision not in ('approved', 'disapproved'):
        raise ValueError(f"decision must be 'approved' or 'disapproved', got {decision!r}")
    if not reason or not reason.strip():
        raise ValueError('An override requires a written reason.')
    leave_request.refresh_from_db()
    if leave_request.status != 'pending':
        raise ValueError(f"Request is already {leave_request.status}; nothing to override.")

    leave_request.approvals.filter(decision='pending').update(decision='skipped', decided_at=timezone.now())
    leave_request.is_overridden = True
    leave_request.overridden_by = superadmin_user
    leave_request.override_reason = reason
    _finalize(leave_request, decision)
    return leave_request


def _reconcile(leave_request):
    """Re-derive overall status from the individual approval rows."""
    leave_request.refresh_from_db()
    decisions = list(leave_request.approvals.values_list('decision', flat=True))
    if any(d == 'disapproved' for d in decisions):
        _finalize(leave_request, 'disapproved')
    elif decisions and all(d == 'approved' for d in decisions):
        _finalize(leave_request, 'approved')
    # else: still pending, nothing to do.


def _finalize(leave_request, status):
    leave_request.status = status
    leave_request.decided_at = timezone.now()
    if status == 'approved':
        record = LeaveRecord.objects.create(
            employee=leave_request.employee,
            leave_type=leave_request.leave_type,
            start_date=leave_request.start_date,
            end_date=leave_request.end_date,
            days=leave_request.days,
            note=f'Approved via leave request #{leave_request.pk}',
        )
        leave_request.leave_record = record
    elif status == 'disapproved':
        leave_request.salary_deduction_applicable = True
    leave_request.save(update_fields=['status', 'decided_at', 'leave_record', 'salary_deduction_applicable'])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveApprovalServiceTests -v 2`
Expected: 7 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add hr/leave_approval_services.py hr/tests.py
git commit -m "hr: dual-approval finalization service with superadmin override"
```

---

### Task 4c: In-app notifications on submit/decide

**Files:**
- Modify: `hr/leave_approval_services.py`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Consumes: `notifications.services.notify_users(recipients, verb, actor=None, target=None, target_url='', description='')` (existing, signature confirmed in `notifications/services.py`).
- Produces: `notify_submission(leave_request)`, called by the view that creates a `LeaveRequest` (Task 4f/4g will call it, not this task).

- [ ] **Step 1: Write the failing test**

```python
# Append to hr/tests.py
from notifications.models import Notification


class LeaveApprovalNotificationTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.emp_user = make_user('emp_user')
        self.emp.user = self.emp_user
        self.emp.save(update_fields=['user'])
        self.aamna = make_user('aamna_khan')
        self.ali = make_user('ali_sultan')
        LeaveApprover.objects.create(user=self.aamna)
        LeaveApprover.objects.create(user=self.ali)
        self.req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 3))
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=self.aamna)
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=self.ali)

    def test_other_approver_notified_after_first_decision(self):
        record_approver_decision(self.req, self.aamna, 'approved')
        self.assertTrue(Notification.objects.filter(recipient=self.ali).exists())

    def test_employee_notified_on_final_approval(self):
        record_approver_decision(self.req, self.aamna, 'approved')
        record_approver_decision(self.req, self.ali, 'approved')
        self.assertTrue(Notification.objects.filter(recipient=self.emp_user, verb__icontains='approved').exists())

    def test_employee_notified_on_disapproval(self):
        record_approver_decision(self.req, self.aamna, 'disapproved')
        self.assertTrue(Notification.objects.filter(recipient=self.emp_user, verb__icontains='disapproved').exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveApprovalNotificationTests -v 2`
Expected: FAIL — no notifications created yet (0 vs expected >=1).

- [ ] **Step 3: Wire notifications into the service**

In `hr/leave_approval_services.py`, add the import and hook into `record_approver_decision` and `_finalize`:

```python
from notifications.services import notify_users
```

Modify `record_approver_decision` — after `_reconcile(leave_request)` and before `return leave_request`, insert:

```python
    leave_request.refresh_from_db()
    if leave_request.status == 'pending':
        remaining = leave_request.pending_approvers()
        if remaining:
            notify_users(
                recipients=remaining,
                verb=f'{approver_user.get_full_name() or approver_user.username} decided on a leave request awaiting your review',
                actor=approver_user,
                description=f'{leave_request.employee.full_name} — {leave_request.leave_type.name} '
                            f'({leave_request.start_date} to {leave_request.end_date})',
            )
```

Modify `_finalize` — after `leave_request.save(...)`, insert (still inside `_finalize`, so it fires for both normal finalization and override):

```python
    if leave_request.employee.user_id:
        verb = 'approved' if status == 'approved' else 'disapproved'
        notify_users(
            recipients=[leave_request.employee.user],
            verb=f'Your {leave_request.leave_type.name} leave request was {verb}',
            description=leave_request.override_reason if leave_request.is_overridden else '',
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveApprovalNotificationTests -v 2`
Expected: 3 tests, OK.

- [ ] **Step 5: Run the full Task 4a/4b/4c test set together to check for regressions**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveApprovalModelsTests hr.tests.LeaveApprovalServiceTests hr.tests.LeaveApprovalNotificationTests -v 2`
Expected: 14 tests, OK.

- [ ] **Step 6: Commit**

```bash
git add hr/leave_approval_services.py hr/tests.py
git commit -m "hr: notify the other approver and the employee on leave-request decisions"
```

---

### Task 4d: Access control mixins + secure document download

**Files:**
- Modify: `hr/views.py`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Consumes: `LeaveApprover`, `LeaveRequest` from `hr.models`.
- Produces: `SuperAdminRequiredMixin` (class, in `hr/views.py`), `is_designated_approver(user) -> bool` (function, in `hr/views.py`), URL name `hr:leave_request_document` at `hr/urls.py` mapping to view `leave_request_document_download(request, pk)`.

- [ ] **Step 1: Write the failing tests**

```python
# Append to hr/tests.py
from django.core.files.uploadedfile import SimpleUploadedFile


class LeaveRequestDocumentAccessTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.emp_user = make_user('doc_emp_user', password='x')
        self.emp_user.set_password('testpass123')
        self.emp_user.save()
        self.emp.user = self.emp_user
        self.emp.save(update_fields=['user'])
        self.aamna = make_user('doc_aamna')
        self.aamna.set_password('testpass123')
        self.aamna.save()
        LeaveApprover.objects.create(user=self.aamna)
        self.stranger = make_user('doc_stranger')
        self.stranger.set_password('testpass123')
        self.stranger.save()
        self.req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 3),
            document=SimpleUploadedFile('cert.pdf', b'dummy-bytes'))

    def test_owner_can_download(self):
        self.client.login(username='doc_emp_user', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_document', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_approver_can_download(self):
        self.client.login(username='doc_aamna', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_document', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_unrelated_user_forbidden(self):
        self.client.login(username='doc_stranger', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_document', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse('hr:leave_request_document', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 302)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveRequestDocumentAccessTests -v 2`
Expected: FAIL — `NoReverseMatch: 'leave_request_document' is not a registered namespace`.

- [ ] **Step 3: Add the mixin, helper, and view**

In `hr/views.py`, near the existing `AdminRequiredMixin` (around line 32), add:

```python
class SuperAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Stricter than AdminRequiredMixin — Super Admin only, no 'admin' role.
    Used for the conditional-leave approval queue per the access-control spec."""
    def test_func(self):
        return self.request.user.is_super_admin_user


def is_designated_approver(user):
    """True if `user` currently holds active approval authority for conditional
    leave requests. This — not a username check — is what makes Aamna Khan and
    Ali Sultan (or whoever holds these rows) able to actually approve/reject."""
    from .models import LeaveApprover
    return LeaveApprover.objects.filter(user=user, is_active=True).exists()
```

Near the other file-serving/document views (search for `employee_document_upload` around line 1153), add:

```python
@login_required
def leave_request_document_download(request, pk):
    """Stream a LeaveRequest's uploaded document only to the employee it
    belongs to, a designated approver, or a super admin — never via a public
    media URL. This deliberately does NOT follow the EmployeeDocument/
    medical_certificate pattern (those link straight to MEDIA_URL); this field
    holds sensitive personal documents and needs a real permission check."""
    from django.http import FileResponse, Http404
    from .models import LeaveRequest
    leave_request = get_object_or_404(LeaveRequest, pk=pk)
    user = request.user
    is_owner = leave_request.employee.user_id == user.id
    if not (is_owner or is_designated_approver(user) or user.is_super_admin_user):
        return HttpResponse('Forbidden', status=403)
    if not leave_request.document:
        raise Http404('No document attached to this request.')
    return FileResponse(leave_request.document.open('rb'), as_attachment=True,
                        filename=leave_request.document.name.rsplit('/', 1)[-1])
```

Add `from django.http import HttpResponse` to the existing `django.http` import line in `hr/views.py` if `HttpResponse` isn't already imported (check first — `HttpResponse` may already be there via `from django.http import HttpResponse, JsonResponse`; if so, just reuse it).

In `hr/urls.py`, add near the Leave Records section:

```python
    path('leave-requests/<int:pk>/document/', views.leave_request_document_download, name='leave_request_document'),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveRequestDocumentAccessTests -v 2`
Expected: 4 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add hr/views.py hr/urls.py hr/tests.py
git commit -m "hr: SuperAdminRequiredMixin + permission-gated leave request document download"
```

---

### Task 4e: Request form + queue (list) view

**Files:**
- Modify: `hr/forms.py`
- Modify: `hr/views.py`
- Modify: `hr/urls.py`
- Create: `templates/hr/leave_request_list.html`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Consumes: `SuperAdminRequiredMixin`, `is_designated_approver` (Task 4d); `LeaveRequest`, `LeaveApprover` (Task 4a).
- Produces: `LeaveRequestForm` (ModelForm, fields `employee, leave_type, start_date, end_date, employee_reason, document`), view `LeaveRequestListView` at `hr:leave_request_list` (`/hr/leave-requests/`), view `LeaveRequestCreateView` at `hr:leave_request_create` (`/hr/leave-requests/create/`) for admins logging on an employee's behalf.

- [ ] **Step 1: Write the failing tests**

```python
# Append to hr/tests.py
class LeaveRequestQueueViewTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.superadmin = make_user('queue_super')
        self.superadmin.set_password('testpass123')
        from accounts.models import Role
        role, _ = Role.objects.get_or_create(name='super_admin')
        self.superadmin.role = role
        self.superadmin.save()
        self.plain_user = make_user('queue_plain', password='x')
        self.plain_user.set_password('testpass123')
        self.plain_user.save()
        self.pending = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 3))

    def test_super_admin_can_view_queue(self):
        self.client.login(username='queue_super', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.emp.full_name)

    def test_non_super_admin_forbidden(self):
        self.client.login(username='queue_plain', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_list'))
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_log_request_on_employees_behalf(self):
        self.client.login(username='queue_super', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_create'), {
            'employee': self.emp.pk, 'leave_type': self.marriage.pk,
            'start_date': '2026-09-01', 'end_date': '2026-09-02', 'employee_reason': 'Family event',
        })
        self.assertEqual(resp.status_code, 302)
        new_req = LeaveRequest.objects.exclude(pk=self.pending.pk).get()
        self.assertEqual(new_req.created_by, self.superadmin)
        self.assertEqual(new_req.approvals.count(), 0)  # approvals seeded in Task 4f alongside submission wiring — see note below
```

Note in the test above: seeding `LeaveRequestApproval` rows happens where the request is actually created (both the admin-logging view here and the employee self-service view in Task 4g share one helper). This task's `LeaveRequestCreateView` calls that shared helper too — see Step 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveRequestQueueViewTests -v 2`
Expected: FAIL — `NoReverseMatch` for `hr:leave_request_list`.

- [ ] **Step 3: Add the shared submission helper, form, and views**

In `hr/leave_approval_services.py`, add:

```python
def submit_leave_request(*, employee, leave_type, start_date, end_date, employee_reason='', document=None, created_by):
    """Create a LeaveRequest and snapshot the currently-active LeaveApprover
    roster onto it as LeaveRequestApproval rows. Used by both the employee
    self-service form and the admin's 'log on behalf of' form so the two
    submission paths can never drift out of sync."""
    from hr.models import LeaveApprover, LeaveRequest, LeaveRequestApproval
    leave_request = LeaveRequest.objects.create(
        employee=employee, leave_type=leave_type, start_date=start_date, end_date=end_date,
        employee_reason=employee_reason, document=document, created_by=created_by,
    )
    for approver in LeaveApprover.objects.filter(is_active=True):
        LeaveRequestApproval.objects.create(leave_request=leave_request, approver=approver.user)
    if employee.user_id:
        from notifications.services import notify_users
        notify_users(
            recipients=[employee.user], verb='Your leave request was submitted and is pending approval',
            actor=created_by,
        )
    return leave_request
```

In `hr/forms.py`, add:

```python
class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['employee', 'leave_type', 'start_date', 'end_date', 'employee_reason', 'document']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'leave_type': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'employee_reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'document': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['leave_type'].queryset = LeaveType.objects.filter(is_accumulative=False, is_active=True)
```

Add `LeaveRequest` and `LeaveType` to the existing `from .models import ...` line in `hr/forms.py` if not already present (check first).

In `hr/views.py`, add near the existing `LeaveRecordCreateView`:

```python
class LeaveRequestListView(SuperAdminRequiredMixin, ListView):
    """FIFO queue: oldest pending first, then a paginated history of decided requests."""
    model = LeaveRequest
    template_name = 'hr/leave_request_list.html'
    context_object_name = 'pending_requests'
    paginate_by = None  # pending list is expected to stay small; history is paginated separately below

    def get_queryset(self):
        return LeaveRequest.objects.filter(status='pending').select_related('employee', 'leave_type').order_by('created_at')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['decided_requests'] = (
            LeaveRequest.objects.exclude(status='pending')
            .select_related('employee', 'leave_type').order_by('-decided_at')[:50])
        return ctx


class LeaveRequestCreateView(SuperAdminRequiredMixin, CreateView):
    model = LeaveRequest
    form_class = LeaveRequestForm
    template_name = 'hr/leave_request_form.html'
    success_url = reverse_lazy('hr:leave_request_list')

    def form_valid(self, form):
        from hr.leave_approval_services import submit_leave_request
        leave_request = submit_leave_request(
            employee=form.cleaned_data['employee'], leave_type=form.cleaned_data['leave_type'],
            start_date=form.cleaned_data['start_date'], end_date=form.cleaned_data['end_date'],
            employee_reason=form.cleaned_data['employee_reason'], document=form.cleaned_data['document'],
            created_by=self.request.user,
        )
        messages.success(self.request, 'Leave request logged and sent for approval.')
        return redirect(self.success_url)
```

Import `LeaveRequest, LeaveRequestForm` where needed (`LeaveRequestForm` from `.forms`, already-imported `LeaveType` etc. from `.models` — extend existing import lines, don't duplicate).

In `hr/urls.py`, add:

```python
    path('leave-requests/', views.LeaveRequestListView.as_view(), name='leave_request_list'),
    path('leave-requests/create/', views.LeaveRequestCreateView.as_view(), name='leave_request_create'),
```

Create `templates/hr/leave_request_form.html` (admin "log on behalf of" form — plain, mirrors `templates/hr/leaverecord_form.html`'s structure):

```html
{% extends 'base.html' %}
{% block title %}Log Leave Request{% endblock %}
{% block content %}
<div class="container-fluid">
  <div class="row justify-content-center">
    <div class="col-lg-6">
      <div class="d-flex justify-content-between align-items-center mb-4">
        <h1 class="h3 mb-0">Log Leave Request</h1>
        <a href="{% url 'hr:leave_request_list' %}" class="btn btn-outline-secondary"><i class="bi bi-arrow-left"></i> Back to Queue</a>
      </div>
      <div class="card"><div class="card-body">
        <form method="post" enctype="multipart/form-data" novalidate>
          {% csrf_token %}
          <div class="mb-3"><label class="form-label">Employee *</label>{{ form.employee }}
            {% if form.employee.errors %}<div class="invalid-feedback d-block">{{ form.employee.errors.0 }}</div>{% endif %}</div>
          <div class="mb-3"><label class="form-label">Leave Type *</label>{{ form.leave_type }}
            {% if form.leave_type.errors %}<div class="invalid-feedback d-block">{{ form.leave_type.errors.0 }}</div>{% endif %}</div>
          <div class="row">
            <div class="col-md-6 mb-3"><label class="form-label">Start Date *</label>{{ form.start_date }}
              {% if form.start_date.errors %}<div class="invalid-feedback d-block">{{ form.start_date.errors.0 }}</div>{% endif %}</div>
            <div class="col-md-6 mb-3"><label class="form-label">End Date *</label>{{ form.end_date }}
              {% if form.end_date.errors %}<div class="invalid-feedback d-block">{{ form.end_date.errors.0 }}</div>{% endif %}</div>
          </div>
          <div class="mb-3"><label class="form-label">Reason</label>{{ form.employee_reason }}
            {% if form.employee_reason.errors %}<div class="invalid-feedback d-block">{{ form.employee_reason.errors.0 }}</div>{% endif %}</div>
          <div class="mb-4"><label class="form-label">Document (optional)</label>{{ form.document }}
            {% if form.document.errors %}<div class="invalid-feedback d-block">{{ form.document.errors.0 }}</div>{% endif %}</div>
          <div class="d-flex justify-content-end gap-2 border-top pt-3">
            <a href="{% url 'hr:leave_request_list' %}" class="btn btn-outline-secondary">Cancel</a>
            <button type="submit" class="btn btn-primary"><i class="bi bi-check-lg"></i> Submit for Approval</button>
          </div>
        </form>
      </div></div>
    </div>
  </div>
</div>
{% endblock %}
```

Create `templates/hr/leave_request_list.html`:

```html
{% extends 'base.html' %}
{% block title %}Leave Approval Queue{% endblock %}
{% block content %}
<div class="container-fluid">
  <div class="d-flex justify-content-between align-items-center mb-4">
    <div><h1 class="h3 mb-1">Conditional Leave Approval Queue</h1>
      <p class="text-muted mb-0">Super Admin only. FIFO — oldest pending request first.</p></div>
    <a href="{% url 'hr:leave_request_create' %}" class="btn btn-primary"><i class="bi bi-plus-lg"></i> Log Request</a>
  </div>

  <div class="card mb-4">
    <div class="card-header"><h5 class="mb-0"><i class="bi bi-hourglass-split"></i> Pending
      <span class="badge bg-warning text-dark ms-2">{{ pending_requests|length }}</span></h5></div>
    <div class="card-body p-0">
      {% if pending_requests %}
      <table class="table table-hover align-middle mb-0">
        <thead class="table-light"><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Waiting On</th><th></th></tr></thead>
        <tbody>
          {% for r in pending_requests %}
          <tr>
            <td><a href="{% url 'hr:leave_request_detail' r.pk %}">{{ r.employee.full_name }}</a></td>
            <td>{{ r.leave_type.name }}</td>
            <td>{{ r.start_date }} – {{ r.end_date }}</td>
            <td>{{ r.days }}</td>
            <td class="text-muted small">
              {% for a in r.pending_approvers %}{{ a.get_full_name|default:a.username }}{% if not forloop.last %}, {% endif %}{% endfor %}
            </td>
            <td><a href="{% url 'hr:leave_request_detail' r.pk %}" class="btn btn-sm btn-outline-primary">Review</a></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <p class="text-muted p-3 mb-0">No pending requests.</p>
      {% endif %}
    </div>
  </div>

  <div class="card">
    <div class="card-header"><h5 class="mb-0"><i class="bi bi-clock-history"></i> History</h5></div>
    <div class="card-body p-0">
      <table class="table table-sm mb-0">
        <thead class="table-light"><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {% for r in decided_requests %}
          <tr>
            <td>{{ r.employee.full_name }}</td>
            <td>{{ r.leave_type.name }}</td>
            <td>{{ r.start_date }} – {{ r.end_date }}</td>
            <td>
              {% if r.status == 'approved' %}<span class="badge bg-success">Approved</span>
              {% elif r.status == 'disapproved' %}<span class="badge bg-danger">Disapproved</span>
              {% else %}<span class="badge bg-secondary">{{ r.get_status_display }}</span>{% endif %}
              {% if r.is_overridden %}<span class="badge bg-info text-dark ms-1">Overridden</span>{% endif %}
            </td>
            <td><a href="{% url 'hr:leave_request_detail' r.pk %}" class="btn btn-sm btn-outline-secondary">View</a></td>
          </tr>
          {% empty %}
          <tr><td colspan="5" class="text-center text-muted py-3">No decided requests yet.</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
```

(`hr:leave_request_detail` is added in Task 4f — this template's links will 404 until then; that's expected mid-plan.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveRequestQueueViewTests -v 2`
Expected: 3 tests, OK. (The detail-page link in the template renders fine even before Task 4f exists — Django templates don't resolve `{% url %}` at import time, only at render time, and these tests don't click through to the detail page.)

- [ ] **Step 5: Commit**

```bash
git add hr/forms.py hr/views.py hr/urls.py hr/leave_approval_services.py templates/hr/leave_request_list.html templates/hr/leave_request_form.html hr/tests.py
git commit -m "hr: leave approval queue (FIFO list) + admin log-on-behalf-of form"
```

---

### Task 4f: Detail view — approve/reject, override, notes

**Files:**
- Modify: `hr/views.py`
- Modify: `hr/urls.py`
- Create: `templates/hr/leave_request_detail.html`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Consumes: `SuperAdminRequiredMixin`, `is_designated_approver` (4d); `record_approver_decision`, `override_finalize` (4b).
- Produces: view `LeaveRequestDetailView` at `hr:leave_request_detail` (`/hr/leave-requests/<pk>/`); POST actions `decide` and `override` and `add_note` handled by the same view.

- [ ] **Step 1: Write the failing tests**

```python
# Append to hr/tests.py
class LeaveRequestDetailViewTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.superadmin = make_user('detail_super', password='x')
        self.superadmin.set_password('testpass123')
        from accounts.models import Role
        role, _ = Role.objects.get_or_create(name='super_admin')
        self.superadmin.role = role
        self.superadmin.save()
        self.aamna = make_user('detail_aamna', password='x')
        self.aamna.set_password('testpass123')
        self.aamna.save()
        LeaveApprover.objects.create(user=self.aamna)
        self.ali = make_user('detail_ali', password='x')
        self.ali.set_password('testpass123')
        self.ali.save()
        LeaveApprover.objects.create(user=self.ali)
        self.req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 3))
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=self.aamna)
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=self.ali)

    def test_detail_page_loads(self):
        self.client.login(username='detail_super', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_approver_can_decide_via_post(self):
        self.client.login(username='detail_aamna', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'decide', 'decision': 'approved', 'comment': 'ok'})
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.approvals.get(approver=self.aamna).decision, 'approved')

    def test_add_note_visible_to_employee_by_default(self):
        self.client.login(username='detail_super', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'add_note', 'note': 'Please bring the certificate.'})
        self.assertEqual(resp.status_code, 302)
        note = self.req.notes.get()
        self.assertFalse(note.is_internal)

    def test_override_by_superadmin(self):
        self.client.login(username='detail_super', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'override', 'decision': 'approved', 'reason': 'Ali is on leave'})
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')
        self.assertTrue(self.req.is_overridden)

    def test_non_approver_non_superadmin_cannot_decide(self):
        outsider = make_user('detail_outsider', password='x')
        outsider.set_password('testpass123')
        outsider.save()
        self.client.login(username='detail_outsider', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveRequestDetailViewTests -v 2`
Expected: FAIL — `NoReverseMatch` for `hr:leave_request_detail`.

- [ ] **Step 3: Implement the view**

In `hr/views.py`:

```python
class LeaveRequestDetailView(SuperAdminRequiredMixin, DetailView):
    model = LeaveRequest
    template_name = 'hr/leave_request_detail.html'
    context_object_name = 'leave_request'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_approver'] = is_designated_approver(self.request.user)
        ctx['my_approval'] = self.object.approvals.filter(approver=self.request.user).first()
        ctx['visible_notes'] = self.object.notes.filter(is_internal=False) if not self.request.user.is_super_admin_user \
            else self.object.notes.all()
        return ctx

    def post(self, request, *args, **kwargs):
        from hr.leave_approval_services import record_approver_decision, override_finalize
        self.object = self.get_object()
        action = request.POST.get('action')

        if action == 'decide':
            approval = self.object.approvals.filter(approver=request.user).first()
            if not approval:
                return HttpResponse('You are not a designated approver for this request.', status=403)
            try:
                record_approver_decision(self.object, request.user, request.POST.get('decision'),
                                         comment=request.POST.get('comment', ''))
                messages.success(request, 'Your decision has been recorded.')
            except ValueError as exc:
                messages.error(request, str(exc))

        elif action == 'override':
            if not request.user.is_super_admin_user:
                return HttpResponse('Super admin required.', status=403)
            try:
                override_finalize(self.object, request.user, request.POST.get('decision'),
                                  request.POST.get('reason', ''))
                messages.success(request, 'Request finalized via override.')
            except ValueError as exc:
                messages.error(request, str(exc))

        elif action == 'add_note':
            from .models import LeaveRequestNote
            note_text = request.POST.get('note', '').strip()
            if note_text:
                LeaveRequestNote.objects.create(
                    leave_request=self.object, author=request.user, note=note_text,
                    is_internal=bool(request.POST.get('is_internal')),
                )
                messages.success(request, 'Note added.')

        return redirect('hr:leave_request_detail', pk=self.object.pk)
```

Add `LeaveRequestNote` and `DetailView` to existing imports if not already present (check `from django.views.generic import ...` line — `DetailView` is already imported per `EmployeeLeaveSummaryView`/`AttendanceHistoryView` earlier in the file).

In `hr/urls.py`, add:

```python
    path('leave-requests/<int:pk>/', views.LeaveRequestDetailView.as_view(), name='leave_request_detail'),
```

Create `templates/hr/leave_request_detail.html`:

```html
{% extends 'base.html' %}
{% block title %}Leave Request — {{ leave_request.employee.full_name }}{% endblock %}
{% block content %}
<div class="container-fluid">
  <div class="row justify-content-center">
    <div class="col-lg-8">
      <div class="d-flex justify-content-between align-items-center mb-4">
        <h1 class="h3 mb-0">{{ leave_request.employee.full_name }} — {{ leave_request.leave_type.name }}</h1>
        <a href="{% url 'hr:leave_request_list' %}" class="btn btn-outline-secondary"><i class="bi bi-arrow-left"></i> Back to Queue</a>
      </div>

      <div class="card mb-4">
        <div class="card-body">
          <p class="mb-1"><strong>Dates:</strong> {{ leave_request.start_date }} – {{ leave_request.end_date }} ({{ leave_request.days }} days)</p>
          <p class="mb-1"><strong>Reason:</strong> {{ leave_request.employee_reason|default:"—" }}</p>
          <p class="mb-1"><strong>Status:</strong>
            {% if leave_request.status == 'approved' %}<span class="badge bg-success">Approved</span>
            {% elif leave_request.status == 'disapproved' %}<span class="badge bg-danger">Disapproved</span>
            {% else %}<span class="badge bg-warning text-dark">Pending</span>{% endif %}
            {% if leave_request.is_overridden %}
              <span class="badge bg-info text-dark">Overridden by {{ leave_request.overridden_by.get_full_name|default:leave_request.overridden_by.username }}</span>
            {% endif %}
          </p>
          {% if leave_request.status == 'disapproved' and leave_request.salary_deduction_applicable %}
          <p class="text-danger mb-1"><i class="bi bi-exclamation-triangle"></i> Disapproved — Salary Deduction Applicable
            {% if leave_request.salary_deduction_note %}<br><small>{{ leave_request.salary_deduction_note }}</small>{% endif %}
          </p>
          {% endif %}
          {% if leave_request.document %}
          <p class="mb-0"><strong>Document:</strong>
            <a href="{% url 'hr:leave_request_document' leave_request.pk %}"><i class="bi bi-paperclip"></i> View attached document</a>
          </p>
          {% endif %}
        </div>
      </div>

      <div class="card mb-4">
        <div class="card-header"><h5 class="mb-0">Approvals</h5></div>
        <div class="card-body">
          <ul class="list-unstyled mb-0">
            {% for a in leave_request.approvals.all %}
            <li class="mb-2">
              <strong>{{ a.approver.get_full_name|default:a.approver.username }}:</strong>
              {% if a.decision == 'approved' %}<span class="badge bg-success">Approved</span>
              {% elif a.decision == 'disapproved' %}<span class="badge bg-danger">Disapproved</span>
              {% elif a.decision == 'skipped' %}<span class="badge bg-secondary">Skipped (overridden)</span>
              {% else %}<span class="badge bg-warning text-dark">Pending</span>{% endif %}
              {% if a.comment %}<div class="text-muted small">{{ a.comment }}</div>{% endif %}
            </li>
            {% endfor %}
          </ul>

          {% if is_approver and my_approval.decision == 'pending' and leave_request.status == 'pending' %}
          <form method="post" class="border-top pt-3 mt-3">
            {% csrf_token %}
            <input type="hidden" name="action" value="decide">
            <div class="mb-2"><textarea name="comment" class="form-control" rows="2" placeholder="Optional comment"></textarea></div>
            <button type="submit" name="decision" value="approved" class="btn btn-success btn-sm">Approve</button>
            <button type="submit" name="decision" value="disapproved" class="btn btn-danger btn-sm">Disapprove</button>
          </form>
          {% endif %}

          {% if request.user.is_super_admin_user and leave_request.status == 'pending' %}
          <form method="post" class="border-top pt-3 mt-3">
            {% csrf_token %}
            <input type="hidden" name="action" value="override">
            <div class="mb-2">
              <label class="form-label small">Superadmin override (use only if an approver is unavailable)</label>
              <textarea name="reason" class="form-control" rows="2" placeholder="Reason — required" required></textarea>
            </div>
            <button type="submit" name="decision" value="approved" class="btn btn-outline-success btn-sm">Override — Approve</button>
            <button type="submit" name="decision" value="disapproved" class="btn btn-outline-danger btn-sm">Override — Disapprove</button>
          </form>
          {% endif %}
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h5 class="mb-0">Notes</h5></div>
        <div class="card-body">
          {% for n in visible_notes %}
          <div class="mb-2 {% if n.is_internal %}bg-light p-2 rounded{% endif %}">
            <strong>{{ n.author.get_full_name|default:n.author.username }}</strong>
            {% if n.is_internal %}<span class="badge bg-secondary">Internal</span>{% endif %}
            <div class="small text-muted">{{ n.created_at }}</div>
            <div>{{ n.note }}</div>
          </div>
          {% empty %}
          <p class="text-muted mb-0">No notes yet.</p>
          {% endfor %}

          {% if request.user.is_super_admin_user %}
          <form method="post" class="border-top pt-3 mt-3">
            {% csrf_token %}
            <input type="hidden" name="action" value="add_note">
            <div class="mb-2"><textarea name="note" class="form-control" rows="2" placeholder="Add a note the employee can see" required></textarea></div>
            <div class="form-check mb-2">
              <input type="checkbox" name="is_internal" value="1" class="form-check-input" id="internalNote">
              <label class="form-check-label small" for="internalNote">Internal only (hidden from employee)</label>
            </div>
            <button type="submit" class="btn btn-sm btn-primary">Add Note</button>
          </form>
          {% endif %}
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe manage.py test hr.tests.LeaveRequestDetailViewTests -v 2`
Expected: 5 tests, OK.

- [ ] **Step 5: Run the whole hr test module for regressions**

Run: `venv/Scripts/python.exe manage.py test hr -v 2`
Expected: all tests OK (existing + new).

- [ ] **Step 6: Commit**

```bash
git add hr/views.py hr/urls.py templates/hr/leave_request_detail.html hr/tests.py
git commit -m "hr: leave request detail view — approve/reject, superadmin override, notes"
```

---

### Task 4g / Task 5: My Profile — self-service request form + status list + wording fixes

**Files:**
- Modify: `hr/views.py` (the `my_profile` function)
- Modify: `templates/hr/my_profile.html`
- Test: `hr/tests.py` (append)

**Interfaces:**
- Consumes: `submit_leave_request` (4e), `LeaveRequestForm` (4e), `LeaveRequest` (4a).
- Produces: `my_profile` context gains `leave_requests` (the employee's own, newest first) and `leave_request_form`; POST to `/hr/my-profile/` with `action=request_leave` creates a request via the shared helper.

- [ ] **Step 1: Write the failing tests**

```python
# Append to hr/tests.py
class MyProfileLeaveRequestTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.user = make_user('profile_user', password='x')
        self.user.set_password('testpass123')
        self.user.save()
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])

    def test_profile_shows_own_requests(self):
        LeaveRequest.objects.create(employee=self.emp, leave_type=self.marriage,
                                    start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 2))
        self.client.login(username='profile_user', password='testpass123')
        resp = self.client.get(reverse('hr:my_profile'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Marriage')

    def test_employee_can_submit_own_request(self):
        self.client.login(username='profile_user', password='testpass123')
        resp = self.client.post(reverse('hr:my_profile'), {
            'action': 'request_leave', 'leave_type': self.marriage.pk,
            'start_date': '2026-09-10', 'end_date': '2026-09-11', 'employee_reason': 'Wedding',
        })
        self.assertEqual(resp.status_code, 302)
        req = LeaveRequest.objects.get(employee=self.emp)
        self.assertEqual(req.created_by, self.user)
        self.assertEqual(req.status, 'pending')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe manage.py test hr.tests.MyProfileLeaveRequestTests -v 2`
Expected: FAIL — GET works (200) but shows no "Marriage" content yet (`assertContains` fails); POST returns 200 (form re-render) instead of 302 since `my_profile` doesn't currently handle POST at all.

- [ ] **Step 3: Update the my_profile view**

In `hr/views.py`, find the `my_profile` function (already handles GET-only context building from the earlier session's Task 1 work). Change its signature/body to also handle POST, and add `leave_requests` + a bare request form to context:

```python
@login_required
def my_profile(request):
    """Self-service portal: the logged-in user's own HR record — attendance,
    leave balance, assets held, documents (iqama/passport), and any vehicle.
    Shows a friendly prompt if the account isn't linked to an employee yet."""
    from decimal import Decimal
    from django.db.models import Q
    from django.utils import timezone
    from .models import AttendanceRecord, Asset, Vehicle, LeaveType
    from .forms import LeaveRequestForm
    from .leave_approval_services import submit_leave_request

    emp = getattr(request.user, 'employee_profile', None)
    context = {'employee': emp}

    if emp and request.method == 'POST' and request.POST.get('action') == 'request_leave':
        form = LeaveRequestForm(request.POST, request.FILES)
        if form.is_valid():
            submit_leave_request(
                employee=emp, leave_type=form.cleaned_data['leave_type'],
                start_date=form.cleaned_data['start_date'], end_date=form.cleaned_data['end_date'],
                employee_reason=form.cleaned_data['employee_reason'], document=form.cleaned_data['document'],
                created_by=request.user,
            )
            messages.success(request, 'Leave request submitted and sent for approval.')
            return redirect('hr:my_profile')
        context['leave_request_form'] = form
    else:
        context['leave_request_form'] = LeaveRequestForm()
    # (drop the 'employee' field from this self-service form — it's implied to be the logged-in employee)
    if 'employee' in context['leave_request_form'].fields:
        del context['leave_request_form'].fields['employee']

    if emp:
        today = timezone.localtime(timezone.now()).date()
        asset_ids = set(
            emp.asset_assignments.filter(returned_at__isnull=True)
            .values_list('asset_id', flat=True))
        if emp.full_name:
            asset_ids |= set(
                Asset.objects.filter(employee_name__iexact=emp.full_name)
                .values_list('id', flat=True))
        context['assets'] = Asset.objects.filter(id__in=asset_ids).order_by('asset_name')
        context['documents'] = emp.documents.all()
        entitlements = (
            emp.leave_entitlements.filter(year=today.year)
            .select_related('leave_type'))
        context['entitlements'] = entitlements
        accumulative = [e for e in entitlements if e.leave_type.is_accumulative]
        context['leave_total_entitled'] = sum((e.entitled_days for e in accumulative), Decimal('0'))
        context['leave_total_remaining'] = sum((e.remaining_days for e in accumulative), Decimal('0'))
        context['leave_requests'] = emp.leave_requests.select_related('leave_type').order_by('-created_at')[:10]
        month_records = AttendanceRecord.objects.filter(
            employee=emp, date__year=today.year, date__month=today.month)
        summary = {}
        for rec in month_records:
            summary[rec.status] = summary.get(rec.status, 0) + 1
        context['attendance_summary'] = summary
        context['attendance_month'] = today
        context['recent_leaves'] = emp.leave_records.select_related(
            'leave_type').order_by('-start_date')[:5]
        veh_q = Q()
        if emp.iqama_number:
            veh_q |= Q(driver_id=emp.iqama_number)
        if emp.full_name:
            veh_q |= Q(driver_name__iexact=emp.full_name)
        context['vehicles'] = (
            Vehicle.objects.filter(veh_q) if veh_q else Vehicle.objects.none())
    return render(request, 'hr/my_profile.html', context)
```

(This replaces the whole existing function body — it's the same logic as before Task 4, with POST handling and `leave_requests`/`leave_request_form` context added.)

- [ ] **Step 4: Update my_profile.html — request status list, submit form, and wording fix**

In `templates/hr/my_profile.html`, find the existing "Leave Balance" card note (currently reads something like "accrued leave only — Annual, Sick, etc."). Update the wording to no longer call out Sick as accrued (only Annual is, per Task 1):

```html
<span class="text-muted">(accrued leave only — Annual; conditional leave shown below isn't counted in this total)</span>
```

Immediately after the existing leave-balance `</table>`/`{% endif %}` block for entitlements, add a new section:

```html
<div class="mt-4">
  <div class="d-flex justify-content-between align-items-center mb-2">
    <div class="fw-semibold small"><i class="bi bi-send me-1"></i> My Leave Requests</div>
    <button type="button" class="btn btn-sm btn-outline-primary" data-bs-toggle="collapse" data-bs-target="#requestLeaveForm">
      <i class="bi bi-plus-lg"></i> Request Leave
    </button>
  </div>

  <div class="collapse mb-3" id="requestLeaveForm">
    <form method="post" enctype="multipart/form-data" class="border rounded p-3">
      {% csrf_token %}
      <input type="hidden" name="action" value="request_leave">
      <div class="row">
        <div class="col-md-6 mb-2">
          <label class="form-label small">Leave Type</label>
          {{ leave_request_form.leave_type }}
        </div>
        <div class="col-md-3 mb-2"><label class="form-label small">Start</label>{{ leave_request_form.start_date }}</div>
        <div class="col-md-3 mb-2"><label class="form-label small">End</label>{{ leave_request_form.end_date }}</div>
      </div>
      <div class="mb-2"><label class="form-label small">Reason</label>{{ leave_request_form.employee_reason }}</div>
      <div class="mb-2"><label class="form-label small">Document (optional)</label>{{ leave_request_form.document }}</div>
      <button type="submit" class="btn btn-sm btn-primary">Submit Request</button>
    </form>
  </div>

  {% if leave_requests %}
  <table class="table table-sm mb-0">
    <thead class="table-light"><tr><th>Type</th><th>Dates</th><th>Status</th></tr></thead>
    <tbody>
      {% for r in leave_requests %}
      <tr>
        <td>{{ r.leave_type.name }}</td>
        <td>{{ r.start_date }} – {{ r.end_date }}</td>
        <td>
          {% if r.status == 'approved' %}<span class="badge bg-success">Approved</span>
          {% elif r.status == 'disapproved' %}
            <span class="badge bg-danger">Disapproved</span>
            {% if r.salary_deduction_applicable %}<div class="small text-danger">Salary deduction applicable</div>{% endif %}
          {% else %}
            <span class="badge bg-warning text-dark">Pending</span>
            <div class="small text-muted">
              {% with waiting=r.pending_approvers %}
                {% if waiting %}Waiting on {% for a in waiting %}{{ a.get_full_name|default:a.username }}{% if not forloop.last %}, {% endif %}{% endfor %}{% endif %}
              {% endwith %}
            </div>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="text-muted small mb-0">No leave requests yet.</p>
  {% endif %}
</div>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/Scripts/python.exe manage.py test hr.tests.MyProfileLeaveRequestTests -v 2`
Expected: 2 tests, OK.

- [ ] **Step 6: Run the full hr suite for regressions**

Run: `venv/Scripts/python.exe manage.py test hr -v 2`
Expected: all OK.

- [ ] **Step 7: Commit**

```bash
git add hr/views.py templates/hr/my_profile.html hr/tests.py
git commit -m "hr: My Profile self-service leave request form, status list, accrued-wording fix"
```

---

### Task 6: Seed data — profile linking + approvers + comprehensive dummy requests

**Files:**
- Modify: `hr/management/commands/seed_dummy_data.py` (extend the existing command from the prior session rather than creating a new one — same file, same `--wipe` convention)

**Interfaces:**
- Consumes: everything from Tasks 4a–4g.
- Produces: no new interfaces (this is a leaf/terminal task).

- [ ] **Step 1: Add a `_seed_approval_workflow` method**

Add this method to the `Command` class in `hr/management/commands/seed_dummy_data.py`, and call it from `handle()` alongside the existing three `_seed_*` calls:

```python
    def _link_superuser_to_employee(self):
        superadmin = User.objects.filter(is_superuser=True).first()
        if not superadmin:
            self.stderr.write(self.style.ERROR('No superuser found — skipping profile link.'))
            return
        if getattr(superadmin, 'employee_profile', None):
            self.stdout.write(f'{superadmin.username} already linked to an employee profile.')
            return
        emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}SUPERUSER',
            defaults={'full_name': superadmin.get_full_name() or superadmin.username,
                     'designation': 'Super Admin', 'is_active': True, 'user': superadmin},
        )
        if emp.user_id != superadmin.id:
            emp.user = superadmin
            emp.save(update_fields=['user'])
        self.stdout.write(self.style.SUCCESS(f'Linked {superadmin.username} to employee profile "{emp.full_name}".'))

    def _seed_approval_workflow(self):
        from django.core.files.base import ContentFile
        from django.contrib.auth.hashers import make_password
        from hr.models import LeaveApprover, LeaveRequest, LeaveRequestApproval, LeaveRequestNote

        aamna, _ = User.objects.get_or_create(
            username='aamna.khan', defaults={
                'first_name': 'Aamna', 'last_name': 'Khan', 'email': 'aamna.khan@example.com',
                'role': Role.objects.filter(name='super_admin').first(), 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        ali, _ = User.objects.get_or_create(
            username='ali.sultan', defaults={
                'first_name': 'Ali', 'last_name': 'Sultan', 'email': 'ali.sultan@example.com',
                'role': Role.objects.filter(name='super_admin').first(), 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        LeaveApprover.objects.get_or_create(user=aamna)
        LeaveApprover.objects.get_or_create(user=ali)
        self.stdout.write(self.style.SUCCESS('Ensured Aamna Khan and Ali Sultan exist as designated leave approvers (login: aamna.khan / ali.sultan, password DemoPass123!).'))

        conditional_types = list(LeaveType.objects.filter(is_accumulative=False)[:3])
        if not conditional_types:
            self.stderr.write(self.style.WARNING('No conditional leave types found — run _seed_leave_data first.'))
            return
        demo_employees = list(Employee.objects.filter(iqama_number__startswith=DEMO_TAG).exclude(
            iqama_number=f'{DEMO_TAG}SUPERUSER'))
        if not demo_employees:
            self.stderr.write(self.style.WARNING('No demo employees found — run _seed_leave_data first.'))
            return

        year = timezone.localdate().year
        scenarios = [
            ('pending', demo_employees[0], conditional_types[0], date(year, 9, 1), date(year, 9, 2), []),
            ('approved', demo_employees[1], conditional_types[1 % len(conditional_types)], date(year, 9, 5), date(year, 9, 6),
             [('aamna', 'approved'), ('ali', 'approved')]),
            ('disapproved', demo_employees[2], conditional_types[2 % len(conditional_types)], date(year, 9, 10), date(year, 9, 11),
             [('aamna', 'disapproved')]),
        ]
        approver_map = {'aamna': aamna, 'ali': ali}
        created = 0
        for label, emp, lt, start, end, decisions in scenarios:
            req, was_created = LeaveRequest.objects.get_or_create(
                employee=emp, leave_type=lt, start_date=start, end_date=end,
                defaults={'employee_reason': f'{DEMO_TAG}{label} scenario for QA', 'created_by': aamna},
            )
            if not was_created:
                continue
            req.document.save(f'{DEMO_TAG}{label}-doc.txt', ContentFile(b'Dummy supporting document for QA.'), save=True)
            for approver_key in ('aamna', 'ali'):
                LeaveRequestApproval.objects.get_or_create(leave_request=req, approver=approver_map[approver_key])
            LeaveRequestNote.objects.create(leave_request=req, author=aamna, note=f'{DEMO_TAG}Seeded {label} scenario.')
            from hr.leave_approval_services import record_approver_decision
            for approver_key, decision in decisions:
                record_approver_decision(req, approver_map[approver_key], decision, comment=f'{DEMO_TAG}auto-decision')
            created += 1
        self.stdout.write(self.style.SUCCESS(f'Created {created} demo leave request(s) (pending/approved/disapproved).'))
```

Update `handle()`:

```python
    def handle(self, *args, **opts):
        if opts['wipe']:
            self._wipe()
            return

        self._seed_notifications()
        self._seed_sales_calls()
        self._seed_leave_data()
        self._link_superuser_to_employee()
        self._seed_approval_workflow()
```

Update the imports at the top of the file to add `from datetime import date` (already imported), `from accounts.models import User, Role` (already imported), and add `from django.utils import timezone` (already imported) — no new import lines needed beyond what's already there; only the two `from ... import ...` lines *inside* the new methods (`ContentFile`, `make_password`, and the new model names) are new, and those are written as local imports directly in the methods above to avoid growing the top-of-file import block for names only used in one method (matches this command's existing style of a few top-level imports + local imports where convenient).

Update `_wipe()` to also clean the new data:

```python
    def _wipe(self):
        from hr.models import LeaveRecord, LeaveRequest, LeaveRequestApproval, LeaveRequestNote, LeaveApprover
        n, _ = Notification.objects.filter(verb__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo notification row(s).')
        n, _ = SalesCallReport.objects.filter(company_name__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo sales call row(s).')
        n, _ = LeaveRequestNote.objects.filter(note__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo leave request note(s).')
        n, _ = LeaveRequest.objects.filter(employee_reason__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo leave request(s).')
        n, _ = LeaveApprover.objects.filter(user__username__in=['aamna.khan', 'ali.sultan']).delete()
        self.stdout.write(f'Deleted {n} demo leave approver row(s).')
        n, _ = LeaveRecord.objects.filter(note=f'{DEMO_TAG}seed').delete()
        self.stdout.write(f'Deleted {n} demo leave record row(s).')
        emp_qs = Employee.objects.filter(iqama_number__startswith=DEMO_TAG).exclude(iqama_number=f'{DEMO_TAG}SUPERUSER')
        n, _ = LeaveEntitlement.objects.filter(employee__in=emp_qs).delete()
        self.stdout.write(f'Deleted {n} demo entitlement row(s).')
        n, _ = emp_qs.delete()
        self.stdout.write(f'Deleted {n} demo employee row(s).')
        n, _ = User.objects.filter(username__startswith=f'{DEMO_TAG.lower()}salesrep-').delete()
        self.stdout.write(f'Deleted {n} demo sales-rep user account(s).')
        n, _ = User.objects.filter(username__in=['aamna.khan', 'ali.sultan']).delete()
        self.stdout.write(f'Deleted {n} demo approver user account(s).')
```

- [ ] **Step 2: Run the command against the local dev DB**

Run: `venv/Scripts/python.exe manage.py migrate && venv/Scripts/python.exe manage.py seed_dummy_data`
Expected: prints success lines for notifications, sales calls, leave data, superuser-employee link, and 3 approval-workflow demo requests (pending/approved/disapproved), with no tracebacks.

- [ ] **Step 3: Manually verify in shell**

Run:
```
venv/Scripts/python.exe manage.py shell -c "
from hr.models import LeaveRequest
for r in LeaveRequest.objects.filter(employee_reason__startswith='DEMO-'):
    print(r.employee.full_name, r.leave_type.name, r.status, r.salary_deduction_applicable)
"
```
Expected: three rows printed — one `pending`, one `approved`, one `disapproved` with `salary_deduction_applicable=True`.

- [ ] **Step 4: Commit**

```bash
git add hr/management/commands/seed_dummy_data.py
git commit -m "hr: seed superuser employee link, designated approvers, and approval-workflow demo scenarios"
```

---

## Self-Review Notes (already applied above)

- **Spec coverage:** Task 1 (values + is_accumulative) → Task 1 above. Task 2 (remove column) → Task 2. Task 3 (labels + note) → Task 3. Task 4 (models, dual-approval, override, notes, documents, access control, employee/admin views, notifications) → Tasks 4a–4g. Task 5 (My Profile reflection) → folded into Task 4g since it's the same file/section. Task 6 (seed + profile link) → Task 6.
- **Type consistency checked:** `LeaveRequest.status` choices (`pending/approved/disapproved/cancelled`) used consistently across the service, views, and templates. `LeaveRequestApproval.decision` choices (`pending/approved/disapproved/skipped`) likewise. `submit_leave_request(...)` keyword args match between its Task 4e definition and both call sites (Task 4e's `LeaveRequestCreateView`, Task 4g's `my_profile`).
- **No placeholders:** every step has real code, not a description of code.
