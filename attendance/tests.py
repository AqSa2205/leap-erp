import json
from datetime import time

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from hr.models import Employee
from attendance.models import OfficeNetwork, RegisteredDevice, Heartbeat, AttendanceDay

OFFICE_BSSID = 'a1:b2:c3:d4:e5:f6'


def _wide_hours():
    # Work window that always contains "now" so tests aren't clock-dependent.
    return override_settings(ATT_WORK_START='00:00', ATT_WORK_END='23:59',
                             ATT_MAX_IDLE_SECONDS=300, ATT_MIN_MINUTES_PRESENT=1)


class CheckInTests(TestCase):
    def setUp(self):
        self.emp = Employee.objects.create(iqama_number='ATT-1', full_name='Test Emp', is_active=True)
        self.device = RegisteredDevice.objects.create(employee=self.emp, label='LT-1')
        OfficeNetwork.objects.create(bssid=OFFICE_BSSID, label='HO', is_active=True)
        self.url = reverse('attendance:checkin')

    def _post(self, **payload):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type='application/json')

    def test_unknown_token_rejected(self):
        r = self._post(token='nope', bssid=OFFICE_BSSID, idle_seconds=0)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.json()['ok'])

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_token_auth_needs_no_csrf(self):
        # The agent isn't a browser — a CSRF-enforcing client must still succeed.
        c = self.client.__class__(enforce_csrf_checks=True)
        with _wide_hours():
            r = c.post(self.url, data=json.dumps(
                {'token': self.device.token, 'bssid': OFFICE_BSSID, 'idle_seconds': 0}),
                content_type='application/json')
        self.assertEqual(r.status_code, 200)

    def test_office_active_ping_counts(self):
        with _wide_hours():
            r = self._post(token=self.device.token, bssid=OFFICE_BSSID, idle_seconds=10)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['counted'])
        day = AttendanceDay.objects.get(employee=self.emp)
        self.assertEqual(day.active_minutes, 1)
        self.assertTrue(day.is_present)          # ATT_MIN_MINUTES_PRESENT=1 in test
        self.assertIsNotNone(day.first_seen)
        self.assertTrue(Heartbeat.objects.filter(device=self.device, counted=True).exists())
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen_at)

    def test_wrong_network_not_counted(self):
        with _wide_hours():
            r = self._post(token=self.device.token, bssid='ff:ff:ff:ff:ff:ff', idle_seconds=0)
        self.assertFalse(r.json()['counted'])
        self.assertEqual(r.json()['reason'], 'off_network')
        self.assertFalse(AttendanceDay.objects.filter(employee=self.emp).exists())
        # A non-counting ping is still logged (with its reason).
        self.assertTrue(Heartbeat.objects.filter(device=self.device, counted=False, reason='off_network').exists())

    def test_idle_not_counted(self):
        with _wide_hours():
            r = self._post(token=self.device.token, bssid=OFFICE_BSSID, idle_seconds=9999)
        self.assertFalse(r.json()['counted'])
        self.assertEqual(r.json()['reason'], 'idle')

    @override_settings(ATT_WORK_START='23:58', ATT_WORK_END='23:59',
                       ATT_MAX_IDLE_SECONDS=300, ATT_MIN_MINUTES_PRESENT=1)
    def test_off_hours_not_counted(self):
        # A window that (almost certainly) excludes "now".
        now_t = timezone.localtime(timezone.now()).time()
        if time(23, 58) <= now_t <= time(23, 59):
            self.skipTest('ran during the tiny test window')
        r = self._post(token=self.device.token, bssid=OFFICE_BSSID, idle_seconds=0)
        self.assertFalse(r.json()['counted'])
        self.assertEqual(r.json()['reason'], 'off_hours')

    def test_second_ping_same_minute_does_not_double_count(self):
        with _wide_hours():
            self._post(token=self.device.token, bssid=OFFICE_BSSID, idle_seconds=0)
            self._post(token=self.device.token, bssid=OFFICE_BSSID, idle_seconds=0)
        day = AttendanceDay.objects.get(employee=self.emp)
        self.assertEqual(day.active_minutes, 1)   # same wall-clock minute → counted once


