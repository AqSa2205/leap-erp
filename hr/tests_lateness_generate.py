"""Generating monthly lateness reports from the Team Exceptions page.

Production has no shell and no cron, so the management command has never run
there and no report has ever been produced. The page action replaces it, and
is restricted to finished months: the generator freezes each employee's
snapshot the first time it runs for a month, so a mid-month run would lock in
a partial report forever.

"Today" is pinned in every test. A test that used the real date would change
meaning on the first of each month.
"""

from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.core import mail
from django.test import TransactionTestCase
from django.urls import reverse

from accounts.models import Role, User
from hr.models import AttendanceRecord, LateQuery, MonthlyLatenessReport
from hr.tests import _date, _login_user, make_employee

RIYADH = ZoneInfo('Asia/Riyadh')


def pinned(year, month, day):
    """Patch 'now' for the lateness service, in Riyadh time."""
    return mock.patch(
        'hr.lateness_report_services.timezone.now',
        return_value=datetime(year, month, day, 10, 0, tzinfo=RIYADH))


class _Fixture(TransactionTestCase):

    def setUp(self):
        self.emp = make_employee(iqama='LATE-GEN-1', name='Sara Khan')
        self.user = _login_user('late_gen_emp')
        self.user.email = 'late_gen_emp@leap.com'
        self.user.save(update_fields=['email'])
        self.emp.user = self.user
        self.emp.save(update_fields=['user'])

    def _mark_late(self, year, month, day):
        # bulk_create, as the existing lateness tests do: it skips
        # AttendanceRecord.save()'s separate real-time "3 lates" email.
        return AttendanceRecord.objects.bulk_create([
            AttendanceRecord(employee=self.emp, date=_date(year, month, day),
                             status='late')])[0]


class GenerateForCompletedMonthTests(_Fixture):

    def test_a_finished_month_is_generated_and_emailed(self):
        from hr.lateness_report_services import generate_for_completed_month
        for day in (3, 10, 17):
            self._mark_late(2026, 8, day)
        with pinned(2026, 9, 15):
            created = generate_for_completed_month(_date(2026, 8, 1))
        self.assertEqual(created, 1)
        report = MonthlyLatenessReport.objects.get(employee=self.emp)
        self.assertEqual(report.month, _date(2026, 8, 1))
        self.assertEqual(report.total_lates, 3)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_whole_month_is_captured_including_the_last_day(self):
        """The reason to run after the month ends. The cron design had to
        guess a moment late on the last day; this cannot miss the 31st."""
        from hr.lateness_report_services import generate_for_completed_month
        self._mark_late(2026, 8, 31)
        with pinned(2026, 9, 1):
            generate_for_completed_month(_date(2026, 8, 1))
        report = MonthlyLatenessReport.objects.get(employee=self.emp)
        self.assertIn('2026-08-31', report.late_dates)

    def test_the_current_month_is_refused(self):
        """A mid-month report would be frozen forever with part of the
        month missing. Refused loudly, not quietly skipped."""
        from hr.lateness_report_services import generate_for_completed_month
        self._mark_late(2026, 9, 3)
        with pinned(2026, 9, 15):
            with self.assertRaises(ValueError):
                generate_for_completed_month(_date(2026, 9, 1))
        self.assertFalse(MonthlyLatenessReport.objects.exists())

    def test_a_future_month_is_refused(self):
        from hr.lateness_report_services import generate_for_completed_month
        with pinned(2026, 9, 15):
            with self.assertRaises(ValueError):
                generate_for_completed_month(_date(2026, 11, 1))

    def test_the_next_months_lates_are_not_pulled_in(self):
        """Generating August on 15 September must not count September."""
        from hr.lateness_report_services import generate_for_completed_month
        self._mark_late(2026, 8, 20)
        self._mark_late(2026, 9, 2)
        with pinned(2026, 9, 15):
            generate_for_completed_month(_date(2026, 8, 1))
        self.assertEqual(MonthlyLatenessReport.objects.get().total_lates, 1)

    def test_running_it_twice_neither_duplicates_nor_re_emails(self):
        from hr.lateness_report_services import generate_for_completed_month
        self._mark_late(2026, 8, 20)
        with pinned(2026, 9, 15):
            first = generate_for_completed_month(_date(2026, 8, 1))
            second = generate_for_completed_month(_date(2026, 8, 1))
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(MonthlyLatenessReport.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)


