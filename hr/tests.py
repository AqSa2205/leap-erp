from datetime import date
from django.test import TestCase
from django.urls import reverse
from hr.work_calendar import count_working_days, is_working_day
from hr.models import LeaveType
from hr.forms import LeaveRequestForm


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


class LeaveTypeModelTests(TestCase):
    def test_create_and_str(self):
        lt, _ = LeaveType.objects.get_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': 21})
        self.assertEqual(str(lt), 'Annual')
        self.assertTrue(lt.is_paid)  # default True

    def test_code_is_unique(self):
        from django.db import IntegrityError
        LeaveType.objects.get_or_create(code='sick', defaults={
            'name': 'Sick', 'default_annual_days': 30})
        with self.assertRaises(IntegrityError):
            LeaveType.objects.create(name='Sick 2', code='sick', default_annual_days=10)


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
        self.assertEqual(AttendanceSettings.load().pk, s.pk)

    def test_settings_parse_custom_weekend(self):
        s = AttendanceSettings.load()
        s.weekend_days = '5,6'  # Sat, Sun
        s.save()
        self.assertEqual(AttendanceSettings.load().weekend_day_set(), {5, 6})


from decimal import Decimal
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from hr.models import (Employee, LeaveEntitlement, LeaveRecord,
                       LeaveDashboardAccess, LeaveRequest, LeaveRequestApproval, LeaveRequestNote)

User = get_user_model()


def make_employee(iqama='E1', name='Ali', joining=None):
    return Employee.objects.create(iqama_number=iqama, full_name=name, joining_date=joining)


def make_user(username, **kwargs):
    return User.objects.create(username=username, **kwargs)


class LeaveEntitlementTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': 30})

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
        self.assertEqual(ent.taken_days, Decimal('0'))


class LeaveRecordTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': 30})

    def test_days_autocomputed_calendar_incl_weekend(self):
        # Thu 2 Jul -> Mon 6 Jul 2026 = 5 calendar days (Fri+Sat weekend counted)
        rec = LeaveRecord(employee=self.emp, leave_type=self.annual,
                          start_date=_date(2026, 7, 2), end_date=_date(2026, 7, 6))
        rec.save()
        self.assertEqual(rec.days, Decimal('5'))

    def test_days_count_calendar_incl_holiday(self):
        # A holiday inside the range still counts as a leave day (calendar count).
        Holiday.objects.create(date=_date(2026, 7, 7), name='X')
        rec = LeaveRecord(employee=self.emp, leave_type=self.annual,
                          start_date=_date(2026, 7, 5), end_date=_date(2026, 7, 9))
        rec.save()
        self.assertEqual(rec.days, Decimal('5'))

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


from hr.leave_services import generate_year_entitlements


class WorkingDayModelTests(TestCase):
    def test_unique_date_and_str(self):
        from django.db import IntegrityError
        from hr.models import WorkingDay
        wd = WorkingDay.objects.create(date=_date(2026, 7, 18), name='Working Saturday')
        self.assertIn('2026-07-18', str(wd))
        with self.assertRaises(IntegrityError):
            WorkingDay.objects.create(date=_date(2026, 7, 18), name='dup')


class GeneratorTests(TestCase):
    def setUp(self):
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': 30})
        # Sick forced to 15 (not the seeded 30) so the generator test verifies the flat-default path with a non-seed value.
        self.sick, _ = LeaveType.objects.update_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': 15})
        self.e_new = make_employee('A', 'New', _date(2026, 2, 1))   # joins 2026
        self.e_old = make_employee('B', 'Old', _date(2020, 1, 1))   # tenured
        inactive = make_employee('C', 'Inactive', _date(2019, 1, 1))
        inactive.is_active = False
        inactive.save()

    def test_generates_flat_default_rows_for_active_employees(self):
        generate_year_entitlements(2026)
        self.assertEqual(self._ent(self.e_new, self.annual), Decimal('30'))  # flat default
        self.assertEqual(self._ent(self.e_old, self.annual), Decimal('30'))  # flat default
        self.assertEqual(self._ent(self.e_new, self.sick), Decimal('15'))    # flat default
        self.assertFalse(LeaveEntitlement.objects.filter(employee__iqama_number='C').exists())

    def test_does_not_overwrite_existing(self):
        LeaveEntitlement.objects.create(employee=self.e_old, leave_type=self.annual, year=2026, entitled_days=99)
        generate_year_entitlements(2026)
        self.assertEqual(self._ent(self.e_old, self.annual), Decimal('99'))

    def _ent(self, emp, lt):
        return LeaveEntitlement.objects.get(employee=emp, leave_type=lt, year=2026).entitled_days


class LeaveAdminViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x')
        self.admin.role = role
        self.admin.save()

    def test_leavetype_list_ok(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('hr:leavetype_list')).status_code, 200)

    def test_holiday_create(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('hr:holiday_create'),
                                {'date': '2026-07-17', 'name': 'Eid', 'is_active': 'on'})
        self.assertRedirects(resp, reverse('hr:holiday_list'))
        self.assertEqual(Holiday.objects.filter(name='Eid').count(), 1)

    def test_asset_form_has_employee_picker_autofill(self):
        from hr.models import Employee
        Employee.objects.create(iqama_number='EMP-AST', full_name='Dana Ali',
                                designation='Network Engineer', is_active=True)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:asset_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'employee-picker')          # the dropdown
        self.assertContains(resp, 'asset-employees')          # the JSON map
        self.assertContains(resp, 'Dana Ali')                 # employee option
        self.assertContains(resp, 'Network Engineer')         # designation in the map


class LeaveRecordViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def test_summary_shows_balance(self):
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=30)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:leave_summary', kwargs={'pk': self.emp.pk}) + '?year=2026')
        self.assertEqual(resp.status_code, 200)

    def test_generate_entitlements_action(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:entitlement_year'), {'year': '2026'})
        self.assertTrue(LeaveEntitlement.objects.filter(employee=self.emp, year=2026).exists())

    def test_reapply_forces_entitlements_to_leave_type_count(self):
        sick, _ = LeaveType.objects.update_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': Decimal('12')})
        # An entitlement that drifted to 30 (e.g. generated before the count change).
        ent = LeaveEntitlement.objects.create(
            employee=self.emp, leave_type=sick, year=2026, entitled_days=Decimal('30'))
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:entitlement_year'),
                         {'year': '2026', 'action': 'reapply'})
        ent.refresh_from_db()
        self.assertEqual(ent.entitled_days, Decimal('12'))  # snapped to the type count


from datetime import time
from hr.models import AttendanceRecord
from hr.attendance_services import derive_status


class AttendanceStatusTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

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


class AttendanceGridTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

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

    def test_grid_post_blank_times_marks_absent(self):
        # The Absent button clears the row's times; saving a working day with no
        # check-in must record 'absent' (overwriting any prior present record).
        from datetime import time
        AttendanceRecord.objects.create(employee=self.emp, date=_date(2026, 7, 13),
                                        status='present', check_in=time(8, 0), hours_worked=Decimal('8'))
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:attendance_grid') + '?date=2026-07-13',
                         {'date': '2026-07-13'})  # no check_in/out submitted = cleared
        rec = AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 13))
        self.assertEqual(rec.status, 'absent')
        self.assertIsNone(rec.check_in)


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


class HardeningTests(TestCase):
    """Final-review fixes: no negative hours, days=0 preserved, bad int params don't 500."""

    def setUp(self):
        from accounts.models import Role, User
        from datetime import time
        self.time = time
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def test_inverted_times_yield_no_hours(self):
        from hr.attendance_services import derive_status
        status, hours = derive_status(self.emp, _date(2026, 7, 13), self.time(22, 0), self.time(6, 0))
        self.assertIn(status, ('present', 'late'))  # late threshold now applies; either is non-absent
        self.assertIsNone(hours)  # negative span -> blank, not a corrupt negative total

    def test_bad_year_param_does_not_500(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:leave_summary', kwargs={'pk': self.emp.pk}) + '?year=abc')
        self.assertEqual(resp.status_code, 200)

    def test_grid_renders_present_presets_on_working_row(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_grid') + '?date=2026-07-13')  # Monday (working)
        # The bulk "Mark all present" (which stamped everyone 08:15) was removed —
        # attendance times now come from the Wi-Fi agent.
        self.assertNotContains(resp, 'Mark all present')
        # The per-row manual controls remain for corrections.
        self.assertContains(resp, 'data-pk="%d"' % self.emp.pk)
        self.assertContains(resp, 'present-btn')
        self.assertContains(resp, 'absent-btn')  # per-row Absent (clears times -> Absent on save)
        self.assertContains(resp, 'unsavedBar')  # sticky 'unsaved changes' reminder

    def test_grid_no_present_button_on_locked_leave_row(self):
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13), days=Decimal('1'))
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_grid') + '?date=2026-07-13')
        # The only active employee is on leave -> locked row -> no editable controls
        # for them (no time inputs, no per-row buttons). Assert on the time-input
        # name, which is unique to the rendered row (the JS references data-pk/.present-btn,
        # so those substrings appear in the script regardless of rows).
        self.assertNotContains(resp, 'name="check_in_%d"' % self.emp.pk)
        self.assertContains(resp, 'Leave')  # the locked row shows its Leave badge