class HRSyncTests(TestCase):
    def setUp(self):
        self.emp = Employee.objects.create(iqama_number='ATT-S1', full_name='Sync Emp', is_active=True)
        self.device = RegisteredDevice.objects.create(employee=self.emp, label='LT-S1')
        OfficeNetwork.objects.create(bssid=OFFICE_BSSID, label='HO', is_active=True)
        self.url = reverse('attendance:checkin')

    def _ping(self):
        with _wide_hours():
            return self.client.post(self.url, data=json.dumps(
                {'token': self.device.token, 'bssid': OFFICE_BSSID, 'idle_seconds': 0}),
                content_type='application/json')

    def test_counted_ping_creates_hr_record(self):
        from hr.models import AttendanceRecord
        self._ping()
        rec = AttendanceRecord.objects.get(employee=self.emp)
        self.assertEqual(rec.source, 'wifi')
        self.assertIn(rec.status, ('present', 'late'))
        self.assertIsNotNone(rec.check_in)

    def test_manual_entry_not_clobbered(self):
        from datetime import time as t, date
        from hr.models import AttendanceRecord
        from django.utils import timezone
        today = timezone.localdate()
        manual = AttendanceRecord.objects.create(
            employee=self.emp, date=today, check_in=t(8, 0), check_out=t(17, 0),
            status='present', source='manual', note='HR entered')
        self._ping()
        manual.refresh_from_db()
        # The manual check-in / note / source are preserved.
        self.assertEqual(manual.check_in, t(8, 0))
        self.assertEqual(manual.note, 'HR entered')
        self.assertEqual(manual.source, 'manual')

    def test_leave_status_not_clobbered(self):
        from hr.models import AttendanceRecord
        from django.utils import timezone
        rec = AttendanceRecord.objects.create(
            employee=self.emp, date=timezone.localdate(), status='leave', source='manual')
        self._ping()
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'leave')
        self.assertEqual(rec.source, 'manual')

    def test_bare_absent_placeholder_upgraded(self):
        # A generated 'absent' with no check-in is upgraded by real presence.
        from hr.models import AttendanceRecord
        from django.utils import timezone
        rec = AttendanceRecord.objects.create(
            employee=self.emp, date=timezone.localdate(), status='absent', source='manual')
        self._ping()
        rec.refresh_from_db()
        self.assertEqual(rec.source, 'wifi')
        self.assertIn(rec.status, ('present', 'late'))
        self.assertIsNotNone(rec.check_in)


class RegistrationUITests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        self.admin = User.objects.create_user(
            'wa_admin', password='pw',
            role=Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0])
        self.emp = Employee.objects.create(iqama_number='ATT-U1', full_name='UI Emp', is_active=True)
        self.url = reverse('attendance_ui:attendance_devices')

    def test_non_admin_blocked(self):
        from accounts.models import Role, User
        u = User.objects.create_user('sales', password='pw',
                                     role=Role.objects.get_or_create(name=Role.SALES_REP)[0])
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_admin_can_add_network_and_device(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        # Add an office AP.
        self.client.post(self.url, {'action': 'add_network', 'bssid': 'A1:B2:C3:D4:E5:F6', 'label': 'HO'})
        self.assertTrue(OfficeNetwork.objects.filter(bssid='a1:b2:c3:d4:e5:f6').exists())
        # Register a device — a token is generated.
        self.client.post(self.url, {'action': 'add_device', 'employee': self.emp.pk, 'label': 'LT'})
        dev = RegisteredDevice.objects.get(employee=self.emp)
        self.assertTrue(dev.token)

    def test_invalid_bssid_rejected(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'add_network', 'bssid': 'not-a-bssid', 'label': 'x'})
        self.assertFalse(OfficeNetwork.objects.exists())

    def test_toggle_and_delete_device(self):
        self.client.force_login(self.admin)
        dev = RegisteredDevice.objects.create(employee=self.emp, label='LT', is_active=True)
        self.client.post(self.url, {'action': 'toggle_device', 'pk': dev.pk})
        dev.refresh_from_db()
        self.assertFalse(dev.is_active)
        self.client.post(self.url, {'action': 'delete_device', 'pk': dev.pk})
        self.assertFalse(RegisteredDevice.objects.filter(pk=dev.pk).exists())

    def test_register_device_with_asset_autofills_serial(self):
        from datetime import date
        from hr.models import Asset, AssetAssignment
        self.client.force_login(self.admin)
        asset = Asset.objects.create(asset_name='Dell 5540', asset_type='Laptop', serial_number='SN-9')
        AssetAssignment.objects.create(asset=asset, employee=self.emp, assigned_at=date(2026, 1, 1))
        # The page carries the employee's assets for the dropdown.
        self.assertContains(self.client.get(self.url), 'SN-9')
        # Registering with the asset links it and auto-fills the serial.
        self.client.post(self.url, {'action': 'add_device', 'employee': self.emp.pk,
                                    'asset_id': asset.pk, 'serial_number': '', 'label': 'LT'})
        dev = RegisteredDevice.objects.get(employee=self.emp)
        self.assertEqual(dev.asset_id, asset.pk)
        self.assertEqual(dev.serial_number, 'SN-9')

    def test_token_csv_export(self):
        self.client.force_login(self.admin)
        dev = RegisteredDevice.objects.create(employee=self.emp, label='LT')
        r = self.client.get(reverse('attendance_ui:attendance_tokens_export'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn(dev.token, r.content.decode())


class ModelTests(TestCase):
    def test_bssid_normalised_lowercase(self):
        n = OfficeNetwork.objects.create(bssid='A1:B2:C3:D4:E5:F6  ')
        self.assertEqual(n.bssid, 'a1:b2:c3:d4:e5:f6')

    def test_device_token_auto_and_unique(self):
        e = Employee.objects.create(iqama_number='ATT-2', full_name='E2', is_active=True)
        d1 = RegisteredDevice.objects.create(employee=e)
        d2 = RegisteredDevice.objects.create(employee=e)
        self.assertTrue(d1.token and d2.token)
        self.assertNotEqual(d1.token, d2.token)
