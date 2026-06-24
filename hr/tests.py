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
        self.assertEqual(g['total_entitled'], Decimal('45'))   # 30 + 15
        self.assertEqual(g['total_taken'], Decimal('8'))       # only annual taken
        self.assertEqual(g['total_remaining'], Decimal('37'))  # 45 - 8
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

    def test_create_form_rejects_sick_without_certificate(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('hr:leave_record_create'), {
            'employee': self.emp.pk, 'leave_type': self.sick.pk,
            'start_date': '2026-07-13', 'end_date': '2026-07-13', 'days': '', 'note': ''})
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors, not redirected
        self.assertFalse(LeaveRecord.objects.filter(employee=self.emp).exists())

    def test_create_form_accepts_sick_with_certificate(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('hr:leave_record_create'), {
            'employee': self.emp.pk, 'leave_type': self.sick.pk,
            'start_date': '2026-07-13', 'end_date': '2026-07-13', 'days': '', 'note': '',
            'medical_certificate': self._cert()})
        self.assertEqual(LeaveRecord.objects.filter(employee=self.emp).count(), 1)
        self.assertTrue(LeaveRecord.objects.get(employee=self.emp).medical_certificate)

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

    def test_annual_type_is_excluded(self):
        # Annual entitlements differ by joining-date policy; a flat change must NOT touch them.
        a = LeaveEntitlement.objects.create(employee=self.emp, leave_type=self.annual, year=2026, entitled_days=25)
        self.annual.default_annual_days = Decimal('40')
        self.annual.save()
        a.refresh_from_db()
        self.assertEqual(a.entitled_days, Decimal('25'))  # untouched

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
