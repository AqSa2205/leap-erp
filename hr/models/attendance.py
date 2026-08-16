from datetime import time, timedelta
from django.db import models
from django.conf import settings


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=150)
    CATEGORY_CHOICES = [
        ('saudi_national_day', 'Saudi National Day'),
        ('eid', 'Eid'),
        ('other', 'Other'),
    ]
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
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


    def save(self, *args, **kwargs):
        is_new = self._state.adding
        was_late_before = None
        if not is_new:
            was_late_before = AttendanceRecord.objects.filter(pk=self.pk).values_list('status', flat=True).first() == 'late'
        super().save(*args, **kwargs)
        just_became_late = self.status == 'late' and not was_late_before
        if just_became_late:
            self._maybe_notify_late_threshold()

    def _maybe_notify_late_threshold(self):
        # Fires an email once, exactly when this employee's late count for the
        # current calendar month reaches 3 - not on every late after that.
        month_start = self.date.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        late_count = AttendanceRecord.objects.filter(
            employee=self.employee, status='late',
            date__gte=month_start, date__lt=next_month).count()
        if late_count == 3:
            self._notify_late_threshold(late_count, month_start)

    def _notify_late_threshold(self, late_count, month_start):
        from notifications.services import create_notification, notify_users
        from accounts.models import User
        emp = self.employee
        month_label = month_start.strftime('%B %Y')
        employee_verb = 'You were late 3 times this month'
        employee_description = 'You were late thrice this month. An email has been sent to you.'
        hr_verb = f'{emp.full_name} was late 3 times this month'
        hr_description = f'{emp.full_name} was late 3 times this month. An email has been sent.'
        from django.urls import reverse
        if emp.user_id:
            create_notification(
                recipient=emp.user, verb=employee_verb, target=self,
                description=employee_description, level='warning',
                target_url=reverse('hr:my_profile'),
            )
        admins = User.objects.filter(is_active=True)
        hr_admins = [u for u in admins if (u.is_super_admin_user or u.is_admin_user or u.is_erp_admin_user) and u.pk != emp.user_id]
        notify_users(
            recipients=hr_admins, verb=hr_verb, target=self,
            description=hr_description, level='warning',
            target_url=reverse('hr:attendance_grid'),
        )
        self._send_late_email(late_count, month_start)

    def _send_late_email(self, late_count, month_start):
        # Attaches the same monthly attendance PDF used on My Profile - sent
        # in a background thread so saving attendance never waits on the
        # network call to actually deliver it.
        import threading
        from django.conf import settings
        from django.core.mail import EmailMessage
        from django.db import connections
        import logging
        logger = logging.getLogger(__name__)
        emp = self.employee
        month_label = month_start.strftime('%B %Y')
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if not (emp.user_id and emp.user.email):
            return
        to_email = emp.user.email

        def _send():
            try:
                from hr.views import build_attendance_pdf
                buffer = build_attendance_pdf(emp, month_start, month_end)
                msg = EmailMessage(
                    subject=f'Attendance Notice - {late_count} Late Arrivals in {month_label}',
                    body=(
                        f'{emp.full_name},\nYou have been marked Late {late_count} times in {month_label}. '
                        f'Your attendance record for the month is attached.\nLeap ERP'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[to_email],
                )
                msg.attach(f'{emp.full_name}_Attendance.pdf', buffer.getvalue(), 'application/pdf')
                msg.send(fail_silently=False)
            except Exception:
                logger.exception('Failed to send late-attendance email to %s', to_email)
            finally:
                connections.close_all()

        threading.Thread(target=_send, daemon=True).start()
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
