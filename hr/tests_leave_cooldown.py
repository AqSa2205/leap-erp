"""Tests for the post-vacation leave cooldown feature (hr.leave_cooldown),
and its two hookups in hr.views.my_profile: the blocking check on
submission, and the context/banner data for the disclaimer.

Covers the three business rules exactly as specified:
  1) Annual leave under 10 days -> can apply again 1 month after it ended.
  2) Annual leave 10+ days (but not the full entitlement) -> 3 months.
  3) Full entitlement used up in the year -> blocked until the employee's
     next joining-date anniversary (or 1 Jan next year if joining_date
     isn't set) - whether that happened in one booking or several smaller
     ones that add up.

Also confirms the feature never touches any other leave type, and that a
blocked self-service submission is actually rejected end-to-end (no
LeaveRequest row created), not just flagged by the pure function.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from hr.models import Employee, LeaveType, LeaveEntitlement, LeaveExceptionGrant, LeaveRecord, LeaveRequest
from hr.leave_cooldown import annual_leave_cooldown

User = get_user_model()


def make_employee(iqama, name, joining=None, work_location='office'):
    return Employee.objects.create(
        iqama_number=iqama, full_name=name, joining_date=joining, work_location=work_location, is_active=True)


def make_leave_type(code='annual', name='Annual', days=Decimal('30'), accumulative=True):
    lt, _ = LeaveType.objects.update_or_create(
        code=code, defaults={'name': name, 'default_annual_days': days, 'is_accumulative': accumulative})
    return lt


class ShortLeaveCooldownTests(TestCase):
    """Rule 1: under 10 days -> 1 month."""

    def setUp(self):
        self.emp = make_employee('CD-001', 'Short Leave Employee')
        self.annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=Decimal('30'))

    def test_blocked_the_day_after_returning(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 3, 1), end_date=date(2026, 3, 5))
        result = annual_leave_cooldown(self.emp, self.annual, today=date(2026, 3, 6))
        self.assertIsNotNone(result)
        self.assertEqual(result['eligible_date'], date(2026, 4, 5))
        self.assertEqual(result['days_remaining'], 30)

    def test_free_again_exactly_on_eligible_date(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 3, 1), end_date=date(2026, 3, 5))
        self.assertIsNone(annual_leave_cooldown(self.emp, self.annual, today=date(2026, 4, 5)))

    def test_still_blocked_one_day_before_eligible(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 3, 1), end_date=date(2026, 3, 5))
        self.assertIsNotNone(annual_leave_cooldown(self.emp, self.annual, today=date(2026, 4, 4)))


class LongLeaveCooldownTests(TestCase):
    """Rule 2: 10+ days (but not the full entitlement) -> 3 months."""

    def setUp(self):
        self.emp = make_employee('CD-002', 'Long Leave Employee')
        self.annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=Decimal('30'))

    def test_exactly_10_days_gets_the_3_month_tier(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 3, 1), end_date=date(2026, 3, 10))
        result = annual_leave_cooldown(self.emp, self.annual, today=date(2026, 3, 11))
        self.assertEqual(result['eligible_date'], date(2026, 6, 10))

    def test_15_days_gets_3_months_not_1(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 3, 1), end_date=date(2026, 3, 15))
        result = annual_leave_cooldown(self.emp, self.annual, today=date(2026, 3, 16))
        self.assertEqual(result['eligible_date'], date(2026, 6, 15))
        self.assertIn('10 days or more', result['reason'])


class FullEntitlementSingleBookingTests(TestCase):
    """Rule 3: the whole entitlement used in ONE booking."""

    def test_joining_anniversary_used_when_set(self):
        emp = make_employee('CD-003', 'Anniversary Employee', joining=date(2022, 3, 15))
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('30'))
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), days=Decimal('30'))
        result = annual_leave_cooldown(emp, annual, today=date(2026, 8, 25))
        # 15 Mar 2026 already passed relative to the 30 Jun end date -> next occurrence is 2027.
        self.assertEqual(result['eligible_date'], date(2027, 3, 15))

    def test_fallback_to_next_jan_1_when_no_joining_date(self):
        emp = make_employee('CD-004', 'Fallback Employee', joining=None)
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('30'))
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 5, 1), end_date=date(2026, 5, 30), days=Decimal('30'))
        result = annual_leave_cooldown(emp, annual, today=date(2026, 8, 25))
        self.assertEqual(result['eligible_date'], date(2027, 1, 1))

    def test_anniversary_not_yet_passed_this_year_is_used_as_is(self):
        # Joining anniversary is 1 Dec; leave ends 30 Jun -> 1 Dec of the SAME year is still ahead.
        emp = make_employee('CD-005', 'Same Year Anniversary', joining=date(2020, 12, 1))
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('30'))
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), days=Decimal('30'))
        result = annual_leave_cooldown(emp, annual, today=date(2026, 8, 25))
        self.assertEqual(result['eligible_date'], date(2026, 12, 1))

    def test_29_feb_joining_date_falls_back_to_28_feb_in_a_non_leap_year(self):
        """_next_joining_anniversary has an except ValueError path for
        exactly this: a joining_date of 29 Feb can't be replicated onto a
        non-leap year's calendar, so it must fall back to 28 Feb rather
        than raising and 500ing the page."""
        emp = make_employee('CD-016', 'Leap Day Joiner', joining=date(2020, 2, 29))  # 2020 is a leap year
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('30'))
        # 2026 and 2027 are both non-leap years.
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), days=Decimal('30'))
        result = annual_leave_cooldown(emp, annual, today=date(2026, 8, 25))
        # 28 Feb 2026 (this year's fallback anniversary) already passed by the
        # time the leave ended in June -> rolls to 28 Feb 2027.
        self.assertEqual(result['eligible_date'], date(2027, 2, 28))


class YearBoundaryFallbackTests(TestCase):
    """The no-joining_date fallback must anchor to the entitlement's own
    year (last_record.start_date.year), not last_record.end_date.year -
    those differ for a leave that spans a year boundary, which the
    self-service form blocks but a direct service call (e.g. an admin
    script) does not."""

    def test_fallback_uses_entitlement_year_not_end_date_year(self):
        emp = make_employee('CD-011', 'Year Boundary Employee', joining=None)
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('5'))
        # Spans the year boundary: starts in 2026, ends in 2027.
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 12, 29), end_date=date(2027, 1, 2), days=Decimal('5'))
        # The 2026 entitlement renews 1 Jan 2027, which has ALREADY passed by
        # the time this leave itself ends (2 Jan 2027) -> free immediately on
        # return. The old buggy version anchored to end_date.year + 1 =
        # 2028-01-01 instead, which would still show them blocked here — this
        # is exactly the regression this test catches.
        self.assertIsNone(annual_leave_cooldown(emp, annual, today=date(2027, 1, 2)))


class ExceptionDaysTests(TestCase):
    """used_full_entitlement must compare against effective_entitled_days
    (base + any HR-granted LeaveExceptionGrant), matching how "full
    balance" is defined everywhere else in this codebase
    (validate_leave_submission / effective_remaining_days) - not the bare
    entitled_days, which would ignore exception days HR already granted."""

    def test_exception_days_prevent_a_false_rule_3(self):
        emp = make_employee('CD-012', 'Exception Grant Employee', joining=None)
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('30'))
        LeaveExceptionGrant.objects.create(
            employee=emp, leave_type=annual, year=2026, days=Decimal('10'), reason='Worked through a holiday.')
        # Took exactly the base 30 days -> under the 40-day effective total,
        # so this must NOT be treated as "used the full entitlement".
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), days=Decimal('30'))
        result = annual_leave_cooldown(emp, annual, today=date(2026, 7, 1))
        # Falls through to the ordinary 3-month (10+ days) rule instead.
        self.assertEqual(result['eligible_date'], date(2026, 9, 30))
        self.assertNotIn('full', result['reason'])

    def test_exception_days_included_when_truly_exhausted(self):
        emp = make_employee('CD-013', 'Exception Grant Exhausted Employee', joining=None)
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('30'))
        LeaveExceptionGrant.objects.create(
            employee=emp, leave_type=annual, year=2026, days=Decimal('10'), reason='Worked through a holiday.')
        # Takes the full effective 40 (30 base + 10 exception) -> Rule 3 DOES apply.
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 5, 1), end_date=date(2026, 6, 9), days=Decimal('40'))
        result = annual_leave_cooldown(emp, annual, today=date(2026, 7, 1))
        self.assertEqual(result['eligible_date'], date(2027, 1, 1))
        self.assertIn('full 40', result['reason'])


class FullEntitlementCumulativeTests(TestCase):
    """Rule 3 must also fire when the entitlement is exhausted gradually
    across several separate bookings, not only in a single one."""

    def setUp(self):
        self.emp = make_employee('CD-006', 'Cumulative Employee', joining=None)
        self.annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=Decimal('30'))

    def test_two_bookings_of_10_still_get_the_ordinary_3_month_rule(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 2, 1), end_date=date(2026, 2, 10))
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 5, 1), end_date=date(2026, 5, 10))
        result = annual_leave_cooldown(self.emp, self.annual, today=date(2026, 5, 11))
        self.assertEqual(result['eligible_date'], date(2026, 8, 10))
        self.assertNotIn('full', result['reason'])

    def test_third_booking_tips_the_cumulative_total_into_rule_3(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 2, 1), end_date=date(2026, 2, 10))
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 5, 1), end_date=date(2026, 5, 10))
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 8, 1), end_date=date(2026, 8, 10))
        result = annual_leave_cooldown(self.emp, self.annual, today=date(2026, 8, 15))
        # Cumulative total is now 30/30 -> Rule 3, not another 3-month Rule 2 window.
        self.assertEqual(result['eligible_date'], date(2027, 1, 1))
        self.assertIn('full 30', result['reason'])

    def test_partial_cumulative_total_stays_on_rule_2(self):
        # Two 10-day bookings = 20/30 -> nowhere near the full entitlement yet.
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 2, 1), end_date=date(2026, 2, 10))
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=date(2026, 5, 1), end_date=date(2026, 5, 10))
        result = annual_leave_cooldown(self.emp, self.annual, today=date(2026, 5, 11))
        self.assertNotIn('full', result['reason'])


class NoCooldownEdgeCaseTests(TestCase):

    def test_no_leave_ever_taken_is_free(self):
        emp = make_employee('CD-007', 'Never Taken Employee')
        annual = make_leave_type()
        self.assertIsNone(annual_leave_cooldown(emp, annual, today=date(2026, 8, 25)))

    def test_no_entitlement_row_falls_back_to_day_count_without_crashing(self):
        """A LeaveRecord can exist with no matching LeaveEntitlement row for
        that year (e.g. HR hasn't generated entitlements yet) - Rule 3 must
        never fire (nothing to compare "full" against), and the day-count
        tiers (Rule 1 / Rule 2) still work off last_leave_days alone."""
        emp = make_employee('CD-015', 'No Entitlement Row Employee')
        annual = make_leave_type()
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 3, 1), end_date=date(2026, 3, 15), days=Decimal('15'))
        result = annual_leave_cooldown(emp, annual, today=date(2026, 3, 16))
        self.assertIsNotNone(result)
        self.assertEqual(result['eligible_date'], date(2026, 6, 15))  # ordinary 3-month Rule 2
        self.assertNotIn('full', result['reason'])

    def test_non_accumulative_leave_type_is_never_blocked(self):
        """Sick/Marriage/etc (is_accumulative=False) must never be affected,
        no matter how long the leave was or whether it exhausted its own
        entitlement — this rule is Annual-only by design."""
        emp = make_employee('CD-008', 'Sick Leave Employee')
        sick = make_leave_type(code='sick', name='Sick', days=Decimal('12'), accumulative=False)
        LeaveEntitlement.objects.create(employee=emp, leave_type=sick, year=2026, entitled_days=Decimal('12'))
        LeaveRecord.objects.create(
            employee=emp, leave_type=sick, start_date=date(2026, 3, 1), end_date=date(2026, 3, 12), days=Decimal('12'))
        self.assertIsNone(annual_leave_cooldown(emp, sick, today=date(2026, 3, 13)))

    def test_future_leave_is_ignored_until_it_actually_ends(self):
        emp = make_employee('CD-009', 'Future Leave Employee')
        annual = make_leave_type()
        LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=Decimal('30'))
        LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=date(2026, 12, 1), end_date=date(2026, 12, 5))
        self.assertIsNone(annual_leave_cooldown(emp, annual, today=date(2026, 8, 25)))


class MyProfileSubmissionBlockedTests(TestCase):
    """End-to-end: a blocked employee's actual POST to My Profile must be
    rejected, and no LeaveRequest row created — the pure function alone
    isn't enough proof; the view hookup has to actually enforce it."""

    def setUp(self):
        self.user = User.objects.create_user(username='cooldown.demo', password='pw12345!')
        self.emp = make_employee('CD-010', 'Blocked Submitter', joining=None)
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.annual = make_leave_type()
        # Dates anchored to date.today() rather than a hardcoded year, so this
        # test's cooldown window (15 days -> 3 months, Rule 2) reliably still
        # covers "now" no matter when the suite is actually run — a fixed
        # 2026 date would silently stop testing the blocked path, then fail,
        # once real time passed the hardcoded eligible date.
        today = date.today()
        leave_end = today - timedelta(days=10)
        leave_start = leave_end - timedelta(days=14)
        self.attempt_start = today + timedelta(days=30)
        self.attempt_end = today + timedelta(days=32)
        LeaveEntitlement.objects.create(
            employee=self.emp, leave_type=self.annual, year=leave_start.year, entitled_days=Decimal('30'))
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=leave_start, end_date=leave_end, days=Decimal('15'))
        self.client = Client()
        self.client.login(username='cooldown.demo', password='pw12345!')

    def test_blocked_submission_creates_no_leave_request(self):
        resp = self.client.post('/hr/my-profile/', {
            'action': 'request_leave',
            'leave_type': self.annual.pk,
            'start_date': self.attempt_start.isoformat(),
            'end_date': self.attempt_end.isoformat(),
            'employee_reason': 'attempt during cooldown',
        })
        self.assertEqual(resp.status_code, 200)  # re-renders the page with the error, no redirect
        self.assertFalse(LeaveRequest.objects.filter(employee=self.emp, start_date=self.attempt_start).exists())

    def test_banner_context_present_on_get(self):
        resp = self.client.get('/hr/my-profile/')
        self.assertIn('leave_cooldown', resp.context)
        self.assertIsNotNone(resp.context['leave_cooldown'])
        self.assertEqual(resp.context['leave_cooldown']['leave_type_id'], self.annual.pk)


