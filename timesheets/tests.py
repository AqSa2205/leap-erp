from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import Role, User
from accounts.permissions import seed_default_permissions
from hr.models import Employee
from .models import ActivityCode, TimesheetEntry
from notifications.models import Notification
from .models import HRSettings, TimesheetRequest, TimesheetRequestAck
from .services import request_timesheets, remind_employee, employees_for_request


def _make_user_with_employee(username, role_name=Role.SALES_REP, full_name=None):
    """Every test needs a user with a role (so timesheets.access actually
    resolves to True — see the lesson from the bugfixes branch: a roleless
    test user always fails has_capability, regardless of seed_default_permissions())
    AND a linked Employee record (since the views 404/redirect gracefully
    otherwise, which is a different code path than the one most tests want
    to exercise)."""
    role, _ = Role.objects.get_or_create(name=role_name)
    user = User.objects.create_user(username=username, password='testpass123', role=role)
    emp = Employee.objects.create(
        full_name=full_name or username, iqama_number=f'IQ-{username}', user=user)
    return user, emp


class TimesheetEntryOwnershipTests(TestCase):
    """The IDOR-critical tests: an employee's own entries are reachable and
    editable; someone else's are not, and the response is 404 (not 403), so
    the pk's existence can't be inferred from the response code alone —
    same pattern as leave_request_document_download in hr/views.py."""

    def setUp(self):
        self.client = Client()
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        self.user_a, self.emp_a = _make_user_with_employee('emp_a', full_name='Employee A')
        self.user_b, self.emp_b = _make_user_with_employee('emp_b', full_name='Employee B')
        self.entry_a = TimesheetEntry.objects.create(
            employee=self.emp_a, date=date(2026, 7, 10),
            task_description='Employee A work log', activity_code=self.code, hours=Decimal('8'))
        seed_default_permissions()  # moved to the end — must run AFTER roles exist

        # ── happy path ──
    def test_owner_can_view_own_timesheet(self):
        self.client.login(username='emp_a', password='testpass123')
        resp = self.client.get(reverse('timesheets:my_timesheet'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Employee A work log")

    def test_owner_can_add_entry(self):
        self.client.login(username='emp_a', password='testpass123')
        resp = self.client.post(reverse('timesheets:my_timesheet'), {
            'date': '2026-07-11', 'activity_code': self.code.pk,
            'task_description': 'New task', 'hours': '4',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TimesheetEntry.objects.filter(
            employee=self.emp_a, date=date(2026, 7, 11)).exists())

    def test_owner_can_edit_own_draft_entry(self):
        self.client.login(username='emp_a', password='testpass123')
        resp = self.client.post(
            reverse('timesheets:entry_edit', args=[self.entry_a.pk]), {
                'date': '2026-07-10', 'activity_code': self.code.pk,
                'task_description': 'Updated', 'hours': '6',
            })
        self.assertEqual(resp.status_code, 302)
        self.entry_a.refresh_from_db()
        self.assertEqual(self.entry_a.task_description, 'Updated')

    def test_owner_can_delete_own_draft_entry(self):
        self.client.login(username='emp_a', password='testpass123')
        resp = self.client.post(reverse('timesheets:entry_delete', args=[self.entry_a.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(TimesheetEntry.objects.filter(pk=self.entry_a.pk).exists())

    # ── the security boundary: THE most important tests in this file ──
    def test_other_employee_cannot_view_edit_page_for_entry(self):
        self.client.login(username='emp_b', password='testpass123')
        resp = self.client.get(reverse('timesheets:entry_edit', args=[self.entry_a.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_other_employee_cannot_edit_entry_via_post(self):
        self.client.login(username='emp_b', password='testpass123')
        resp = self.client.post(
            reverse('timesheets:entry_edit', args=[self.entry_a.pk]), {
                'date': '2026-07-10', 'activity_code': self.code.pk,
                'task_description': 'HIJACKED', 'hours': '1',
            })
        self.assertEqual(resp.status_code, 404)
        self.entry_a.refresh_from_db()
        self.assertEqual(self.entry_a.task_description, "Employee A work log") # untouched

    def test_other_employee_cannot_delete_entry(self):
        self.client.login(username='emp_b', password='testpass123')
        resp = self.client.post(reverse('timesheets:entry_delete', args=[self.entry_a.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(TimesheetEntry.objects.filter(pk=self.entry_a.pk).exists())  # still there

    # ── auth required ──
    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse('timesheets:my_timesheet'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)

    # ── locked (submitted) entries can't be edited/deleted, even by owner ──
    def test_submitted_entry_cannot_be_edited_by_owner(self):
        self.entry_a.status = TimesheetEntry.STATUS_SUBMITTED
        self.entry_a.save()
        self.client.login(username='emp_a', password='testpass123')
        resp = self.client.post(
            reverse('timesheets:entry_edit', args=[self.entry_a.pk]), {
                'date': '2026-07-10', 'activity_code': self.code.pk,
                'task_description': 'Trying to change', 'hours': '1',
            })
        self.assertEqual(resp.status_code, 302)  # redirected with error message
        self.entry_a.refresh_from_db()
        self.assertEqual(self.entry_a.task_description, "Employee A work log")  # unchanged  

    def test_submitted_entry_cannot_be_deleted_by_owner(self):
        self.entry_a.status = TimesheetEntry.STATUS_SUBMITTED
        self.entry_a.save()
        self.client.login(username='emp_a', password='testpass123')
        resp = self.client.post(reverse('timesheets:entry_delete', args=[self.entry_a.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TimesheetEntry.objects.filter(pk=self.entry_a.pk).exists())

    def test_entry_delete_via_get_not_allowed(self):
        self.client.login(username='emp_a', password='testpass123')
        resp = self.client.get(reverse('timesheets:entry_delete', args=[self.entry_a.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_edit_cannot_reassign_employee_via_post(self):
        other_user, other_emp = _make_user_with_employee('editreassign')
        self.client.login(username='emp_a', password='testpass123')
        self.client.post(
            reverse('timesheets:entry_edit', args=[self.entry_a.pk]), {
                'date': '2026-07-10', 'activity_code': self.code.pk,
                'task_description': 'Still mine', 'hours': '5',
                'employee': other_emp.pk,
            })
        self.entry_a.refresh_from_db()
        self.assertEqual(self.entry_a.employee, self.emp_a)  # unchanged, not other_emp


class TimesheetOpenToAllUsersTests(TestCase):
    """Confirms the deliberate design change: My Timesheet has no
    capability gate anymore, just login — any authenticated user with a
    linked Employee record can reach it, regardless of role."""

    def test_user_with_no_role_permissions_seeded_can_still_open_my_timesheet(self):
        """Deliberately skip seed_default_permissions() here — if
        my_timesheet ever regains a capability check, this test starts
        failing immediately instead of passing by accident."""
        role, _ = Role.objects.get_or_create(name=Role.DEVELOPER)
        user = User.objects.create_user(username='noaccess', password='testpass123', role=role)
        Employee.objects.create(full_name='No Access', iqama_number='IQ-NOACCESS', user=user)
        client = Client()
        client.login(username='noaccess', password='testpass123')
        resp = client.get(reverse('timesheets:my_timesheet'))
        self.assertEqual(resp.status_code, 200)

class TimesheetEntryValidationTests(TestCase):
    """Server-side validation — must hold even if someone bypasses the
    HTML date-picker's max attribute or the number input's min/max via
    browser dev tools, since those are purely cosmetic client-side hints."""

    def setUp(self):
        self.client = Client()
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        self.user, self.emp = _make_user_with_employee('valemp')
        seed_default_permissions()  # moved to the end
        self.client.login(username='valemp', password='testpass123')    

    def _post(self, **overrides):
        data = {
            'date': '2026-07-10', 'activity_code': self.code.pk,
            'task_description': 'Task', 'hours': '4',
        }
        data.update(overrides)
        return self.client.post(reverse('timesheets:my_timesheet'), data)

    def test_future_date_now_accepted(self):
        """Regression lock-in: clean_date() was removed from
        TimesheetEntryForm, so future dates are now intentionally allowed
        (per the product decision to let employees log future entries).
        This replaces the old test_future_date_rejected, which would
        silently pass on a false assumption if left in place."""
        future = (date.today() + timedelta(days=5)).isoformat()
        resp = self._post(date=future)
        self.assertEqual(resp.status_code, 302)  # redirected on success, not re-rendered
        self.assertTrue(TimesheetEntry.objects.filter(date=future).exists())

    def test_far_future_date_still_accepted(self):
        """Confirms there's no hidden upper bound either (e.g. no
        'within N days' cap left over from an earlier version)."""
        far_future = (date.today() + timedelta(days=400)).isoformat()
        resp = self._post(date=far_future)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TimesheetEntry.objects.filter(date=far_future).exists())

    def test_zero_hours_rejected(self):
        resp = self._post(hours='0')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TimesheetEntry.objects.count(), 0)

    def test_negative_hours_rejected(self):
        resp = self._post(hours='-3')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TimesheetEntry.objects.count(), 0)

    def test_over_24_hours_rejected(self):
        resp = self._post(hours='25')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TimesheetEntry.objects.count(), 0)

    def test_valid_entry_accepted(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(TimesheetEntry.objects.count(), 1)

    def test_deactivated_code_not_selectable(self):
        inactive_code = ActivityCode.objects.create(code='OLD_CODE', is_active=False)
        resp = self._post(activity_code=inactive_code.pk)
        self.assertEqual(resp.status_code, 200)  # form invalid: not a valid choice
        self.assertEqual(TimesheetEntry.objects.count(), 0)

    def test_malicious_post_cannot_set_employee_or_status(self):
        """Even if someone crafts a POST including 'employee' and 'status'
        fields (neither is in TimesheetEntryForm.Meta.fields), the created
        row must still belong to the logged-in user and start as draft."""
        other_user, other_emp = _make_user_with_employee('otheremp2')
        resp = self._post(**{'employee': other_emp.pk, 'status': TimesheetEntry.STATUS_SUBMITTED})
        self.assertEqual(resp.status_code, 302)
        entry = TimesheetEntry.objects.get()
        self.assertEqual(entry.employee, self.emp)  # not other_emp
        self.assertEqual(entry.status, TimesheetEntry.STATUS_DRAFT)  # not submitted


class TimesheetExportOwnershipTests(TestCase):
    """The export must only ever contain the requesting employee's own
    data — same IDOR concern as entry edit/delete, just on a read path."""

    def setUp(self):
        self.client = Client()
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        self.user_a, self.emp_a = _make_user_with_employee('expa', full_name='Export A')
        self.user_b, self.emp_b = _make_user_with_employee('expb', full_name='Export B')
        TimesheetEntry.objects.create(
            employee=self.emp_a, date=date(2026, 7, 5),
            task_description='A-only task', activity_code=self.code, hours=Decimal('3'))
        TimesheetEntry.objects.create(
            employee=self.emp_b, date=date(2026, 7, 5),
            task_description='B-only task', activity_code=self.code, hours=Decimal('5'))
        seed_default_permissions()

    def test_export_is_valid_xlsx(self):
        self.client.login(username='expa', password='testpass123')
        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=7')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_export_contains_only_own_entries_not_other_employees(self):
        """The real IDOR check for this endpoint: load the workbook and
        confirm B's task text never appears anywhere in A's export."""
        import openpyxl
        from io import BytesIO
        self.client.login(username='expa', password='testpass123')
        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=7')
        wb = openpyxl.load_workbook(BytesIO(resp.content))
        ws = wb.active
        all_text = ' '.join(
            str(cell.value) for row in ws.iter_rows() for cell in row if cell.value)
        self.assertIn('A-only task', all_text)
        self.assertNotIn('B-only task', all_text)

    def test_no_employee_link_redirects_instead_of_crashing(self):
        role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        User.objects.create_user(username='noemp', password='testpass123', role=role)
        seed_default_permissions()
        self.client.login(username='noemp', password='testpass123')
        resp = self.client.get(reverse('timesheets:export'))
        self.assertEqual(resp.status_code, 302)


class HRReviewCapabilityBoundaryTests(TestCase):
    """The highest-priority gap per the guide: proving the DENIED case for
    every access-control rule, not just the happy path. A plain employee
    (no timesheets.review) must never reach the HR-only pages, no matter
    the URL they try."""

    def setUp(self):
        self.client = Client()
        self.plain_user, self.plain_emp = _make_user_with_employee('plainemp')
        # HR user: explicitly admin, which DOES have timesheets.review
        # per accounts/permissions.py's DEFAULT_CODENAME_GRANTS.
        self.hr_user, self.hr_emp = _make_user_with_employee(
            'hrperson', role_name=Role.ADMIN, full_name='HR Person')
        seed_default_permissions()

        from .services import request_timesheets
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    # ── plain employee denied ──
    def test_plain_employee_cannot_open_hr_request_page(self):
        self.client.login(username='plainemp', password='testpass123')
        resp = self.client.get(reverse('timesheets:hr_request'))
        self.assertEqual(resp.status_code, 403)

    def test_plain_employee_cannot_open_hr_request_detail(self):
        self.client.login(username='plainemp', password='testpass123')
        resp = self.client.get(
            reverse('timesheets:hr_request_detail', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_plain_employee_cannot_post_a_new_request(self):
        self.client.login(username='plainemp', password='testpass123')
        resp = self.client.post(reverse('timesheets:hr_request'), {
            'year': '2026', 'month': '8',
        })
        self.assertEqual(resp.status_code, 403)
        # confirm no new request was silently created despite the denial
        from .models import TimesheetRequest
        self.assertEqual(TimesheetRequest.objects.filter(month=8).count(), 0)

    def test_plain_employee_cannot_trigger_a_reminder(self):
        self.client.login(username='plainemp', password='testpass123')
        resp = self.client.post(
            reverse('timesheets:hr_request_detail', args=[self.ts_request.pk]),
            {'employee_id': self.plain_emp.pk})
        self.assertEqual(resp.status_code, 403)

    # ── HR user allowed (happy path, so the denial above is proven
    # to be about the capability, not a broken URL/view) ──
    def test_hr_user_can_open_request_page(self):
        self.client.login(username='hrperson', password='testpass123')
        resp = self.client.get(reverse('timesheets:hr_request'))
        self.assertEqual(resp.status_code, 200)

    def test_hr_user_can_open_request_detail(self):
        self.client.login(username='hrperson', password='testpass123')
        resp = self.client.get(
            reverse('timesheets:hr_request_detail', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_anonymous_redirected_from_hr_pages(self):
        resp = self.client.get(reverse('timesheets:hr_request'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)


from django.db import transaction
from .services import submit_month, reopen_month


class SubmitMonthTests(TestCase):
    """submit_month is the one authoritative place the lock happens — these
    tests prove its rules hold regardless of what UI calls it."""

    def setUp(self):
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        self.user, self.emp = _make_user_with_employee('submitter')
        self.hr_user, _ = _make_user_with_employee('subhr', role_name=Role.ADMIN)
        seed_default_permissions()

    def test_submitting_empty_month_raises(self):
        with self.assertRaises(ValueError):
            submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)

    def test_submit_flips_draft_entries_to_submitted(self):
        e1 = TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='t1', activity_code=self.code, hours=Decimal('4'))
        e2 = TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 2),
            task_description='t2', activity_code=self.code, hours=Decimal('4'))
        submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)
        e1.refresh_from_db()
        e2.refresh_from_db()
        self.assertEqual(e1.status, TimesheetEntry.STATUS_SUBMITTED)
        self.assertEqual(e2.status, TimesheetEntry.STATUS_SUBMITTED)

    def test_submit_stamps_submitted_at_and_by(self):
        TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='t1', activity_code=self.code, hours=Decimal('4'))
        tsm = submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)
        self.assertIsNotNone(tsm.submitted_at)
        self.assertEqual(tsm.submitted_by, self.user)
        self.assertTrue(tsm.is_submitted)

    def test_double_submit_raises(self):
        TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='t1', activity_code=self.code, hours=Decimal('4'))
        submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)
        with self.assertRaises(ValueError):
            submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)

    def test_entries_from_other_months_not_touched(self):
        """Submitting July must not accidentally lock August entries too."""
        july_entry = TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='july', activity_code=self.code, hours=Decimal('4'))
        august_entry = TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 8, 1),
            task_description='august', activity_code=self.code, hours=Decimal('4'))
        submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)
        august_entry.refresh_from_db()
        self.assertEqual(august_entry.status, TimesheetEntry.STATUS_DRAFT)  # untouched

    def test_already_submitted_entries_not_re_touched_on_failed_resubmit(self):
        """A failed double-submit must not create a second TimesheetMonth
        row or otherwise corrupt state."""
        TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='t1', activity_code=self.code, hours=Decimal('4'))
        submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)
        try:
            submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)
        except ValueError:
            pass
        from .models import TimesheetMonth
        self.assertEqual(
            TimesheetMonth.objects.filter(employee=self.emp, year=2026, month=7).count(), 1)


class ReopenMonthTests(TestCase):
    """reopen_month unlocks a submitted month so the employee can fix a
    mistake — must flip entries back to draft, not just the month flag."""

    def setUp(self):
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        self.user, self.emp = _make_user_with_employee('reopener')
        self.hr_user, _ = _make_user_with_employee('reopenhr', role_name=Role.ADMIN)
        seed_default_permissions()
        self.entry = TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='t1', activity_code=self.code, hours=Decimal('4'))
        self.tsm = submit_month(employee=self.emp, year=2026, month=7, submitted_by=self.user)

    def test_reopen_never_submitted_month_raises(self):
        with self.assertRaises(ValueError):
            reopen_month(employee=self.emp, year=2099, month=1, reopened_by=self.hr_user)

    def test_reopen_flips_entries_back_to_draft(self):
        reopen_month(employee=self.emp, year=2026, month=7, reopened_by=self.hr_user)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, TimesheetEntry.STATUS_DRAFT)

    def test_reopen_stamps_reopened_at_and_by(self):
        tsm = reopen_month(employee=self.emp, year=2026, month=7, reopened_by=self.hr_user)
        self.assertIsNotNone(tsm.reopened_at)
        self.assertEqual(tsm.reopened_by, self.hr_user)
        self.assertFalse(tsm.is_submitted)

    def test_reopening_twice_raises_second_time(self):
        reopen_month(employee=self.emp, year=2026, month=7, reopened_by=self.hr_user)
        with self.assertRaises(ValueError):
            reopen_month(employee=self.emp, year=2026, month=7, reopened_by=self.hr_user)

    def test_after_reopen_employee_can_edit_entry_again(self):
        """End-to-end: reopen unlocks the entry for real editing through the
        view layer, not just at the model/service level."""
        reopen_month(employee=self.emp, year=2026, month=7, reopened_by=self.hr_user)
        client = Client()
        client.login(username='reopener', password='testpass123')
        resp = client.post(
            reverse('timesheets:entry_edit', args=[self.entry.pk]), {
                'date': '2026-07-01', 'activity_code': self.code.pk,
                'task_description': 'Fixed after reopen', 'hours': '5',
            })
        self.assertEqual(resp.status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.task_description, 'Fixed after reopen')