class MonthsNeedingReportsTests(_Fixture):

    def test_a_finished_month_with_lates_and_no_reports_is_flagged(self):
        from hr.lateness_report_services import completed_months_needing_reports
        self._mark_late(2026, 8, 20)
        with pinned(2026, 9, 15):
            self.assertEqual(completed_months_needing_reports(),
                             [_date(2026, 8, 1)])

    def test_a_month_already_generated_is_not_flagged(self):
        from hr.lateness_report_services import (
            completed_months_needing_reports, generate_for_completed_month)
        self._mark_late(2026, 8, 20)
        with pinned(2026, 9, 15):
            generate_for_completed_month(_date(2026, 8, 1))
            self.assertEqual(completed_months_needing_reports(), [])

    def test_a_month_where_nobody_was_late_is_not_flagged(self):
        """No report rows is normal when nobody was late. Flagging it would
        teach HR to ignore the warning."""
        from hr.lateness_report_services import completed_months_needing_reports
        with pinned(2026, 9, 15):
            self.assertEqual(completed_months_needing_reports(), [])

    def test_the_current_month_is_never_flagged(self):
        from hr.lateness_report_services import completed_months_needing_reports
        self._mark_late(2026, 9, 3)
        with pinned(2026, 9, 15):
            self.assertEqual(completed_months_needing_reports(), [])

    def test_lates_under_a_pending_query_do_not_count(self):
        """The generator excludes disputed days, so a month whose only late
        is disputed would generate nothing - flagging it would be a warning
        the button cannot clear."""
        from hr.lateness_report_services import completed_months_needing_reports
        record = self._mark_late(2026, 8, 20)
        LateQuery.objects.create(employee=self.emp, attendance_record=record,
                                 status='pending', message='Traffic')
        with pinned(2026, 9, 15):
            self.assertEqual(completed_months_needing_reports(), [])

    def test_the_month_picker_offers_only_finished_months_newest_first(self):
        from hr.lateness_report_services import recent_completed_months
        with pinned(2026, 9, 15):
            months = recent_completed_months(count=3)
        self.assertEqual(months, [_date(2026, 8, 1), _date(2026, 7, 1),
                                  _date(2026, 6, 1)])

    def test_january_rolls_back_into_the_previous_year(self):
        from hr.lateness_report_services import recent_completed_months
        with pinned(2027, 1, 10):
            self.assertEqual(recent_completed_months(count=2),
                             [_date(2026, 12, 1), _date(2026, 11, 1)])


class GenerateButtonViewTests(_Fixture):

    def setUp(self):
        super().setUp()
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.hr = User.objects.create_user('late_gen_hr', password='x', role=role)
        self.url = reverse('hr:team_exceptions')

    def _post(self, month):
        return self.client.post(self.url, {
            'action': 'generate_lateness_report', 'month': month,
            'tab': 'late_queries'})

    def test_hr_can_generate_a_finished_month(self):
        self._mark_late(2026, 8, 20)
        self.client.force_login(self.hr)
        with pinned(2026, 9, 15):
            resp = self._post('2026-08')
        self.assertRedirects(resp, f'{self.url}?tab=late_queries',
                             fetch_redirect_response=False)
        self.assertEqual(MonthlyLatenessReport.objects.count(), 1)

    def test_the_current_month_is_refused_by_the_server(self):
        """Not just absent from the dropdown - a hand-built POST is refused."""
        self._mark_late(2026, 9, 3)
        self.client.force_login(self.hr)
        with pinned(2026, 9, 15):
            resp = self._post('2026-09')
        self.assertFalse(MonthlyLatenessReport.objects.exists())
        follow = self.client.get(resp['Location'])
        self.assertContains(follow, 'has not finished yet')

    def test_a_garbled_month_is_refused_with_a_message(self):
        self.client.force_login(self.hr)
        resp = self._post('not-a-month')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(MonthlyLatenessReport.objects.exists())

    def test_a_line_manager_who_can_open_the_page_cannot_generate(self):
        """The page is open to anyone with a report, so its own gate does not
        keep a line manager out. Generating emails the whole company's late
        notices, which is an HR act - the inner check is what stops it.

        A user with no reports would be refused by the page before this check
        ran, and a test built on one would pass with the check deleted."""
        self._mark_late(2026, 8, 20)
        role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        manager_user = User.objects.create_user('late_gen_mgr', password='x', role=role)
        manager = make_employee(iqama='LATE-GEN-MGR', name='Line Manager')
        manager.user = manager_user
        manager.save(update_fields=['user'])
        self.emp.main_manager = manager
        self.emp.save(update_fields=['main_manager'])

        self.client.force_login(manager_user)
        # Sanity: this user really does get past the page's own gate.
        self.assertEqual(self.client.get(self.url).status_code, 200)
        with pinned(2026, 9, 15):
            resp = self._post('2026-08')
        self.assertIn(resp.status_code, (403, 404))
        self.assertFalse(MonthlyLatenessReport.objects.exists())

    def test_the_panel_offers_the_control_and_flags_the_missing_month(self):
        self._mark_late(2026, 8, 20)
        self.client.force_login(self.hr)
        with pinned(2026, 9, 15):
            resp = self.client.get(f'{self.url}?tab=late_queries')
        self.assertContains(resp, 'generate_lateness_report')
        self.assertContains(resp, 'Those employees have not been told')
        self.assertContains(resp, 'August 2026')
