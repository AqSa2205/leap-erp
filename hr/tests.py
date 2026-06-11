from datetime import date
from django.test import TestCase
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
        lt = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=21)
        self.assertEqual(str(lt), 'Annual')
        self.assertTrue(lt.is_paid)  # default True

    def test_code_is_unique(self):
        from django.db import IntegrityError
        LeaveType.objects.create(name='Sick', code='sick', default_annual_days=30)
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
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)

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
        self.annual = LeaveType.objects.create(name='Annual', code='annual', default_annual_days=30)

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
