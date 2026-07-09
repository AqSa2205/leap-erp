"""Wi-Fi based automatic attendance.

An agent on each employee laptop pings the ERP every minute with the BSSID of
the Wi-Fi it's connected to + how long the user has been idle. A ping only
counts toward attendance when the laptop is on a *registered office AP*, the
user is *active* (idle below the threshold), and it's *within work hours*.
Counted pings roll up into a per-employee-per-day AttendanceDay.
"""
import secrets

from django.db import models


def _gen_token():
    return secrets.token_urlsafe(32)


class OfficeNetwork(models.Model):
    """A registered office access point. A heartbeat only counts when the
    laptop is connected to one of these BSSIDs — so being on a phone hotspot
    or a café network never registers as office attendance."""

    bssid = models.CharField(
        max_length=17, unique=True,
        help_text='Access-point MAC (BSSID), lowercase — e.g. a1:b2:c3:d4:e5:f6. '
                  'Read one per AP with: netsh wlan show interfaces.')
    label = models.CharField(max_length=100, blank=True, help_text='e.g. "HO 2nd floor AP"')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['label', 'bssid']

    def save(self, *args, **kwargs):
        self.bssid = (self.bssid or '').strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.label or "AP"} ({self.bssid})'


class RegisteredDevice(models.Model):
    """An employee laptop running the attendance agent. Authenticates to the
    check-in endpoint by its unique token (revoke by unticking is_active)."""

    employee = models.ForeignKey(
        'hr.Employee', on_delete=models.CASCADE, related_name='attendance_devices')
    asset = models.ForeignKey(
        'hr.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        help_text="The employee's laptop from the HR asset register.")
    serial_number = models.CharField(
        max_length=255, blank=True, help_text='Auto-filled from the selected asset.')
    label = models.CharField(
        max_length=120, blank=True, help_text='e.g. hostname or asset tag')
    token = models.CharField(max_length=64, unique=True, default=_gen_token, editable=False)
    is_active = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField(
        null=True, blank=True,
        help_text='First successful office check-in — the day auto-attendance activated for this device.')
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee_id', 'label']

    def __str__(self):
        return f'{self.employee} — {self.label or self.token[:8]}'


class Heartbeat(models.Model):
    """A single ping from an agent — one row per machine per minute, so this
    table grows fast; prune it with `manage.py prune_heartbeats`."""

    device = models.ForeignKey(
        RegisteredDevice, on_delete=models.CASCADE, related_name='heartbeats')
    bssid = models.CharField(max_length=17, blank=True)
    idle_seconds = models.PositiveIntegerField(default=0)
    counted = models.BooleanField(default=False, help_text='Passed all checks and rolled into the day.')
    reason = models.CharField(
        max_length=40, blank=True,
        help_text='Why it did not count: off_network / idle / off_hours.')
    hostname = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['device', 'created_at'])]

    def __str__(self):
        return f'{self.device_id} @ {self.created_at:%Y-%m-%d %H:%M} ({"ok" if self.counted else self.reason})'


class AttendanceDay(models.Model):
    """Derived per-employee-per-day presence, aggregated from counted
    heartbeats. This is what the reporting/dashboard reads."""

    employee = models.ForeignKey(
        'hr.Employee', on_delete=models.CASCADE, related_name='attendance_days')
    date = models.DateField()
    first_seen = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    active_minutes = models.PositiveIntegerField(
        default=0, help_text='Distinct minutes with a counted heartbeat (idle excluded).')
    is_present = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
        ordering = ['-date', 'employee_id']

    def __str__(self):
        return f'{self.employee} — {self.date} ({self.active_minutes} min)'
