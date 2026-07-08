from datetime import time
from django.db import models
from django.conf import settings


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date:%Y-%m-%d} {self.name}"


class AttendanceSettings(models.Model):
    """Singleton (pk=1) holding global attendance config."""
    weekend_days = models.CharField(
        max_length=20, default='4,5',
        help_text='Comma-separated weekday numbers, Mon=0..Sun=6. Default 4,5 = Fri,Sat.')
    expected_in_by = models.TimeField(
        default=time(8, 30),
        help_text='Check-ins after this time are marked Late.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Attendance settings'

    def __str__(self):
        return 'Attendance settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def weekend_day_set(self):
        out = set()
        for part in (self.weekend_days or '').split(','):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out


class AttendanceRecord(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'), ('absent', 'Absent'), ('leave', 'Leave'),
        ('holiday', 'Holiday'), ('weekend', 'Weekend'),
        ('late', 'Late'), ('wfh', 'WFH'),
    ]
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='attendance')
    date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='absent')
    SOURCE_CHOICES = [('manual', 'Manual'), ('wifi', 'Auto (Wi-Fi)')]
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default='manual',
        help_text='How this record was set — manual entry or the Wi-Fi attendance agent.')
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
        ordering = ['-date']
        indexes = [models.Index(fields=['date', 'status']), models.Index(fields=['employee', 'date'])]

    def __str__(self):
        return f"{self.employee.full_name} {self.date} {self.status}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.check_in and self.check_out and self.check_out < self.check_in:
            raise ValidationError({'check_out': 'Check-out cannot be before check-in.'})


class WorkingDay(models.Model):
    """A normally-weekend date that is a working day (inverse of Holiday)."""
    date = models.DateField(unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date:%Y-%m-%d} {self.name}"


class WFHRecord(models.Model):
    """A work-from-home period. Worked time (no leave balance)."""
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='wfh_records')
    start_date = models.DateField()
    end_date = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        indexes = [models.Index(fields=['employee', 'start_date'])]

    def __str__(self):
        return f"WFH {self.employee.full_name} {self.start_date}..{self.end_date}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})