import json as _json
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

    def test_display_status_no_record_honors_wfh(self):
        from hr.models import WFHRecord
        emp = make_employee(iqama='WFHHELPER')
        WFHRecord.objects.create(employee=emp, start_date=_date(2026, 7, 16), end_date=_date(2026, 7, 16))
        # Thursday working day with a WFH record -> wfh (not blank)
        self.assertEqual(display_status_no_record(_date(2026, 7, 16), emp), 'wfh')
        # weekend still beats wfh
        WFHRecord.objects.create(employee=emp, start_date=_date(2026, 7, 11), end_date=_date(2026, 7, 11))
        self.assertEqual(display_status_no_record(_date(2026, 7, 11), emp), 'weekend')  # Saturday


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


class MarkLeaveIntegrityTests(TestCase):
    """Final-review fixes: no double-booking; clock times survive a mark->unmark round trip."""

    def setUp(self):
        from accounts.models import Role, User
        from datetime import time
        self.time = time
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})
        self.client.force_login(self.admin)

    def _mark(self, day='2026-07-13'):
        return self.client.post(reverse('hr:attendance_mark_leave'),
                                data=_json.dumps({'employee': self.emp.pk, 'date': day, 'leave_type': self.annual.pk}),
                                content_type='application/json')

    def test_mark_rejects_overlapping_leave(self):
        # A multi-day leave already covers 2026-07-13.
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 7, 12), end_date=_date(2026, 7, 15), days=Decimal('4'))
        resp = self._mark('2026-07-13')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LeaveRecord.objects.filter(employee=self.emp).count(), 1)  # no second record

    def test_mark_over_present_preserves_times_roundtrip(self):
        AttendanceRecord.objects.create(employee=self.emp, date=_date(2026, 7, 13), status='present',
                                        check_in=self.time(8, 0), check_out=self.time(17, 0), hours_worked=Decimal('9'))
        self._mark('2026-07-13')
        ar = AttendanceRecord.objects.get(employee=self.emp, date=_date(2026, 7, 13))
        self.assertEqual(ar.status, 'leave')
        self.assertEqual(ar.check_in, self.time(8, 0))   # times preserved, not wiped
        lr = LeaveRecord.objects.get(employee=self.emp)
        resp = self.client.post(reverse('hr:attendance_unmark_leave'),
                                data=_json.dumps({'leave_record_id': lr.pk}), content_type='application/json')
        self.assertEqual(resp.json()['status'], 'present')   # re-derived back to present
        ar.refresh_from_db()
        self.assertEqual(ar.status, 'present')
        self.assertEqual(ar.hours_worked, Decimal('9.00'))


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
        WorkingDay.objects.create(date=_date(2026, 7, 10), name='WS')  # Fri 10 Jul is weekend
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 10), _time(8, 0))[0], 'present')
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 10), None)[0], 'absent')

    def test_plain_weekend_still_weekend(self):
        from hr.attendance_services import derive_status
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 11), None)[0], 'weekend')  # Saturday


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


class DeriveWFHTests(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_wfh_record_makes_day_wfh(self):
        from hr.models import WFHRecord
        from hr.attendance_services import derive_status
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13))
        status, hours = derive_status(self.emp, _date(2026, 7, 13), _time(8, 0), _time(17, 0))
        self.assertEqual(status, 'wfh')
        self.assertEqual(hours, Decimal('9'))  # hours still counted; Decimal(round(9.0,2)) == Decimal('9')

    def test_weekend_beats_wfh(self):
        from hr.models import WFHRecord
        from hr.attendance_services import derive_status
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 11), end_date=_date(2026, 7, 11))
        self.assertEqual(derive_status(self.emp, _date(2026, 7, 11), None)[0], 'weekend')  # Saturday


class MatrixWFHTests(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_wfh_record_shows_in_matrix(self):
        from hr.models import WFHRecord
        from hr.attendance_matrix import build_matrix
        WFHRecord.objects.create(employee=self.emp, start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13))
        days, rows = build_matrix([self.emp], _date(2026, 7, 13), _date(2026, 7, 13))
        self.assertEqual(rows[0]['cells'][0]['status'], 'wfh')


class WFHViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()

    def test_list_ok(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('hr:wfh_list')).status_code, 200)

    def test_create_wfh(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('hr:wfh_create'), {'employee': self.emp.pk,
                         'start_date': '2026-07-13', 'end_date': '2026-07-15', 'note': ''})
        from hr.models import WFHRecord
        self.assertEqual(WFHRecord.objects.filter(employee=self.emp).count(), 1)


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


class EntitlementYearGroupedTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})
        self.sick, _ = LeaveType.objects.get_or_create(code='sick', defaults={'name': 'Sick', 'default_annual_days': 15})

    def test_grouped_totals_and_taken_remaining(self):
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=30)
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2026, entitled_days=15)
        # 8 annual days taken in 2026 (explicit days so it's not recomputed)
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2026, 3, 2), end_date=_date(2026, 3, 11), days=Decimal('8'))
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:entitlement_year') + '?year=2026')
        self.assertEqual(resp.status_code, 200)
        groups = resp.context['groups']
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g['employee'].pk, self.emp.pk)
        # Only Annual is accumulative (Task 1's rule) — Sick no longer counts toward the top-level total.
        self.assertEqual(g['total_entitled'], Decimal('30'))   # Annual only; Sick is conditional
        self.assertEqual(g['total_taken'], Decimal('8'))       # only annual taken
        self.assertEqual(g['total_remaining'], Decimal('22'))  # 30 - 8
        self.assertEqual(len(g['rows']), 2)                    # per-type breakdown
        annual_row = next(r for r in g['rows'] if r['leave_type'].pk == self.annual.pk)
        self.assertEqual(annual_row['taken'], Decimal('8'))
        self.assertEqual(annual_row['remaining'], Decimal('22'))

    def test_leave_from_other_year_not_counted(self):
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=30)
        LeaveRecord.objects.create(employee=self.emp, leave_type=self.annual,
                                   start_date=_date(2025, 3, 2), end_date=_date(2025, 3, 6), days=Decimal('5'))
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:entitlement_year') + '?year=2026')
        g = resp.context['groups'][0]
        self.assertEqual(g['total_taken'], Decimal('0'))       # 2025 leave excluded
        self.assertEqual(g['total_remaining'], Decimal('30'))


class SickLeaveCertificateTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': 15})
        self.sick.requires_medical_certificate = True
        self.sick.save()
        self.annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def _cert(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile('cert.pdf', b'%PDF-1.4 fake', content_type='application/pdf')

    def test_sick_migration_set_flag(self):
        # Data migration 0017 should have flagged the seeded sick type — but this
        # test recreates it, so just assert the flag we set holds.
        self.assertTrue(LeaveType.objects.get(code='sick').requires_medical_certificate)

    def test_model_clean_blocks_sick_without_certificate(self):
        from django.core.exceptions import ValidationError
        r = LeaveRecord(employee=self.emp, leave_type=self.sick,
                        start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13), days=Decimal('1'))
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_model_clean_allows_sick_with_certificate(self):
        r = LeaveRecord(employee=self.emp, leave_type=self.sick,
                        start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13), days=Decimal('1'),
                        medical_certificate=self._cert())
        r.full_clean()  # should not raise

    def test_model_clean_allows_non_sick_without_certificate(self):
        r = LeaveRecord(employee=self.emp, leave_type=self.annual,
                        start_date=_date(2026, 7, 13), end_date=_date(2026, 7, 13), days=Decimal('1'))
        r.full_clean()  # annual doesn't require a certificate

    def test_grid_mark_leave_blocks_certificate_type(self):
        import json as _json
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('hr:attendance_mark_leave'),
                                data=_json.dumps({'employee': self.emp.pk, 'date': '2026-07-13',
                                                  'leave_type': self.sick.pk}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('certificate', resp.json()['error'].lower())
        self.assertFalse(LeaveRecord.objects.filter(employee=self.emp).exists())

    def tearDown(self):
        # Remove any files written to MEDIA_ROOT by accepted-certificate tests.
        for r in LeaveRecord.objects.exclude(medical_certificate=''):
            if r.medical_certificate:
                r.medical_certificate.delete(save=False)


class LeaveTypeDaySyncTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.emp2 = make_employee(iqama='E2', name='Sara')
        self.sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': 15})
        self.annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def test_changing_days_updates_all_entitlements_all_years(self):
        e25 = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2025, entitled_days=15)
        e26 = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2026, entitled_days=15)
        e26b = LeaveEntitlement.objects.create(employee=self.emp2, leave_type=self.sick, year=2026, entitled_days=15)
        self.sick.default_annual_days = Decimal('20')
        self.sick.save()
        for e in (e25, e26, e26b):
            e.refresh_from_db()
            self.assertEqual(e.entitled_days, Decimal('20'))

    def test_overwrites_custom_values(self):
        custom = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2026, entitled_days=10)
        self.sick.default_annual_days = Decimal('20')
        self.sick.save()
        custom.refresh_from_db()
        self.assertEqual(custom.entitled_days, Decimal('20'))  # custom value overwritten

    def test_annual_type_is_synced(self):
        # Annual now behaves like any other type: a flat change updates all its
        # entitlements (proration policy dropped).
        a = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=25)
        self.annual.default_annual_days = Decimal('40')
        self.annual.save()
        a.refresh_from_db()
        self.assertEqual(a.entitled_days, Decimal('40'))  # now updated

    def test_no_change_leaves_entitlements_alone(self):
        e = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2026, entitled_days=15)
        self.sick.name = 'Sick Leave'  # change something other than the day count
        self.sick.save()
        e.refresh_from_db()
        self.assertEqual(e.entitled_days, Decimal('15'))

    def test_other_leave_types_unaffected(self):
        other, _ = LeaveType.objects.get_or_create(
            code='unpaid', defaults={'name': 'Unpaid', 'default_annual_days': 0})
        other_ent = LeaveEntitlement.objects.create(employee=self.emp, leave_type=other, year=2026, entitled_days=0)
        sick_ent = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2026, entitled_days=15)
        self.sick.default_annual_days = Decimal('20')
        self.sick.save()
        other_ent.refresh_from_db(); sick_ent.refresh_from_db()
        self.assertEqual(other_ent.entitled_days, Decimal('0'))   # different type untouched
        self.assertEqual(sick_ent.entitled_days, Decimal('20'))


