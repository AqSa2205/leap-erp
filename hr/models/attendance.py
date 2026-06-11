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