class EditLeaveRequestCooldownBypassTests(TestCase):
    """Editing an existing pending request must not be a back door around
    the same cooldown a brand-new submission would be blocked by — e.g.
    switching an already-pending Sick request's leave_type to Annual while
    blocked. hr.views.my_profile's is_edit_leave_post branch calls
    edit_leave_request directly and, before this fix, never consulted
    annual_leave_cooldown at all."""

    def setUp(self):
        self.user = User.objects.create_user(username='cooldown.editor', password='pw12345!')
        self.emp = make_employee('CD-014', 'Edit Bypass Employee', joining=None)
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])
        self.annual = make_leave_type()
        self.sick = make_leave_type(code='sick', name='Sick', days=Decimal('12'), accumulative=False)
        today = date.today()
        leave_end = today - timedelta(days=10)
        leave_start = leave_end - timedelta(days=14)
        LeaveEntitlement.objects.create(
            employee=self.emp, leave_type=self.annual, year=leave_start.year, entitled_days=Decimal('30'))
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.annual, start_date=leave_start, end_date=leave_end, days=Decimal('15'))
        # The bypass vector: an unrelated, still-pending request the employee
        # is otherwise free to edit (dates/reason) at any time.
        self.pending = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.sick, start_date=today + timedelta(days=5),
            end_date=today + timedelta(days=6), created_by=self.user, status='pending')
        self.client = Client()
        self.client.login(username='cooldown.editor', password='pw12345!')

    def test_editing_into_annual_while_blocked_is_rejected(self):
        original_start = self.pending.start_date
        new_start = date.today() + timedelta(days=40)
        new_end = new_start + timedelta(days=2)
        resp = self.client.post('/hr/my-profile/', {
            'action': 'edit_leave_request',
            'request_id': self.pending.pk,
            'leave_type': self.annual.pk,
            'start_date': new_start.isoformat(),
            'end_date': new_end.isoformat(),
            'employee_reason': 'trying to switch into annual while blocked',
        })
        self.assertEqual(resp.status_code, 302)  # bounced back, not applied
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.leave_type_id, self.sick.pk)  # unchanged
        self.assertEqual(self.pending.start_date, original_start)  # unchanged

    def test_editing_an_already_pending_annual_request_while_blocked_is_also_rejected(self):
        """Same gate applies even when the pending request was already
        Annual (just changing its dates), not only on a type-switch."""
        self.pending.leave_type = self.annual
        self.pending.save(update_fields=['leave_type'])
        new_start = date.today() + timedelta(days=50)
        new_end = new_start + timedelta(days=1)
        original_start = self.pending.start_date
        resp = self.client.post('/hr/my-profile/', {
            'action': 'edit_leave_request',
            'request_id': self.pending.pk,
            'leave_type': self.annual.pk,
            'start_date': new_start.isoformat(),
            'end_date': new_end.isoformat(),
            'employee_reason': 'just moving my dates',
        })
        self.assertEqual(resp.status_code, 302)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.start_date, original_start)  # unchanged