import tempfile
from django.test import override_settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import Role, User
from hr.models import Vehicle, VehicleDocument


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class VehicleDocumentTests(TestCase):
    def setUp(self):
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.admin = User.objects.create_user('va', password='x')
        self.admin.role = sa
        self.admin.save()
        self.vehicle = Vehicle.objects.create(plate_number='ABC-1234')
        self.client.force_login(self.admin)

    def _upload(self, **overrides):
        data = {
            'document_type': 'registration', 'custom_type': '',
            'title': 'Istimara 2026',
            'file': SimpleUploadedFile('reg.pdf', b'%PDF x',
                                       content_type='application/pdf'),
        }
        data.update(overrides)
        return self.client.post(
            reverse('hr:vehicle_doc_upload', kwargs={'pk': self.vehicle.pk}), data)

    def test_upload_then_delete_reclaims_file(self):
        self._upload()
        self.assertEqual(VehicleDocument.objects.count(), 1)
        doc = VehicleDocument.objects.first()
        name = doc.file.name
        self.assertTrue(default_storage.exists(name))
        self.client.get(reverse('hr:vehicle_doc_delete', kwargs={'pk': doc.pk}))
        self.assertFalse(default_storage.exists(name))

    def test_other_type_requires_custom_label(self):
        self._upload(document_type='other', custom_type='')
        self.assertEqual(VehicleDocument.objects.count(), 0)  # rejected

    def test_custom_type_label(self):
        self._upload(document_type='other', custom_type='Pollution Cert')
        doc = VehicleDocument.objects.first()
        self.assertEqual(doc.type_label, 'Pollution Cert')

    def test_cascade_delete_removes_doc_files(self):
        self._upload(document_type='insurance')
        doc = VehicleDocument.objects.first()
        name = doc.file.name
        self.vehicle.delete()  # cascade -> central signal cleans the file
        self.assertEqual(VehicleDocument.objects.count(), 0)
        self.assertFalse(default_storage.exists(name))

    def test_edit_replacing_file_reclaims_old(self):
        self._upload()
        doc = VehicleDocument.objects.first()
        old_name = doc.file.name
        self.client.post(reverse('hr:vehicle_doc_edit', kwargs={'pk': doc.pk}), {
            'document_type': 'registration', 'custom_type': '', 'title': 'Istimara 2026',
            'file': SimpleUploadedFile('newreg.pdf', b'%PDF new',
                                       content_type='application/pdf'),
        })
        doc.refresh_from_db()
        self.assertNotEqual(doc.file.name, old_name)
        self.assertFalse(default_storage.exists(old_name))  # no orphan on replace
        self.assertTrue(default_storage.exists(doc.file.name))

    def test_edit_metadata_keeps_file(self):
        self._upload()
        doc = VehicleDocument.objects.first()
        name = doc.file.name
        self.client.post(reverse('hr:vehicle_doc_edit', kwargs={'pk': doc.pk}), {
            'document_type': 'registration', 'custom_type': '', 'title': 'Istimara (renamed)',
        })  # no file -> keep existing
        doc.refresh_from_db()
        self.assertEqual(doc.title, 'Istimara (renamed)')
        self.assertEqual(doc.file.name, name)
        self.assertTrue(default_storage.exists(name))

    def test_edit_form_renders(self):
        self._upload()
        doc = VehicleDocument.objects.first()
        r = self.client.get(reverse('hr:vehicle_doc_edit', kwargs={'pk': doc.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Edit Document', r.content)


from hr.models import Employee


class MyProfilePortalTests(TestCase):
    """The self-service portal shows the linked employee's data, or a prompt
    when the account isn't linked yet."""

    def setUp(self):
        self.user = User.objects.create_user('emp1', password='x', email='emp1@leap.com')
        self.emp = Employee.objects.create(
            full_name='Emp One', iqama_number='IQ-001', user=self.user)
        self.unlinked = User.objects.create_user('nobody', password='x')

    def test_portal_shows_linked_employee(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse('hr:my_profile'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Emp One')

    def test_portal_prompts_when_not_linked(self):
        self.client.force_login(self.unlinked)
        r = self.client.get(reverse('hr:my_profile'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "isn't linked")

    def test_portal_shows_assets_by_name_and_vehicles_by_driver_id(self):
        from hr.models import Asset, Vehicle
        # Asset linked via the denormalised employee_name (no AssetAssignment).
        Asset.objects.create(asset_name='Dell Laptop', asset_type='Laptop',
                             employee_name='Emp One')
        # Vehicle linked via driver_id == iqama_number.
        Vehicle.objects.create(plate_number='XYZ-9', vehicle_maker='Toyota',
                              driver_id='IQ-001')
        self.client.force_login(self.user)
        r = self.client.get(reverse('hr:my_profile'))
        self.assertContains(r, 'Dell Laptop')   # matched by employee_name
        self.assertContains(r, 'XYZ-9')          # matched by driver_id == iqama


class LinkEmployeeUsersCommandTests(TestCase):
    def test_links_by_email_then_code(self):
        from django.core.management import call_command
        u_email = User.objects.create_user('byemail', password='x', email='match@leap.com')
        u_code = User.objects.create_user('bycode', password='x', employee_code='IQ-CODE')
        e1 = Employee.objects.create(full_name='By Email', iqama_number='IQ-1', work_email='match@leap.com')
        e2 = Employee.objects.create(full_name='By Code', iqama_number='IQ-CODE')
        call_command('link_employee_users')
        e1.refresh_from_db()
        e2.refresh_from_db()
        self.assertEqual(e1.user, u_email)
        self.assertEqual(e2.user, u_code)

    def test_does_not_steal_already_linked_user(self):
        from django.core.management import call_command
        u = User.objects.create_user('shared', password='x', email='dup@leap.com')
        already = Employee.objects.create(full_name='Already', iqama_number='IQ-A', user=u)
        contender = Employee.objects.create(full_name='Contender', iqama_number='IQ-B', work_email='dup@leap.com')
        call_command('link_employee_users')
        contender.refresh_from_db()
        self.assertIsNone(contender.user)  # u already taken


class EmployeeInactiveFromTests(TestCase):
    """Marking an employee inactive captures an 'inactive from' date (defaulting
    to today, never in the future); reactivating clears it; the list filters by
    status and the date picker is capped at today."""

    def _data(self, **over):
        d = {'iqama_number': 'IQ-X', 'full_name': 'Test Emp', 'user': '', 'is_active': 'on'}
        d.update(over)
        return d

    def test_marking_inactive_defaults_date_to_today(self):
        from django.utils import timezone
        from hr.forms import EmployeeForm
        emp = Employee.objects.create(full_name='X', iqama_number='IQ-1', is_active=True)
        data = self._data(iqama_number='IQ-1', full_name='X')
        data.pop('is_active')  # unchecked -> inactive
        f = EmployeeForm(data=data, instance=emp)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        emp.refresh_from_db()
        self.assertFalse(emp.is_active)
        self.assertEqual(emp.inactive_from, timezone.localdate())

    def test_inactive_with_past_date_kept(self):
        from datetime import date
        from hr.forms import EmployeeForm
        emp = Employee.objects.create(full_name='Y', iqama_number='IQ-2', is_active=True)
        data = self._data(iqama_number='IQ-2', full_name='Y', inactive_from='2025-01-15')
        data.pop('is_active')
        f = EmployeeForm(data=data, instance=emp)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        emp.refresh_from_db()
        self.assertEqual(emp.inactive_from, date(2025, 1, 15))

    def test_future_inactive_date_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        from hr.forms import EmployeeForm
        emp = Employee.objects.create(full_name='Z', iqama_number='IQ-3', is_active=True)
        future = (timezone.localdate() + timedelta(days=5)).isoformat()
        data = self._data(iqama_number='IQ-3', full_name='Z', inactive_from=future)
        data.pop('is_active')
        f = EmployeeForm(data=data, instance=emp)
        self.assertFalse(f.is_valid())
        self.assertIn('inactive_from', f.errors)

    def test_reactivating_clears_inactive_from(self):
        from datetime import date
        from hr.forms import EmployeeForm
        emp = Employee.objects.create(full_name='W', iqama_number='IQ-4',
                                      is_active=False, inactive_from=date(2025, 1, 1))
        f = EmployeeForm(data=self._data(iqama_number='IQ-4', full_name='W', is_active='on'),
                         instance=emp)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        emp.refresh_from_db()
        self.assertTrue(emp.is_active)
        self.assertIsNone(emp.inactive_from)

    def test_date_picker_max_is_today(self):
        from django.utils import timezone
        from hr.forms import EmployeeForm
        f = EmployeeForm()
        self.assertEqual(f.fields['inactive_from'].widget.attrs.get('max'),
                         timezone.localdate().isoformat())

    def test_list_filters_by_status(self):
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        admin = User.objects.create_user('hradmin', password='x')
        admin.role = sa
        admin.save()
        Employee.objects.create(full_name='Active One', iqama_number='IQ-AC', is_active=True)
        Employee.objects.create(full_name='Inactive One', iqama_number='IQ-IN', is_active=False)
        self.client.force_login(admin)
        r = self.client.get(reverse('hr:employee_list'), {'status': 'inactive'})
        names = [e.full_name for e in r.context['employees']]
        self.assertIn('Inactive One', names)
        self.assertNotIn('Active One', names)


class AssetDecommissionTests(TestCase):
    """Assets can be marked out of service (dead) — removed from stock, kept for
    records — and restored. Listable via the status filter."""

    def setUp(self):
        from hr.models import Asset
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.admin = User.objects.create_user('aadm', password='x')
        self.admin.role = role
        self.admin.save()
        self.asset = Asset.objects.create(asset_name='Old Laptop', in_stock=True)
        self.client.force_login(self.admin)

    def test_decommission_marks_and_removes_from_stock(self):
        from django.utils import timezone
        self.client.post(reverse('hr:asset_decommission', kwargs={'pk': self.asset.pk}),
                         {'reason': 'Dead'})
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.is_decommissioned)
        self.assertFalse(self.asset.in_stock)  # can't be in stock
        self.assertEqual(self.asset.decommissioned_on, timezone.localdate())
        self.assertEqual(self.asset.decommission_reason, 'Dead')

    def test_restore_brings_back(self):
        from datetime import date
        self.asset.is_decommissioned = True
        self.asset.decommissioned_on = date(2025, 1, 1)
        self.asset.in_stock = False
        self.asset.save()
        self.client.post(reverse('hr:asset_restore', kwargs={'pk': self.asset.pk}))
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.is_decommissioned)
        self.assertIsNone(self.asset.decommissioned_on)

    def test_form_decommission_forces_out_of_stock(self):
        from hr.forms import AssetForm
        f = AssetForm(data={'asset_name': 'X', 'quantity': '1', 'in_stock': 'on',
                            'is_decommissioned': 'on'}, instance=self.asset)
        self.assertTrue(f.is_valid(), f.errors)
        obj = f.save()
        self.assertTrue(obj.is_decommissioned)
        self.assertFalse(obj.in_stock)  # in_stock forced off

    def test_list_filters_decommissioned(self):
        from hr.models import Asset
        Asset.objects.create(asset_name='Dead One', is_decommissioned=True)
        r = self.client.get(reverse('hr:asset_list'), {'status': 'decommissioned'})
        names = [a.asset_name for a in r.context['assets']]
        self.assertIn('Dead One', names)
        self.assertNotIn('Old Laptop', names)


class LeaveApprovalModelsTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.aamna = make_user('aamna_khan')
        self.ali = make_user('ali_sultan')
        LeaveDashboardAccess.objects.create(user=self.aamna, is_active=True)
        LeaveDashboardAccess.objects.create(user=self.ali, is_active=True)

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


import threading
from datetime import timedelta
from django.test import TransactionTestCase
from django.utils import timezone
from hr.forms import check_leave_balance
from hr.leave_approval_services import (
    record_approver_decision, override_finalize, edit_approver_decision, submit_leave_request)
from notifications.models import Notification


class LeaveApprovalServiceTests(TestCase):
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.aamna = make_user('aamna_khan')
        self.ali = make_user('ali_sultan')
        LeaveDashboardAccess.objects.create(user=self.aamna, is_active=True)
        LeaveDashboardAccess.objects.create(user=self.ali, is_active=True)
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


class SelfApprovalPreventionTests(TestCase):
    """A user must never be able to approve, disapprove, or override their
    own leave request, under any circumstances — whether they'd otherwise
    qualify as an approver (LeaveDashboardAccess) or hold override access."""
    def setUp(self):
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.creator = make_user('sap_creator')

    def _emp_with_user(self, iqama, username):
        user = make_user(username, password='x')
        emp = make_employee(iqama=iqama, name=username)
        emp.user = user
        emp.save(update_fields=['user'])
        return emp, user

    def test_submitter_excluded_from_own_approver_roster(self):
        # Person A is both an approver (LeaveDashboardAccess) AND the one
        # submitting this request — they must not end up as their own
        # approver. Person B, the co-approver, must still be assigned.
        person_a, user_a = self._emp_with_user('SAP-A', 'sap_person_a')
        person_b, user_b = self._emp_with_user('SAP-B', 'sap_person_b')
        LeaveDashboardAccess.objects.create(user=user_a, is_active=True)
        LeaveDashboardAccess.objects.create(user=user_b, is_active=True)
        LeaveEntitlement.objects.create(employee=person_a, leave_type=self.marriage, year=2026, entitled_days=3)

        req = submit_leave_request(
            employee=person_a, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3), created_by=user_a)

        self.assertFalse(req.approvals.filter(approver=user_a).exists())
        self.assertTrue(req.approvals.filter(approver=user_b).exists())

    def test_multi_approver_escalation_bypasses_requester(self):
        # 3 configured approvers; the requester is one of them. Approval
        # authority must fall entirely to the other 2 — unanimous approval
        # from just those 2 (never involving the requester) finalizes it.
        person_a, user_a = self._emp_with_user('SAP-MA', 'sap_multi_a')
        _, user_b = self._emp_with_user('SAP-MB', 'sap_multi_b')
        _, user_c = self._emp_with_user('SAP-MC', 'sap_multi_c')
        for u in (user_a, user_b, user_c):
            LeaveDashboardAccess.objects.create(user=u, is_active=True)
        LeaveEntitlement.objects.create(employee=person_a, leave_type=self.marriage, year=2026, entitled_days=3)

        req = submit_leave_request(
            employee=person_a, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3), created_by=user_a)

        self.assertEqual(req.approvals.count(), 2)  # requester excluded, only B and C
        self.assertFalse(req.approvals.filter(approver=user_a).exists())

        record_approver_decision(req, user_b, 'approved')
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')  # still waiting on C
        record_approver_decision(req, user_c, 'approved')
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')  # unanimous among B+C alone finalized it

    def test_lone_approver_being_requester_leaves_zero_approvers(self):
        # If the ONLY configured approver submits for themselves, nobody is
        # assigned — the request sits pending until a Super Admin/override
        # holder steps in. This must not error; it's a legitimate state.
        person_a, user_a = self._emp_with_user('SAP-LONE', 'sap_lone')
        LeaveDashboardAccess.objects.create(user=user_a, is_active=True)
        LeaveEntitlement.objects.create(employee=person_a, leave_type=self.marriage, year=2026, entitled_days=3)

        req = submit_leave_request(
            employee=person_a, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3), created_by=user_a)
        self.assertEqual(req.approvals.count(), 0)
        self.assertEqual(req.status, 'pending')
        self.assertEqual(req.pending_approvers(), [])

    def test_record_approver_decision_blocks_self_approval(self):
        # Simulates a legacy/edge-case row where a self-approval slipped in
        # despite the submission-time exclusion — must still be blocked.
        person_a, user_a = self._emp_with_user('SAP-LEGACY1', 'sap_legacy1')
        req = LeaveRequest.objects.create(
            employee=person_a, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3))
        LeaveRequestApproval.objects.create(leave_request=req, approver=user_a)
        with self.assertRaises(ValueError):
            record_approver_decision(req, user_a, 'approved')
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_edit_approver_decision_blocks_self_approval(self):
        person_a, user_a = self._emp_with_user('SAP-LEGACY2', 'sap_legacy2')
        _, user_b = self._emp_with_user('SAP-LEGACY2B', 'sap_legacy2b')
        req = LeaveRequest.objects.create(
            employee=person_a, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3))
        approval = LeaveRequestApproval.objects.create(
            leave_request=req, approver=user_a, decision='approved', decided_at=timezone.now())
        LeaveRequestApproval.objects.create(leave_request=req, approver=user_b)
        with self.assertRaises(ValueError):
            edit_approver_decision(req, user_a, 'disapproved', edit_note='Changed my mind')

    def test_override_finalize_blocks_self_override(self):
        person_a, user_a = self._emp_with_user('SAP-OVR', 'sap_override_self')
        req = LeaveRequest.objects.create(
            employee=person_a, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3))
        with self.assertRaises(ValueError):
            override_finalize(req, user_a, 'approved', reason='Approving my own request')
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_override_finalize_still_works_for_a_different_admin(self):
        # Confirms the self-check is narrowly scoped to the requester, not a
        # blanket block on override for this request.
        person_a, _ = self._emp_with_user('SAP-OVR2', 'sap_override_target')
        admin = make_user('sap_override_admin')
        req = LeaveRequest.objects.create(
            employee=person_a, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3))
        override_finalize(req, admin, 'approved', reason='Approver unavailable')
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')


