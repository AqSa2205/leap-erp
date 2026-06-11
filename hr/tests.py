from datetime import date
from django.test import TestCase
from django.urls import reverse
from hr.work_calendar import count_working_days, is_working_day
from hr.models import LeaveType


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
from hr.models import Employee, LeaveEntitlement, LeaveRecord


def make_employee(iqama='E1', name='Ali', joining=None):
    return Employee.objects.create(iqama_number=iqama, full_name=name, joining_date=joining)


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


from hr.leave_services import annual_entitlement_for, generate_year_entitlements


class AnnualRuleTests(TestCase):
    def test_joining_year_is_25(self):
        self.assertEqual(annual_entitlement_for(_date(2025, 7, 1), 2025), Decimal('25'))

    def test_year_after_joining_is_30(self):
        self.assertEqual(annual_entitlement_for(_date(2025, 7, 1), 2026), Decimal('30'))
        self.assertEqual(annual_entitlement_for(_date(2025, 7, 1), 2027), Decimal('30'))

    def test_no_joining_date_defaults_30(self):
        self.assertEqual(annual_entitlement_for(None, 2026), Decimal('30'))


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

    def test_generates_rows_for_active_employees_with_rule(self):
        generate_year_entitlements(2026)
        self.assertEqual(self._ent(self.e_new, self.annual), Decimal('25'))  # joining year
        self.assertEqual(self._ent(self.e_old, self.annual), Decimal('30'))  # tenured
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


class LeaveRecordViewTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        self.admin = User.objects.create_user('adm', password='x'); self.admin.role = role; self.admin.save()
        self.emp = make_employee()
        self.annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

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

    def test_explicit_zero_days_preserved(self):
        self.client.force_login(self.admin)
        # A leave that spans only Fri+Sat would compute 0 working days; recording
        # an explicit 0 must be kept, not overwritten by auto-compute.
        self.client.post(reverse('hr:leave_record_create'), {
            'employee': self.emp.pk, 'leave_type': self.annual.pk,
            'start_date': '2026-07-10', 'end_date': '2026-07-11', 'days': '0', 'note': ''})
        rec = LeaveRecord.objects.get(employee=self.emp)
        self.assertEqual(rec.days, Decimal('0'))

    def test_bad_year_param_does_not_500(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:leave_summary', kwargs={'pk': self.emp.pk}) + '?year=abc')
        self.assertEqual(resp.status_code, 200)

    def test_grid_renders_present_presets_on_working_row(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('hr:attendance_grid') + '?date=2026-07-13')  # Monday (working)
        self.assertContains(resp, 'Mark all present')
        # The per-row Present button carries the employee pk in data-pk (only the
        # rendered button has the literal `data-pk=`; the JS uses .dataset.pk).
        self.assertContains(resp, 'data-pk="%d"' % self.emp.pk)
        self.assertContains(resp, '08:15')   # default check-in baked into the JS
        self.assertContains(resp, '18:00')   # default check-out (6:00 PM)
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