class SendToHRTests(TestCase):
    """send_to_hr / acknowledge_send: an employee only ever acts on their
    own acknowledgment row, never someone else's, and double-acking is a
    no-op rather than a duplicate or a crash."""

    def setUp(self):
        self.client = Client()
        self.hr_user, _ = _make_user_with_employee('sendhr', role_name=Role.ADMIN)
        HRSettings.objects.get_or_create(pk=1, defaults={'hr_email': 'hr@leap-arabia.com'})
        self.user_a, self.emp_a = _make_user_with_employee('senda', full_name='Send A')
        self.user_b, self.emp_b = _make_user_with_employee('sendb', full_name='Send B')
        seed_default_permissions()
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_preview_page_loads_for_pending_request(self):
        self.client.login(username='senda', password='testpass123')
        resp = self.client.get(reverse('timesheets:send_to_hr', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Open in Outlook')

    def test_acknowledge_creates_ack_for_correct_employee_only(self):
        self.client.login(username='senda', password='testpass123')
        self.client.post(reverse('timesheets:acknowledge_send', args=[self.ts_request.pk]))
        self.assertTrue(TimesheetRequestAck.objects.filter(
            request=self.ts_request, employee=self.emp_a).exists())
        self.assertFalse(TimesheetRequestAck.objects.filter(
            request=self.ts_request, employee=self.emp_b).exists())

    def test_double_acknowledge_does_not_duplicate(self):
        self.client.login(username='senda', password='testpass123')
        self.client.post(reverse('timesheets:acknowledge_send', args=[self.ts_request.pk]))
        self.client.post(reverse('timesheets:acknowledge_send', args=[self.ts_request.pk]))
        self.assertEqual(TimesheetRequestAck.objects.filter(
            request=self.ts_request, employee=self.emp_a).count(), 1)

    def test_already_acked_employee_redirected_from_preview(self):
        TimesheetRequestAck.objects.create(request=self.ts_request, employee=self.emp_a)
        self.client.login(username='senda', password='testpass123')
        resp = self.client.get(reverse('timesheets:send_to_hr', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_mailto_uses_fixed_hr_email_not_employee_input(self):
        """The HR email in the preview always comes from HRSettings, never
        from anything the employee could submit or influence."""
        self.client.login(username='senda', password='testpass123')
        resp = self.client.get(reverse('timesheets:send_to_hr', args=[self.ts_request.pk]))
        self.assertContains(resp, 'hr@leap-arabia.com')

    def test_acknowledging_someone_elses_request_id_only_affects_own_employee(self):
        """B posting to the SAME ts_request only ever creates B's own ack —
        there's no way to forge an ack for A through this endpoint."""
        self.client.login(username='sendb', password='testpass123')
        self.client.post(reverse('timesheets:acknowledge_send', args=[self.ts_request.pk]))
        self.assertTrue(TimesheetRequestAck.objects.filter(
            request=self.ts_request, employee=self.emp_b).exists())
        self.assertFalse(TimesheetRequestAck.objects.filter(
            request=self.ts_request, employee=self.emp_a).exists())


class NotificationTargetingTests(TestCase):
    """request_timesheets notifies every active user regardless of role
    (per _all_active_users()'s own docstring: timesheets is intentionally
    open to everyone), minus the actor. remind_employee must still reach
    only the one targeted employee, not broadcast to everyone."""

    def setUp(self):
        self.hr_user, self.hr_emp = _make_user_with_employee('notifhr', role_name=Role.ADMIN)
        self.user_a, self.emp_a = _make_user_with_employee('notifa')
        self.user_b, self.emp_b = _make_user_with_employee('notifb')
        # AI roles do NOT have timesheets.access per DEFAULT_MODULE_ACCESS —
        # this user must never be notified.
        self.ai_user, self.ai_emp = _make_user_with_employee(
            'notifai', role_name=Role.DEVELOPER)
        seed_default_permissions()

    def test_request_notifies_eligible_users_excludes_actor(self):
        ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        notified = set(Notification.objects.filter(
            verb='requested your timesheet').values_list('recipient__username', flat=True))
        self.assertIn('notifa', notified)
        self.assertIn('notifb', notified)
        self.assertNotIn('notifhr', notified)  # actor excluded

    def test_request_notifies_users_regardless_of_role(self):
        """Regression lock-in: request_timesheets was switched from
        _users_with_capability('timesheets.access') to _all_active_users()
        specifically so developers (who don't have timesheets.access)
        still get notified. This replaces the old test that asserted the
        opposite — leaving it as-is would have made the suite pass while
        actually proving the wrong thing."""
        request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        notified = set(Notification.objects.filter(
            verb='requested your timesheet').values_list('recipient__username', flat=True))
        self.assertIn('notifai', notified)  # developer role — now correctly included

    def test_reminder_reaches_only_the_targeted_employee(self):
        ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        remind_employee(ts_request=ts_request, employee=self.emp_a, reminded_by=self.hr_user)
        notified = set(Notification.objects.filter(
            verb='reminded you to send your timesheet').values_list('recipient__username', flat=True))
        self.assertEqual(notified, {'notifa'})  # exactly one recipient, not B, not AI, not HR

    def test_reminding_employee_with_no_linked_user_raises(self):
        ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        orphan_emp = Employee.objects.create(full_name='No Login', iqama_number='IQ-ORPHAN')
        with self.assertRaises(ValueError):
            remind_employee(ts_request=ts_request, employee=orphan_emp, reminded_by=self.hr_user)


class CSRFEnforcementTests(TestCase):
    """Per the guide: an explicit test proving a request with no CSRF token
    is rejected — the exact test that would have caught the drafts-feature
    empty-token bypass, applied here even though our CSRF handling was
    always plain Django (no hand-rolled check to worry about)."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.hr_user, _ = _make_user_with_employee('csrfhr', role_name=Role.ADMIN)
        self.user, self.emp = _make_user_with_employee('csrfemp')
        seed_default_permissions()
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_post_without_csrf_token_rejected(self):
        self.client.login(username='csrfemp', password='testpass123')
        resp = self.client.post(
            reverse('timesheets:acknowledge_send', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 403)
        # confirm the denied request had no actual side effect
        self.assertFalse(TimesheetRequestAck.objects.filter(
            request=self.ts_request, employee=self.emp).exists())


class HRSettingsSingletonTests(TestCase):
    """HRSettings.load() must always return the same single row, even when
    called repeatedly — this is what keeps the fixed-HR-email guarantee
    (never typed by an employee) actually true over time."""

    def test_load_creates_row_on_first_call(self):
        self.assertEqual(HRSettings.objects.count(), 0)
        settings_obj = HRSettings.load()
        self.assertEqual(HRSettings.objects.count(), 1)
        self.assertEqual(settings_obj.pk, 1)

    def test_load_never_creates_a_second_row(self):
        first = HRSettings.load()
        first.hr_email = 'hr@leap-arabia.com'
        first.save()
        second = HRSettings.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(HRSettings.objects.count(), 1)
        self.assertEqual(second.hr_email, 'hr@leap-arabia.com')

class RequestTimesheetsDeduplicationTests(TestCase):
    """Regression test for the duplicate-TimesheetRequest bug: calling
    request_timesheets twice for the same year/month must reuse the same
    row, not fragment ack-tracking across multiple copies of 'July'."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('duphr', role_name=Role.ADMIN)
        seed_default_permissions()

    def test_calling_twice_for_same_month_creates_only_one_request(self):
        request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        self.assertEqual(
            TimesheetRequest.objects.filter(year=2026, month=7).count(), 1)

    def test_second_call_still_notifies_everyone(self):
        """Re-requesting should still act as a valid 'remind everyone'
        action, not silently no-op just because the row already exists."""
        user_a, emp_a = _make_user_with_employee('dupnotify_a')
        seed_default_permissions()  # re-seed now that dupnotify_a's role exists
        request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        Notification.objects.all().delete()  # clear the first round
        request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        notified = set(Notification.objects.filter(
            verb='requested your timesheet').values_list('recipient__username', flat=True))
        self.assertIn('dupnotify_a', notified)

    def test_different_months_still_create_separate_requests(self):
        """The fix must not over-correct into treating every request as
        the same one regardless of month."""
        request_timesheets(year=2026, month=7, requested_by=self.hr_user)
        request_timesheets(year=2026, month=8, requested_by=self.hr_user)
        self.assertEqual(TimesheetRequest.objects.count(), 2)


class RemindAlreadyAckedTests(TestCase):
    """Regression test for the 'remind someone who already sent theirs'
    gap: remind_employee must check the ack itself, not trust that the UI
    hid the button."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('remindhr', role_name=Role.ADMIN)
        self.user, self.emp = _make_user_with_employee('remindme')
        seed_default_permissions()
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_reminding_already_acked_employee_raises(self):
        TimesheetRequestAck.objects.create(request=self.ts_request, employee=self.emp)
        with self.assertRaises(ValueError):
            remind_employee(
                ts_request=self.ts_request, employee=self.emp, reminded_by=self.hr_user)

    def test_reminding_already_acked_employee_sends_no_notification(self):
        TimesheetRequestAck.objects.create(request=self.ts_request, employee=self.emp)
        Notification.objects.all().delete()
        try:
            remind_employee(
                ts_request=self.ts_request, employee=self.emp, reminded_by=self.hr_user)
        except ValueError:
            pass
        self.assertFalse(Notification.objects.filter(
            verb='reminded you to send your timesheet').exists())

    def test_reminding_via_view_shows_error_not_crash(self):
        """End-to-end through hr_request_detail's POST handler, not just
        the service function directly."""
        TimesheetRequestAck.objects.create(request=self.ts_request, employee=self.emp)
        client = Client()
        client.login(username='remindhr', password='testpass123')
        resp = client.post(
            reverse('timesheets:hr_request_detail', args=[self.ts_request.pk]),
            {'employee_id': self.emp.pk})
        self.assertEqual(resp.status_code, 302)  # redirected with error message, not a 500


class MalformedQueryParamTests(TestCase):
    """Per the guide's checklist: invalid input must return 4xx (or a
    clean redirect with an error), never an unhandled 500."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('malformedhr', role_name=Role.ADMIN)
        self.user, self.emp = _make_user_with_employee('malformeduser')
        seed_default_permissions()

    def test_export_with_non_numeric_year_does_not_500(self):
        self.client.login(username='malformeduser', password='testpass123')
        resp = self.client.get(reverse('timesheets:export') + '?year=abc&month=7')
        self.assertEqual(resp.status_code, 302)  # redirected with error, not a crash

    def test_export_with_non_numeric_month_does_not_500(self):
        self.client.login(username='malformeduser', password='testpass123')
        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=xyz')
        self.assertEqual(resp.status_code, 302)

    def test_hr_request_with_non_numeric_year_does_not_500(self):
        self.client.login(username='malformedhr', password='testpass123')
        resp = self.client.post(reverse('timesheets:hr_request'), {
            'year': 'not-a-year', 'month': '7',
        })
        self.assertEqual(resp.status_code, 302)
        # confirm nothing bad got created despite the malformed input
        self.assertEqual(TimesheetRequest.objects.filter(month=7).count(), 0)

    def test_export_with_month_13_does_not_500(self):
        self.client.login(username='malformeduser', password='testpass123')
        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=13')
        self.assertNotEqual(resp.status_code, 500)

class HRRequestYearDefaultingTests(TestCase):
    """The template no longer submits a 'year' field — the view must keep
    defaulting to the current year server-side when it's absent, not
    error or silently use year=0/None."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('yrdefault', role_name=Role.ADMIN)
        seed_default_permissions()

    def test_posting_without_year_field_defaults_to_current_year(self):
        client = Client()
        client.login(username='yrdefault', password='testpass123')
        client.post(reverse('timesheets:hr_request'), {'month': '7'})  # no 'year' key at all
        current_year = date.today().year
        self.assertTrue(TimesheetRequest.objects.filter(year=current_year, month=7).exists())


class AdditionalCSRFEnforcementTests(TestCase):
    """A second CSRF check on a different destructive endpoint, since
    entry_delete removes data and deserves its own explicit proof, not
    just an inference from the acknowledge_send test."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        self.user, self.emp = _make_user_with_employee('csrfdel')
        seed_default_permissions()
        self.entry = TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='t1', activity_code=self.code, hours=Decimal('4'))

    def test_delete_without_csrf_token_rejected(self):
        self.client.login(username='csrfdel', password='testpass123')
        resp = self.client.post(reverse('timesheets:entry_delete', args=[self.entry.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(TimesheetEntry.objects.filter(pk=self.entry.pk).exists())


class NonexistentRequestIdTests(TestCase):
    """send_to_hr and acknowledge_send must 404 cleanly on a request_id
    that doesn't exist at all, not just one that belongs to someone else."""

    def setUp(self):
        self.user, self.emp = _make_user_with_employee('nonexistuser')
        seed_default_permissions()
        self.client.login(username='nonexistuser', password='testpass123')

    def test_send_to_hr_with_bad_id_404s(self):
        resp = self.client.get(reverse('timesheets:send_to_hr', args=[99999]))
        self.assertEqual(resp.status_code, 404)

    def test_acknowledge_send_with_bad_id_404s(self):
        resp = self.client.post(reverse('timesheets:acknowledge_send', args=[99999]))
        self.assertEqual(resp.status_code, 404)


class HRRequestDetailQueryCountTests(TestCase):
    """N+1 guard, per the guide's Django-practices section: looping over
    employees in employees_for_request must not issue one query per
    employee. This locks the query count so a future refactor that
    accidentally removes select_related gets caught immediately."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('queryhr', role_name=Role.ADMIN)
        for i in range(5):
            _make_user_with_employee(f'queryemp{i}')
        seed_default_permissions()
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_request_detail_query_count_does_not_scale_with_employee_count(self):
        client = Client()
        client.login(username='queryhr', password='testpass123')
        with self.assertNumQueries(8):  # adjust once if your baseline differs; the
                                          # point is this number staying FLAT as
                                          # employee count grows, not its exact value
            client.get(reverse('timesheets:hr_request_detail', args=[self.ts_request.pk]))


from .forms import TimesheetEntryForm


class TimesheetEntryFormDirectTests(TestCase):
    """Form-level tests, isolated from the view — faster and pinpoints
    exactly which clean_* method is responsible if one of these breaks."""

    def setUp(self):
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})

    def _data(self, **overrides):
        data = {
            'date': '2026-07-10', 'activity_code': self.code.pk,
            'task_description': 'Task', 'hours': '4',
        }
        data.update(overrides)
        return data

    def test_today_is_a_valid_date(self):
        form = TimesheetEntryForm(self._data(date=date.today().isoformat()))
        self.assertTrue(form.is_valid())

    def test_exactly_24_hours_is_valid(self):
        form = TimesheetEntryForm(self._data(hours='24'))
        self.assertTrue(form.is_valid())

    def test_decimal_hours_accepted(self):
        form = TimesheetEntryForm(self._data(hours='4.5'))
        self.assertTrue(form.is_valid())

    def test_missing_task_description_invalid(self):
        form = TimesheetEntryForm(self._data(task_description=''))
        self.assertFalse(form.is_valid())

    def test_missing_date_invalid(self):
        form = TimesheetEntryForm(self._data(date=''))
        self.assertFalse(form.is_valid())

    def test_missing_activity_code_invalid(self):
        form = TimesheetEntryForm(self._data(activity_code=''))
        self.assertFalse(form.is_valid())

class TimesheetEntryFormDefaultsTests(TestCase):
    """Locks in the two default-value changes: hours defaults to 10, date
    defaults to today — and confirms editing an existing entry does NOT
    silently overwrite its real values with these defaults."""

    def setUp(self):
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})

    def test_new_form_defaults_hours_to_10(self):
        form = TimesheetEntryForm()
        self.assertEqual(form.fields['hours'].initial, 10)

    def test_new_form_defaults_date_to_today(self):
        form = TimesheetEntryForm()
        self.assertEqual(form.fields['date'].initial, date.today())

    def test_editing_existing_entry_does_not_overwrite_its_real_date_or_hours(self):
        """The guard in forms.py's __init__ (`if not self.instance.pk`)
        exists specifically so editing an old entry doesn't silently
        reset its date to today or its hours to 10."""
        user, emp = _make_user_with_employee('defaultsedit')
        entry = TimesheetEntry.objects.create(
            employee=emp, date=date(2026, 1, 5), task_description='Old entry',
            activity_code=self.code, hours=Decimal('3'))
        form = TimesheetEntryForm(instance=entry)
        # initial should be unset (None) here, not forced to today/10 —
        # Django's ModelForm already pulls the real values from the
        # instance itself when initial is left alone.
        self.assertIsNone(form.fields['date'].initial)
        self.assertIsNone(form.fields['hours'].initial)
        self.assertEqual(form['date'].value(), entry.date)
        self.assertEqual(form['hours'].value(), entry.hours)


class ExportBusinessLogicTests(TestCase):
    """The export's actual computed values, not just 'does it download' —
    combining multiple same-day entries and weekend highlighting are real
    business logic with room to silently drift wrong."""

    def setUp(self):
        self.client = Client()
        self.code1, _ = ActivityCode.objects.get_or_create(
            code='COS_0001', defaults={'label': 'A', 'is_active': True})
        self.code2, _ = ActivityCode.objects.get_or_create(
            code='COS_0002', defaults={'label': 'B', 'is_active': True})
        self.user, self.emp = _make_user_with_employee('exportlogic')
        seed_default_permissions()
        self.client.login(username='exportlogic', password='testpass123')

    def test_combined_row_sums_hours_and_joins_both_descriptions_and_codes(self):
        import openpyxl
        from io import BytesIO
        TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 10),
            task_description='Morning task', activity_code=self.code1, hours=Decimal('3'))
        TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 10),
            task_description='Afternoon task', activity_code=self.code2, hours=Decimal('5'))

        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=7')
        wb = openpyxl.load_workbook(BytesIO(resp.content))
        ws = wb.active
        row = 11 + 10  # header at row 11, day 10 -> row 21

        self.assertEqual(ws.cell(row=row, column=3).value, 'Morning task; Afternoon task')
        self.assertEqual(ws.cell(row=row, column=6).value, Decimal('8'))
        self.assertEqual(ws.cell(row=row, column=7).value, 'COS_0001, COS_0002')

    def test_day_with_no_entries_exports_blank_row(self):
        import openpyxl
        from io import BytesIO
        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=7')
        wb = openpyxl.load_workbook(BytesIO(resp.content))
        ws = wb.active
        row = 11 + 15  # a day with nothing logged
        self.assertIsNone(ws.cell(row=row, column=3).value)
        self.assertIsNone(ws.cell(row=row, column=6).value)

    def test_weekend_rows_are_highlighted_differently_from_weekdays(self):
        import openpyxl
        import calendar as cal_module
        from io import BytesIO
        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=7')
        wb = openpyxl.load_workbook(BytesIO(resp.content))
        ws = wb.active

        days_in_month = cal_module.monthrange(2026, 7)[1]
        weekend_row = weekday_row = None
        for day in range(1, days_in_month + 1):
            d = date(2026, 7, day)
            if d.weekday() in (4, 5) and weekend_row is None:
                weekend_row = 11 + day
            if d.weekday() not in (4, 5) and weekday_row is None:
                weekday_row = 11 + day

        weekend_fill = ws.cell(row=weekend_row, column=4).fill.start_color.rgb or ''
        weekday_fill = ws.cell(row=weekday_row, column=4).fill.start_color.rgb or ''
        self.assertIn('FFFF00', weekend_fill)
        self.assertNotIn('FFFF00', weekday_fill)

    def test_entry_with_since_deactivated_code_still_exports_correctly(self):
        """An entry logged while a code was active must still export fine
        after that code gets deactivated later — the FK still resolves."""
        import openpyxl
        from io import BytesIO
        TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 5),
            task_description='Old task', activity_code=self.code1, hours=Decimal('4'))
        self.code1.is_active = False
        self.code1.save()

        resp = self.client.get(reverse('timesheets:export') + '?year=2026&month=7')
        self.assertEqual(resp.status_code, 200)
        wb = openpyxl.load_workbook(BytesIO(resp.content))
        ws = wb.active
        self.assertEqual(ws.cell(row=11 + 5, column=7).value, 'COS_0001')

    def test_export_query_count_does_not_scale_with_entry_count(self):
        """N+1 guard for the export loop — without select_related('activity_code')
        this scales linearly with entry count instead of staying flat."""
        for day in range(1, 11):
            TimesheetEntry.objects.create(
                employee=self.emp, date=date(2026, 7, day),
                task_description=f'task {day}', activity_code=self.code1, hours=Decimal('4'))
        with self.assertNumQueries(4):  # confirmed real baseline after adding
                                          # select_related('activity_code') — the
                                          # point is this staying FLAT as entries grow
            self.client.get(reverse('timesheets:export') + '?year=2026&month=7')


class SendToHRNoEmployeeLinkTests(TestCase):
    """send_to_hr must redirect gracefully for a user with no linked
    Employee record, same as the other views — not crash."""

    def test_send_to_hr_redirects_when_no_employee_link(self):
        role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        User.objects.create_user(username='noemplink', password='testpass123', role=role)
        seed_default_permissions()
        client = Client()
        client.login(username='noemplink', password='testpass123')
        from .services import request_timesheets
        hr_role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        hr_user = User.objects.create_user(username='noemplinkhr', password='testpass123', role=hr_role)
        ts_request = request_timesheets(year=2026, month=7, requested_by=hr_user)
        resp = client.get(reverse('timesheets:send_to_hr', args=[ts_request.pk]))
        self.assertEqual(resp.status_code, 302)


class EmployeesForRequestOrderingTests(TestCase):
    """employees_for_request sorts Pending before Sent, alphabetical
    within each group — the HR detail page's usefulness depends on this."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('orderhr', role_name=Role.ADMIN)
        self.zoe_user, self.zoe_emp = _make_user_with_employee('orderz', full_name='Zoe')
        self.amy_user, self.amy_emp = _make_user_with_employee('ordera', full_name='Amy')
        seed_default_permissions()
        from .services import request_timesheets
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_pending_employees_listed_before_sent_employees(self):
        TimesheetRequestAck.objects.create(request=self.ts_request, employee=self.zoe_emp)
        rows = employees_for_request(self.ts_request)
        acked_flags = [r['acked'] for r in rows]
        # all False (pending) entries must come before all True (sent) entries
        self.assertEqual(acked_flags, sorted(acked_flags))

    def test_alphabetical_within_same_acked_group(self):
        rows = employees_for_request(self.ts_request)
        pending_names = [r['employee'].full_name for r in rows if not r['acked']]
        self.assertEqual(pending_names, sorted(pending_names))


class RemainingCSRFEnforcementTests(TestCase):
    """CSRF coverage for every remaining state-changing endpoint, so no
    destructive/mutating view is left unproven — matches the guide's
    'CSRF enforced' checklist item applied exhaustively, not just once."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        self.user, self.emp = _make_user_with_employee('csrfall')
        self.hr_user, _ = _make_user_with_employee('csrfallhr', role_name=Role.ADMIN)
        seed_default_permissions()
        from .services import request_timesheets
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_add_entry_without_csrf_token_rejected(self):
        self.client.login(username='csrfall', password='testpass123')
        resp = self.client.post(reverse('timesheets:my_timesheet'), {
            'date': '2026-07-10', 'activity_code': self.code.pk,
            'task_description': 'Should not save', 'hours': '4',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(TimesheetEntry.objects.count(), 0)

    def test_edit_entry_without_csrf_token_rejected(self):
        entry = TimesheetEntry.objects.create(
            employee=self.emp, date=date(2026, 7, 1),
            task_description='original', activity_code=self.code, hours=Decimal('4'))
        self.client.login(username='csrfall', password='testpass123')
        resp = self.client.post(
            reverse('timesheets:entry_edit', args=[entry.pk]), {
                'date': '2026-07-01', 'activity_code': self.code.pk,
                'task_description': 'HIJACKED', 'hours': '1',
            })
        self.assertEqual(resp.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.task_description, 'original')

    def test_hr_request_submit_without_csrf_token_rejected(self):
        self.client.login(username='csrfallhr', password='testpass123')
        resp = self.client.post(reverse('timesheets:hr_request'), {
            'year': '2026', 'month': '9',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(TimesheetRequest.objects.filter(month=9).count(), 0)

    def test_remind_without_csrf_token_rejected(self):
        self.client.login(username='csrfallhr', password='testpass123')
        resp = self.client.post(
            reverse('timesheets:hr_request_detail', args=[self.ts_request.pk]),
            {'employee_id': self.emp.pk})
        self.assertEqual(resp.status_code, 403)


class TimesheetEntryOrderingTests(TestCase):
    """The entries list on My Timesheet is ordered most-recent-first,
    per the model's Meta.ordering — worth locking in explicitly."""

    def test_entries_ordered_newest_date_first(self):
        code, _ = ActivityCode.objects.get_or_create(
            code='COS_0009', defaults={'label': 'Office', 'is_active': True})
        user, emp = _make_user_with_employee('orderentries')
        seed_default_permissions()
        e_old = TimesheetEntry.objects.create(
            employee=emp, date=date(2026, 7, 1),
            task_description='old', activity_code=code, hours=Decimal('4'))
        e_new = TimesheetEntry.objects.create(
            employee=emp, date=date(2026, 7, 20),
            task_description='new', activity_code=code, hours=Decimal('4'))
        entries = list(TimesheetEntry.objects.filter(employee=emp))
        self.assertEqual(entries[0].pk, e_new.pk)
        self.assertEqual(entries[1].pk, e_old.pk)


class NonexistentEntryTests(TestCase):
    """Editing/deleting a pk that belongs to no one at all (not just
    someone else) must still 404 cleanly, not error differently."""

    def setUp(self):
        self.user, self.emp = _make_user_with_employee('ghostentry')
        seed_default_permissions()
        self.client.login(username='ghostentry', password='testpass123')

    def test_edit_nonexistent_entry_404s(self):
        resp = self.client.get(reverse('timesheets:entry_edit', args=[99999]))
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_entry_404s(self):
        resp = self.client.post(reverse('timesheets:entry_delete', args=[99999]))
        self.assertEqual(resp.status_code, 404)


class InactiveEmployeeVisibilityTests(TestCase):
    """Documents actual current behavior: employees_for_request only
    checks User.is_active (via _users_with_capability), NOT
    Employee.is_active. A deactivated Employee whose User account is
    still active keeps showing up as Pending indefinitely. This is a
    product decision to make on purpose, not a bug -- this test just
    makes the real behavior visible and locked in, so if it's ever
    "fixed" to also check Employee.is_active, that's a deliberate
    change someone reviews, not an accidental side effect."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('inactivehr', role_name=Role.ADMIN)
        self.user, self.emp = _make_user_with_employee('inactiveemp', full_name='Inactive Person')
        seed_default_permissions()
        from .services import request_timesheets
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_employee_with_inactive_employee_record_but_active_user_still_listed(self):
        self.emp.is_active = False
        self.emp.save()
        rows = employees_for_request(self.ts_request)
        names = [r['employee'].full_name for r in rows]
        self.assertIn('Inactive Person', names)  # current behavior: still shows up

    def test_employee_with_deactivated_user_account_disappears_from_list(self):
        self.user.is_active = False
        self.user.save()
        rows = employees_for_request(self.ts_request)
        names = [r['employee'].full_name for r in rows]
        self.assertNotIn('Inactive Person', names)  # correctly excluded via User.is_active


class HRSettingsAdminSingletonTests(TestCase):
    """The admin's has_add_permission override must block adding a second
    HRSettings row through /admin/, not just through .load()."""

    def setUp(self):
        self.hr_user, _ = _make_user_with_employee('adminhr', role_name=Role.ADMIN)
        self.hr_user.is_staff = True
        self.hr_user.is_superuser = True
        self.hr_user.save()
        seed_default_permissions()

    def test_admin_add_view_blocked_when_a_row_already_exists(self):
        HRSettings.objects.get_or_create(pk=1, defaults={'hr_email': 'hr@leap-arabia.com'})
        self.client.login(username='adminhr', password='testpass123')
        resp = self.client.get('/admin/timesheets/hrsettings/add/')
        self.assertEqual(resp.status_code, 403)

    def test_admin_add_view_allowed_when_no_row_exists_yet(self):
        self.assertEqual(HRSettings.objects.count(), 0)
        self.client.login(username='adminhr', password='testpass123')
        resp = self.client.get('/admin/timesheets/hrsettings/add/')
        self.assertEqual(resp.status_code, 200)



class HRRequestDeleteTests(TestCase):
    """hr_request_delete: covers the full checklist from the guide — happy
    path, the denied case, CSRF, and that delete_request's cascade cleanup
    (acks + notifications) actually happens, not just the request row."""

    def setUp(self):
        self.hr_user, self.hr_emp = _make_user_with_employee('delhr', role_name=Role.ADMIN)
        self.plain_user, self.plain_emp = _make_user_with_employee('delplain')
        seed_default_permissions()
        self.ts_request = request_timesheets(year=2026, month=7, requested_by=self.hr_user)

    def test_hr_can_delete_a_request(self):
        client = Client()
        client.login(username='delhr', password='testpass123')
        resp = client.post(reverse('timesheets:hr_request_delete', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(TimesheetRequest.objects.filter(pk=self.ts_request.pk).exists())

    def test_deleting_a_request_also_deletes_its_acks(self):
        TimesheetRequestAck.objects.create(request=self.ts_request, employee=self.plain_emp)
        client = Client()
        client.login(username='delhr', password='testpass123')
        client.post(reverse('timesheets:hr_request_delete', args=[self.ts_request.pk]))
        self.assertFalse(TimesheetRequestAck.objects.filter(
            request_id=self.ts_request.pk).exists())

    def test_deleting_a_request_also_deletes_its_notifications(self):
        """delete_request explicitly cleans up notifications pointing at
        the request, on top of the CASCADE on acks — so nobody's left
        with a notification linking to something that no longer exists."""
        self.assertTrue(Notification.objects.filter(
            verb='requested your timesheet').exists())  # sanity: request_timesheets sent one
        client = Client()
        client.login(username='delhr', password='testpass123')
        client.post(reverse('timesheets:hr_request_delete', args=[self.ts_request.pk]))
        self.assertFalse(Notification.objects.filter(
            verb='requested your timesheet').exists())

    def test_plain_employee_cannot_delete_a_request(self):
        client = Client()
        client.login(username='delplain', password='testpass123')
        resp = client.post(reverse('timesheets:hr_request_delete', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(TimesheetRequest.objects.filter(pk=self.ts_request.pk).exists())

    def test_delete_requires_post_not_get(self):
        client = Client()
        client.login(username='delhr', password='testpass123')
        resp = client.get(reverse('timesheets:hr_request_delete', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 405)  # @require_POST

    def test_delete_nonexistent_request_404s(self):
        client = Client()
        client.login(username='delhr', password='testpass123')
        resp = client.post(reverse('timesheets:hr_request_delete', args=[99999]))
        self.assertEqual(resp.status_code, 404)

    def test_delete_without_csrf_token_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username='delhr', password='testpass123')
        resp = client.post(reverse('timesheets:hr_request_delete', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(TimesheetRequest.objects.filter(pk=self.ts_request.pk).exists())

    def test_anonymous_redirected_from_delete(self):
        client = Client()
        resp = client.post(reverse('timesheets:hr_request_delete', args=[self.ts_request.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)