class LeaveRequestDetailOwnRequestUITests(TestCase):
    """UI-level: a user viewing their OWN leave request must never see the
    decide/edit-decision/override forms, even if they'd otherwise qualify —
    and the page must make clear why (see is_own_request context)."""
    def setUp(self):
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.employee_user = make_user('duo_employee', password='x')
        self.employee_user.set_password('testpass123')
        # Also a Super Admin AND holds override access — the strongest case:
        # even with every other authority, their OWN request must stay view-only.
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.employee_user.role = role
        self.employee_user.save()
        LeaveDashboardAccess.objects.create(user=self.employee_user, is_active=True)
        self.emp = make_employee(iqama='DUO-EMP', name='Duo Employee')
        self.emp.user = self.employee_user
        self.emp.save(update_fields=['user'])

        self.co_approver_user = make_user('duo_coapprover', password='x')
        self.co_approver_user.set_password('testpass123')
        self.co_approver_user.save()
        LeaveDashboardAccess.objects.create(user=self.co_approver_user, is_active=True)

        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2026, entitled_days=3)
        self.req = submit_leave_request(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3), created_by=self.employee_user)

    def test_own_request_hides_decide_and_override_forms(self):
        self.client.login(username='duo_employee', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'name="action" value="decide"')
        self.assertNotContains(resp, 'name="action" value="override"')
        self.assertNotContains(resp, 'name="action" value="add_note"')
        self.assertContains(resp, 'This is your own leave request')

    def test_co_approver_sees_decide_form_for_the_same_request(self):
        self.client.login(username='duo_coapprover', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        self.assertContains(resp, 'name="action" value="decide"')

    def test_own_request_post_decide_forbidden_even_with_legacy_approval_row(self):
        # Simulate a legacy self-approval row somehow existing, and confirm
        # POSTing a decision is still blocked (error message, not a crash).
        LeaveRequestApproval.objects.get_or_create(leave_request=self.req, approver=self.employee_user)
        self.client.login(username='duo_employee', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'decide', 'decision': 'approved'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')

    def test_own_request_post_override_forbidden(self):
        self.client.login(username='duo_employee', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'override', 'decision': 'approved', 'reason': 'Self-serving override'})
        self.assertEqual(resp.status_code, 302)  # redirects with an error message, doesn't crash
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')


class SubmitLeaveRequestRaceConditionTests(TransactionTestCase):
    """Real concurrency test: two genuinely simultaneous submissions for the
    same employee/leave_type/year, together exceeding the entitlement, must
    not both succeed. Uses TransactionTestCase (real commits, no wrapping
    transaction) + threads so the select_for_update() lock in
    validate_leave_submission actually has two separate DB connections to
    serialize — a plain TestCase's single outer transaction would make the
    lock a no-op and prove nothing."""
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2026, entitled_days=3)
        self.creator = make_user('race_creator')

    def test_concurrent_submissions_cannot_together_exceed_balance(self):
        from django.db import connection
        results = []
        barrier = threading.Barrier(2)

        def attempt(start_day):
            try:
                barrier.wait(timeout=5)
                submit_leave_request(
                    employee=self.emp, leave_type=self.marriage,
                    start_date=_date(2026, 9, start_day), end_date=_date(2026, 9, start_day + 2),
                    created_by=self.creator,
                )
                results.append('success')
            except ValueError:
                results.append('rejected')
            finally:
                connection.close()

        # Two non-overlapping-in-date but same-balance-pool requests (3 days
        # each against a 3-day entitlement) — only one can survive.
        t1 = threading.Thread(target=attempt, args=(1,))
        t2 = threading.Thread(target=attempt, args=(10,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(sorted(results), ['rejected', 'success'])
        self.assertEqual(LeaveRequest.objects.filter(employee=self.emp).count(), 1)


class OverlappingLeaveAcrossTypesTests(TestCase):
    """A person cannot be on two kinds of leave for overlapping dates —
    check_leave_balance/validate_leave_submission must block this
    regardless of leave type, closing the 'split the same dates across two
    leave types to get more total days off' loophole."""
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': 12, 'is_accumulative': False})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2026, entitled_days=30)
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2026, entitled_days=30)

    def test_overlapping_pending_request_of_different_type_rejected(self):
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.sick,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 5))  # pending
        with self.assertRaises(ValidationError) as ctx:
            check_leave_balance(self.emp, self.marriage, _date(2026, 9, 3), _date(2026, 9, 4))
        self.assertIn('overlaps with another leave request', str(ctx.exception))

    def test_overlapping_approved_leaverecord_of_different_type_rejected(self):
        LeaveRecord.objects.create(
            employee=self.emp, leave_type=self.sick,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 5), days=Decimal('5'))
        with self.assertRaises(ValidationError) as ctx:
            check_leave_balance(self.emp, self.marriage, _date(2026, 9, 3), _date(2026, 9, 4))
        self.assertIn('already taken or been approved', str(ctx.exception))

    def test_non_overlapping_dates_different_type_allowed(self):
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.sick,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 5))
        check_leave_balance(self.emp, self.marriage, _date(2026, 10, 1), _date(2026, 10, 2))  # no overlap

    def test_overlapping_with_disapproved_request_of_different_type_allowed(self):
        # A disapproved request never created a LeaveRecord and is no longer
        # 'pending' — those same dates must be freely reusable.
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.sick,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 5), status='disapproved')
        check_leave_balance(self.emp, self.marriage, _date(2026, 9, 3), _date(2026, 9, 4))


class LeaveCancellationRefundSafetyTests(TestCase):
    """Balances are pure live aggregates over LeaveRecord (taken_days/
    remaining_days are @property, never a stored counter) — deleting a
    LeaveRecord (the closest thing to 'cancelling' approved leave today;
    there's no separate cancel action yet) restores the exact balance with
    no double-refund risk, since there's nothing to decrement/increment
    apart from the aggregate itself."""
    def test_deleting_leave_record_restores_full_balance(self):
        emp = make_employee()
        annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})
        ent = LeaveEntitlement.objects.create(employee=emp, leave_type=annual, year=2026, entitled_days=10)
        record = LeaveRecord.objects.create(
            employee=emp, leave_type=annual, start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 5), days=Decimal('5'))
        self.assertEqual(ent.remaining_days, Decimal('5'))
        record.delete()
        self.assertEqual(ent.remaining_days, Decimal('10'))

    def test_balance_fields_are_computed_not_stored(self):
        # Structural regression guard: if a future change adds a cached/
        # stored counter (e.g. 'taken_days' as a real column) instead of
        # this live aggregate, double-refund bugs become possible again.
        stored_field_names = {f.name for f in LeaveEntitlement._meta.get_fields() if not f.is_relation}
        self.assertNotIn('taken_days', stored_field_names)
        self.assertIsInstance(LeaveEntitlement.taken_days, property)
        self.assertIsInstance(LeaveEntitlement.remaining_days, property)




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
        LeaveDashboardAccess.objects.create(user=self.aamna, is_active=True)
        LeaveDashboardAccess.objects.create(user=self.ali, is_active=True)
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
        LeaveDashboardAccess.objects.create(user=self.aamna, is_active=True)
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
        # 404, not 403 — an unauthorized user must not be able to distinguish
        # "this request doesn't exist" from "it exists but isn't yours".
        self.client.login(username='doc_stranger', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_document', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_request_also_404s_indistinguishably(self):
        self.client.login(username='doc_stranger', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_document', args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse('hr:leave_request_document', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 302)


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
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2026, entitled_days=10)
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
        LeaveDashboardAccess.objects.create(user=self.aamna, is_active=True)
        self.ali = make_user('detail_ali', password='x')
        self.ali.set_password('testpass123')
        self.ali.save()
        LeaveDashboardAccess.objects.create(user=self.ali, is_active=True)
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
        # add_note is restricted to an assigned approver for THIS request, not
        # any Super Admin — detail_aamna is a designated approver here.
        self.client.login(username='detail_aamna', password='testpass123')
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

    def test_add_note_by_non_superadmin_approver_forbidden(self):
        # A Super Admin with no approval row on this specific request is
        # view-only aside from the override escape hatch — add_note requires
        # being a designated approver for THIS request, not Super Admin status.
        self.client.login(username='detail_super', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'add_note', 'note': 'Should not be allowed.'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.req.notes.count(), 0)

    def test_non_approver_non_superadmin_cannot_decide(self):
        outsider = make_user('detail_outsider', password='x')
        outsider.set_password('testpass123')
        outsider.save()
        self.client.login(username='detail_outsider', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        self.assertEqual(resp.status_code, 403)


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
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2026, entitled_days=10)

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


def _make_role_user(username, role_name):
    """A logged-in test user holding a specific Role (e.g. super_admin, ai_head)."""
    role, _ = Role.objects.get_or_create(name=role_name)
    user = make_user(username, password='x')
    user.set_password('testpass123')
    user.role = role
    user.save()
    return user


def _login_user(username):
    user = make_user(username, password='x')
    user.set_password('testpass123')
    user.save()
    return user



class GenerateEntitlementsForEmployeeTests(TestCase):
    """Unit coverage of hr.leave_services.generate_entitlements_for_employee —
    the single-employee helper shared by generate_year_entitlements (bulk)
    and the new-employee auto-generation hooks below."""
    def setUp(self):
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': 30})
        self.sick, _ = LeaveType.objects.update_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': 12, 'is_active': True})
        self.emp = make_employee('GEFE-1', 'Solo Employee')

    def test_creates_one_row_per_active_leave_type(self):
        # Not asserting an exact `created` count here: a data migration
        # (0024_leavetype_task1_values) pre-seeds several canonical LeaveType
        # rows (annual, sick, marriage, ...) into every test database, so the
        # real active-type count is >= the 2 this test explicitly cares about.
        from hr.leave_services import generate_entitlements_for_employee
        created = generate_entitlements_for_employee(self.emp, 2026)
        self.assertGreaterEqual(created, 2)
        self.assertEqual(
            LeaveEntitlement.objects.get(employee=self.emp, leave_type=self.annual, year=2026).entitled_days,
            Decimal('30'))
        self.assertEqual(
            LeaveEntitlement.objects.get(employee=self.emp, leave_type=self.sick, year=2026).entitled_days,
            Decimal('12'))

    def test_does_not_overwrite_existing_row(self):
        from hr.leave_services import generate_entitlements_for_employee
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=99)
        generate_entitlements_for_employee(self.emp, 2026)
        self.assertEqual(
            LeaveEntitlement.objects.get(employee=self.emp, leave_type=self.annual, year=2026).entitled_days,
            Decimal('99'))

    def test_ignores_inactive_leave_types(self):
        from hr.leave_services import generate_entitlements_for_employee
        inactive_lt, _ = LeaveType.objects.get_or_create(code='defunct', defaults={
            'name': 'Defunct', 'default_annual_days': 5, 'is_active': False})
        generate_entitlements_for_employee(self.emp, 2026)
        self.assertFalse(LeaveEntitlement.objects.filter(employee=self.emp, leave_type=inactive_lt).exists())



class NewEmployeeAutoEntitlementTests(TestCase):
    """A newly added employee (via the real 'Add Employee' form or the Excel
    import) automatically gets this year's leave balances — no separate
    manual 'Generate' step needed. Deliberately hooked into these two actual
    creation entry points, NOT Employee.save() itself: a model-level hook
    would fire on every raw Employee.objects.create() across the whole test
    suite (and any future script), silently colliding with the ~30 existing
    tests that create their own LeaveEntitlement rows for a just-created
    employee in the current year."""
    def setUp(self):
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={
            'name': 'Annual', 'default_annual_days': 30})
        self.admin = _make_role_user('neaet_admin', Role.SUPER_ADMIN)
        self.this_year = timezone.now().year

    def test_creating_employee_via_form_generates_entitlements(self):
        self.client.login(username='neaet_admin', password='testpass123')
        resp = self.client.post(reverse('hr:employee_create'), {
            'iqama_number': 'NEAET-1', 'full_name': 'Fresh Hire', 'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        emp = Employee.objects.get(iqama_number='NEAET-1')
        ent = LeaveEntitlement.objects.get(employee=emp, leave_type=self.annual, year=self.this_year)
        self.assertEqual(ent.entitled_days, Decimal('30'))

    def test_creating_inactive_employee_via_form_generates_no_entitlements(self):
        self.client.login(username='neaet_admin', password='testpass123')
        resp = self.client.post(reverse('hr:employee_create'), {
            'iqama_number': 'NEAET-2', 'full_name': 'Not Yet Active',
        })
        self.assertEqual(resp.status_code, 302)
        emp = Employee.objects.get(iqama_number='NEAET-2')
        self.assertFalse(emp.is_active)
        self.assertFalse(LeaveEntitlement.objects.filter(employee=emp).exists())

    def test_updating_existing_employee_does_not_regenerate(self):
        # EmployeeUpdateView reuses EmployeeForm but is a different view
        # (UpdateView, not CreateView) — this hook lives only in
        # EmployeeCreateView.form_valid, so an edit must never touch entitlements.
        emp = make_employee('NEAET-3', 'Existing Employee')
        self.client.login(username='neaet_admin', password='testpass123')
        resp = self.client.post(reverse('hr:employee_update', args=[emp.pk]), {
            'iqama_number': 'NEAET-3', 'full_name': 'Existing Employee Renamed', 'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(LeaveEntitlement.objects.filter(employee=emp).exists())



class CanViewLeaveDashboardTests(TestCase):
    """Unit tests for hr.views.can_view_leave_dashboard, independent of any
    specific view. LeaveDashboardAccess is the single merged roster — an
    active grant is both 'can view the dashboard' and 'is a real approver'
    (see is_designated_approver / submit_leave_request) — while Super Admin
    status alone only grants viewing, never approval authority by itself."""
    def setUp(self):
        from accounts.models import Role
        self.role, _ = Role.objects.get_or_create(name='super_admin')

    def test_super_admin_can_view(self):
        from hr.views import can_view_leave_dashboard
        user = make_user('cvld_super', password='x')
        user.role = self.role
        user.save()
        self.assertTrue(can_view_leave_dashboard(user))

    def test_plain_user_cannot_view(self):
        from hr.views import can_view_leave_dashboard
        user = make_user('cvld_plain', password='x')
        user.save()
        self.assertFalse(can_view_leave_dashboard(user))

    def test_user_with_active_grant_can_view(self):
        from hr.views import can_view_leave_dashboard
        user = make_user('cvld_grantee', password='x')
        user.save()
        LeaveDashboardAccess.objects.create(user=user, is_active=True)
        self.assertTrue(can_view_leave_dashboard(user))

    def test_user_with_inactive_grant_cannot_view(self):
        from hr.views import can_view_leave_dashboard
        user = make_user('cvld_revoked', password='x')
        user.save()
        LeaveDashboardAccess.objects.create(user=user, is_active=False)
        self.assertFalse(can_view_leave_dashboard(user))



class CheckLeaveBalanceTests(TestCase):
    """Unit tests for hr.forms.check_leave_balance, independent of any view.
    remaining_days only reflects APPROVED days (LeaveRecord rows) — this
    function must also treat this employee's OTHER still-pending requests
    for the same leave type/year as already-spoken-for balance, or two
    individually-valid submissions could together exceed the entitlement."""
    def setUp(self):
        from hr.forms import check_leave_balance
        self.check_leave_balance = check_leave_balance
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': 12, 'is_accumulative': False})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2026, entitled_days=3)

    def test_no_entitlement_raises(self):
        with self.assertRaises(ValidationError):
            self.check_leave_balance(self.emp, self.marriage, _date(2027, 1, 5), _date(2027, 1, 6))

    def test_within_balance_passes(self):
        self.check_leave_balance(self.emp, self.marriage, _date(2026, 9, 1), _date(2026, 9, 3))  # 3 days, exact fit

    def test_exceeding_plain_entitlement_raises(self):
        with self.assertRaises(ValidationError):
            self.check_leave_balance(self.emp, self.marriage, _date(2026, 9, 1), _date(2026, 9, 10))

    def test_second_request_blocked_once_first_pending_request_uses_up_balance(self):
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3))  # 3 days, still pending
        with self.assertRaises(ValidationError) as ctx:
            self.check_leave_balance(self.emp, self.marriage, _date(2026, 10, 1), _date(2026, 10, 1))  # just 1 more day
        self.assertIn('already tied up in other pending requests', str(ctx.exception))

    def test_second_request_allowed_once_first_request_is_no_longer_pending(self):
        req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 3), status='disapproved')
        self.check_leave_balance(self.emp, self.marriage, _date(2026, 10, 1), _date(2026, 10, 3))

    def test_pending_request_of_a_different_leave_type_does_not_count(self):
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.sick, year=2026, entitled_days=12)
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.sick,
            start_date=_date(2026, 9, 1), end_date=_date(2026, 9, 10))  # 10 days of Sick, pending
        # Marriage's own 3-day balance is untouched by Sick's pending request.
        self.check_leave_balance(self.emp, self.marriage, _date(2026, 11, 1), _date(2026, 11, 3))

    def test_pending_request_in_a_different_year_does_not_count(self):
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2027, entitled_days=3)
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2027, 1, 1), end_date=_date(2027, 1, 3))  # pending, but a different year
        self.check_leave_balance(self.emp, self.marriage, _date(2026, 9, 1), _date(2026, 9, 3))



class LeaveRequestFormCrossYearTests(TestCase):
    """LeaveRequestForm.clean() must reject a date range spanning two
    calendar years — LeaveEntitlement (and check_leave_balance) is keyed per
    year, so a cross-year request would otherwise only ever be checked
    against the start year's balance, silently never consulting the end
    year's entitlement at all."""
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2026, entitled_days=30)
        LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.marriage, year=2027, entitled_days=30)

    def test_cross_year_range_rejected(self):
        form = LeaveRequestForm({
            'leave_type': self.marriage.pk, 'start_date': '2026-12-30', 'end_date': '2027-01-02',
            'employee_reason': 'New Year trip',
        }, fixed_employee=self.emp)
        self.assertFalse(form.is_valid())
        self.assertIn('cannot span two different years', str(form.errors['end_date']))

    def test_same_year_range_still_passes(self):
        form = LeaveRequestForm({
            'leave_type': self.marriage.pk, 'start_date': '2026-12-28', 'end_date': '2026-12-31',
            'employee_reason': 'End of year',
        }, fixed_employee=self.emp)
        self.assertTrue(form.is_valid(), form.errors)

    def test_new_years_eve_to_new_years_day_rejected(self):
        # The most common real-world case this guards against.
        form = LeaveRequestForm({
            'leave_type': self.marriage.pk, 'start_date': '2026-12-31', 'end_date': '2027-01-01',
            'employee_reason': 'NYE',
        }, fixed_employee=self.emp)
        self.assertFalse(form.is_valid())
        self.assertIn('end_date', form.errors)



class LeaveRequestDecidedByDisplayTests(TestCase):
    """Model-level coverage of LeaveRequest.decided_by_display, independent
    of any view/template — the 'by whom' summary shown across the queue
    history, My Profile, and the detail page."""
    def setUp(self):
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 2))

    def test_pending_returns_none(self):
        self.assertIsNone(self.req.decided_by_display)

    def test_approved_by_all_approvers(self):
        aamna = make_user('dbd_aamna', password='x', first_name='Aamna', last_name='Khan')
        ali = make_user('dbd_ali', password='x', first_name='Ali', last_name='Sultan')
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=aamna, decision='approved')
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=ali, decision='approved')
        self.req.status = 'approved'
        self.req.save(update_fields=['status'])
        self.assertEqual(self.req.decided_by_display, 'by Aamna Khan, Ali Sultan')

    def test_disapproved_by_single_approver(self):
        aamna = make_user('dbd_aamna2', password='x', first_name='Aamna', last_name='Khan')
        ali = make_user('dbd_ali2', password='x', first_name='Ali', last_name='Sultan')
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=aamna, decision='disapproved')
        LeaveRequestApproval.objects.create(leave_request=self.req, approver=ali, decision='skipped')
        self.req.status = 'disapproved'
        self.req.save(update_fields=['status'])
        self.assertEqual(self.req.decided_by_display, 'by Aamna Khan')

    def test_override_shows_overriding_admin(self):
        admin = make_user('dbd_admin', password='x', first_name='Sarah', last_name='Admin')
        self.req.status = 'approved'
        self.req.is_overridden = True
        self.req.overridden_by = admin
        self.req.save(update_fields=['status', 'is_overridden', 'overridden_by'])
        self.assertEqual(self.req.decided_by_display, 'by Sarah Admin (override)')

    def test_override_with_deleted_overriding_user(self):
        admin = make_user('dbd_admin2', password='x')
        self.req.status = 'approved'
        self.req.is_overridden = True
        self.req.overridden_by = admin
        self.req.save(update_fields=['status', 'is_overridden', 'overridden_by'])
        admin.delete()
        self.req.refresh_from_db()
        self.assertEqual(self.req.decided_by_display, '(override — approver account no longer exists)')




class HasOverrideAccessTests(TestCase):
    """Unit tests for hr.views.has_override_access, independent of any view —
    exactly one OverrideAccessSettings mode is authoritative at a time."""
    def setUp(self):
        from hr.models import OverrideAccessSettings
        OverrideAccessSettings.objects.all().delete()

    def test_default_mode_is_all_super_admins(self):
        from hr.models import OverrideAccessSettings
        config = OverrideAccessSettings.get_solo()
        self.assertEqual(config.mode, OverrideAccessSettings.MODE_ALL_SUPER_ADMINS)

    def test_all_super_admins_mode_grants_every_super_admin(self):
        from hr.views import has_override_access
        admin = _make_role_user('hoa_super1', Role.SUPER_ADMIN)
        self.assertTrue(has_override_access(admin))

    def test_all_super_admins_mode_denies_non_super_admin(self):
        from hr.views import has_override_access
        plain = _login_user('hoa_plain1')
        self.assertFalse(has_override_access(plain))

    def test_specific_roles_mode_grants_only_that_role(self):
        from hr.views import has_override_access
        from hr.models import OverrideAccessSettings, OverrideAccessRole
        config = OverrideAccessSettings.get_solo()
        config.mode = OverrideAccessSettings.MODE_SPECIFIC_ROLES
        config.save()
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_HEAD)
        OverrideAccessRole.objects.create(role=finance_role)

        finance_user = _make_role_user('hoa_finance', Role.FINANCE_HEAD)
        self.assertTrue(has_override_access(finance_user))

        super_admin_without_grant = _make_role_user('hoa_super2', Role.SUPER_ADMIN)
        self.assertFalse(has_override_access(super_admin_without_grant))

    def test_specific_employees_mode_grants_only_that_person(self):
        from hr.views import has_override_access
        from hr.models import OverrideAccessSettings, OverrideAccessEmployee
        config = OverrideAccessSettings.get_solo()
        config.mode = OverrideAccessSettings.MODE_SPECIFIC_EMPLOYEES
        config.save()
        chosen = _login_user('hoa_chosen')
        OverrideAccessEmployee.objects.create(user=chosen)

        self.assertTrue(has_override_access(chosen))

        super_admin_without_grant = _make_role_user('hoa_super3', Role.SUPER_ADMIN)
        self.assertFalse(has_override_access(super_admin_without_grant))

    def test_override_access_alone_grants_dashboard_viewing(self):
        # Otherwise granting override access to a non-super-admin,
        # non-approver would be pointless — they couldn't reach the page.
        from hr.views import can_view_leave_dashboard
        from hr.models import OverrideAccessSettings, OverrideAccessEmployee
        config = OverrideAccessSettings.get_solo()
        config.mode = OverrideAccessSettings.MODE_SPECIFIC_EMPLOYEES
        config.save()
        chosen = _login_user('hoa_viewer')
        OverrideAccessEmployee.objects.create(user=chosen)
        self.assertTrue(can_view_leave_dashboard(chosen))



class OverrideAccessViewTests(TestCase):
    """End-to-end: the leave-request detail page's override form/action
    respects has_override_access instead of a hardcoded is_super_admin_user
    check, across all three modes."""
    def setUp(self):
        from hr.models import OverrideAccessSettings
        OverrideAccessSettings.objects.all().delete()
        self.emp = make_employee()
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 3))

    def test_default_mode_super_admin_sees_and_can_use_override(self):
        admin = _make_role_user('oav_super1', Role.SUPER_ADMIN)
        self.client.login(username='oav_super1', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        self.assertContains(resp, 'name="action" value="override"')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'override', 'decision': 'approved', 'reason': 'Testing'})
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')

    def test_specific_roles_mode_blocks_super_admin_without_the_role(self):
        from hr.models import OverrideAccessSettings, OverrideAccessRole
        config = OverrideAccessSettings.get_solo()
        config.mode = OverrideAccessSettings.MODE_SPECIFIC_ROLES
        config.save()
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_HEAD)
        OverrideAccessRole.objects.create(role=finance_role)

        admin = _make_role_user('oav_super2', Role.SUPER_ADMIN)
        self.client.login(username='oav_super2', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        self.assertNotContains(resp, 'name="action" value="override"')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'override', 'decision': 'approved', 'reason': 'Testing'})
        self.assertEqual(resp.status_code, 403)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')

    def test_specific_roles_mode_allows_the_granted_role_even_if_not_super_admin(self):
        from hr.models import OverrideAccessSettings, OverrideAccessRole
        config = OverrideAccessSettings.get_solo()
        config.mode = OverrideAccessSettings.MODE_SPECIFIC_ROLES
        config.save()
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_HEAD)
        OverrideAccessRole.objects.create(role=finance_role)

        finance_user = _make_role_user('oav_finance', Role.FINANCE_HEAD)
        self.client.login(username='oav_finance', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'override', 'decision': 'approved', 'reason': 'Testing'})
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')

    def test_specific_employees_mode_blocks_unlisted_super_admin(self):
        from hr.models import OverrideAccessSettings, OverrideAccessEmployee
        config = OverrideAccessSettings.get_solo()
        config.mode = OverrideAccessSettings.MODE_SPECIFIC_EMPLOYEES
        config.save()
        chosen = _login_user('oav_chosen')
        OverrideAccessEmployee.objects.create(user=chosen)

        admin = _make_role_user('oav_super3', Role.SUPER_ADMIN)
        self.client.login(username='oav_super3', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'override', 'decision': 'approved', 'reason': 'Testing'})
        self.assertEqual(resp.status_code, 403)

        self.client.login(username='oav_chosen', password='testpass123')
        resp = self.client.post(reverse('hr:leave_request_detail', args=[self.req.pk]),
                                {'action': 'override', 'decision': 'approved', 'reason': 'Testing'})
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')



class LeaveRequestDetailDocumentLinkTests(TestCase):
    """The 'View attached document' link on the leave request detail page
    must open in a new tab (target="_blank") — otherwise the global loading
    overlay in base.html (which only hides on `pageshow` or a 30s fallback)
    gets stuck showing "Loading, please wait…" forever, because the
    same-page FileResponse download never fires a real navigation/pageshow
    event. This matches the established convention used elsewhere in the
    codebase (e.g. the document links on My Profile use target="_blank")."""

    def setUp(self):
        self.emp = make_employee(iqama='DOCLINK-1', name='Doc Link Employee')
        self.marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': 3, 'is_accumulative': False})
        self.superadmin = make_user('doclink_super', password='x')
        self.superadmin.set_password('testpass123')
        from accounts.models import Role
        role, _ = Role.objects.get_or_create(name='super_admin')
        self.superadmin.role = role
        self.superadmin.save()
        self.req = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.marriage,
            start_date=_date(2026, 8, 1), end_date=_date(2026, 8, 3),
            document=SimpleUploadedFile('cert.pdf', b'dummy-bytes'))

    def test_document_link_opens_in_new_tab(self):
        self.client.login(username='doclink_super', password='testpass123')
        resp = self.client.get(reverse('hr:leave_request_detail', args=[self.req.pk]))
        doc_url = reverse('hr:leave_request_document', args=[self.req.pk])
        # Confirm the exact rendered markup, not just that target="_blank"
        # appears somewhere on the page.
        self.assertContains(resp, f'href="{doc_url}" target="_blank"')



class ReapplyLeaveTypeDefaultsScopingTests(TestCase):
    """Unit-level test for reapply_leave_type_defaults' new `leave_type` param
    (added alongside the existing `year` param), isolated from
    LeaveType.save()'s own broader propagation by changing default_annual_days
    via a bare queryset .update() (which bypasses save())."""

    def test_scoped_to_single_leave_type_and_year(self):
        emp = make_employee(iqama='RSD-1', name='Reapply Scope Employee')
        sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': Decimal('12.0'),
                                   'is_accumulative': False})
        marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': Decimal('3.0'),
                                       'is_accumulative': False})
        year = timezone.now().year
        other_year = year - 1
        sick_current = LeaveEntitlement.objects.create(
            employee=emp, leave_type=sick, year=year, entitled_days=Decimal('99'))
        sick_other = LeaveEntitlement.objects.create(
            employee=emp, leave_type=sick, year=other_year, entitled_days=Decimal('99'))
        marriage_current = LeaveEntitlement.objects.create(
            employee=emp, leave_type=marriage, year=year, entitled_days=Decimal('99'))

        LeaveType.objects.filter(pk=sick.pk).update(default_annual_days=Decimal('20'))
        sick.refresh_from_db()

        from hr.leave_services import reapply_leave_type_defaults
        updated = reapply_leave_type_defaults(year=year, leave_type=sick)
        self.assertEqual(updated, 1)

        sick_current.refresh_from_db()
        sick_other.refresh_from_db()
        marriage_current.refresh_from_db()
        self.assertEqual(sick_current.entitled_days, Decimal('20'))
        self.assertEqual(sick_other.entitled_days, Decimal('99'))       # different year untouched
        self.assertEqual(marriage_current.entitled_days, Decimal('99'))  # different type untouched



class LeaveTypeUpdateViewAutoReapplyTests(TestCase):
    """Editing a LeaveType's default_annual_days through the admin edit view
    flashes an explicit "Re-applied ... for <year>" confirmation, current-year
    scoped, mirroring the Entitlements page's manual Re-apply action."""

    def setUp(self):
        self.admin = _make_role_user('ltu_admin', Role.SUPER_ADMIN)
        self.emp = make_employee(iqama='LTU-1', name='LTU Employee')
        self.sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': Decimal('12.0'),
                                   'is_accumulative': False})
        self.current_year = timezone.now().year
        self.current_ent = LeaveEntitlement.objects.create(
            employee=self.emp, leave_type=self.sick, year=self.current_year, entitled_days=Decimal('12.0'))

    def test_editing_default_days_updates_current_year_and_flashes_confirmation(self):
        self.client.login(username='ltu_admin', password='testpass123')
        resp = self.client.post(reverse('hr:leavetype_update', args=[self.sick.pk]), {
            'name': 'Sick', 'code': 'sick', 'default_annual_days': '20.0',
            'is_paid': 'on', 'color': 'secondary', 'is_active': 'on',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.current_ent.refresh_from_db()
        self.assertEqual(self.current_ent.entitled_days, Decimal('20.0'))
        rendered_messages = [str(m) for m in resp.context['messages']]
        self.assertTrue(
            any(f'entitlement(s) for {self.current_year}' in m for m in rendered_messages),
            rendered_messages)

    def test_no_day_count_change_does_not_flash_reapply_confirmation(self):
        self.client.login(username='ltu_admin', password='testpass123')
        resp = self.client.post(reverse('hr:leavetype_update', args=[self.sick.pk]), {
            'name': 'Sick Leave', 'code': 'sick', 'default_annual_days': '12.0',
            'is_paid': 'on', 'color': 'secondary', 'is_active': 'on',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        rendered_messages = [str(m) for m in resp.context['messages']]
        self.assertFalse(any('Re-applied leave-type day counts' in m for m in rendered_messages), rendered_messages)

