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


# Lates in one calendar month that earn a warning. Named because the
# Attendance Register recomputes warnings from the same rule - if this moves,
# both have to move together or the register will report warnings that were
# never sent.
LATE_WARNING_THRESHOLD = 3


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
        if late_count == LATE_WARNING_THRESHOLD:
            self._notify_late_threshold(late_count, month_start)

    def _notify_late_threshold(self, late_count, month_start):
        from notifications.services import create_notification, notify_users
        from accounts.models import User
        emp = self.employee
        month_label = month_start.strftime('%B %Y')
        n = LATE_WARNING_THRESHOLD
        employee_verb = f'You were late {n} times this month'
        employee_description = (
            f'You were late {n} times this month. An email has been sent to you.')
        hr_verb = f'{emp.full_name} was late {n} times this month'
        hr_description = (
            f'{emp.full_name} was late {n} times this month. An email has been sent.')
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
        from django.core.mail import EmailMultiAlternatives
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
                plain_body = (
                    'Dear Employee,\n\n'
                    'This is an automated notification regarding your attendance record.\n\n'
                    'Our system indicates that you have recorded late arrivals on three occasions '
                    'within the current cycle. In accordance with company policy, three late arrivals '
                    'are counted as one day of absence. If you believe this notification is incorrect '
                    'or wish to discuss your attendance record, please contact the HR Department.\n\n'
                    'Your attendance PDF is attached.\n\n'
                    'Kind regards,\n'
                    'Admin Team\n\n'
                    'Leap Networks Arabia\n'
                    'P.O. Box \u2013 70005, Al-Khobar-31952, Kingdom of Saudi Arabia\n'
                    'TEL: (+966) 13 8491867 X 108\n'
                    'Web: www.leap-arabia.com\n\n'
                    'Disclaimer: This email and its attachments are confidential and intended only for '
                    'the recipient(s). If you are not the intended recipient, please notify the sender '
                    'and delete the message. Unauthorized use, disclosure, or distribution of the email '
                    'or the documents is prohibited. Leap Networks Arabia complies with GDPR and ensures '
                    'the security of personal data. While we take steps to protect against malware, we '
                    "cannot guarantee the email's security. Please verify any information before acting on it."
                )
                html_body = _build_late_attendance_email_html()
                msg = EmailMultiAlternatives(
                    subject=f'Attendance Notice - {late_count} Late Arrivals in {month_label}',
                    body=plain_body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[to_email],
                )
                msg.attach_alternative(html_body, 'text/html')
                msg.attach(f'{emp.full_name}_Attendance.pdf', buffer.getvalue(), 'application/pdf')
                msg.send(fail_silently=False)
            except Exception:
                logger.exception('Failed to send late-attendance email to %s', to_email)
            finally:
                connections.close_all()

        threading.Thread(target=_send, daemon=True).start()


def _build_late_attendance_email_html():
    """Build the branded HTML version of the 3-lates attendance email,
    matching accounts.views._build_reset_email_html's styling. Content is
    fixed (no per-employee variables) - the greeting is deliberately
    generic ('Dear Employee'), not personalised."""
    return '''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f4f4f4; font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4; padding:30px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 4px 20px rgba(0,0,0,0.08);">

    <!-- Header -->
    <tr>
        <!-- bgcolor is the Outlook fallback: it renders mail through Word,
             which ignores CSS gradients, and without it this header loses its
             background entirely and the white heading below becomes invisible.
             Clients that do support the gradient paint over the flat colour. -->
        <td bgcolor="#C41E3A" style="background:linear-gradient(135deg,#C41E3A,#a01830); padding:35px 40px; text-align:center;">
            <h1 style="color:#ffffff; margin:0; font-size:22px; font-weight:700; letter-spacing:0.5px;">Attendance Notice</h1>
        </td>
    </tr>

    <!-- Body -->
    <tr>
        <td style="padding:40px;">
            <p style="color:#333; font-size:16px; margin:0 0 20px;">Dear Employee,</p>

            <p style="color:#555; font-size:14px; line-height:1.7; margin:0 0 20px;">
                This is an automated notification regarding your attendance record.
            </p>

            <p style="color:#555; font-size:14px; line-height:1.7; margin:0 0 20px;">
                Our system indicates that you have recorded late arrivals on three occasions within the current cycle.
                In accordance with company policy, three late arrivals are counted as one day of absence.
                If you believe this notification is incorrect or wish to discuss your attendance record,
                please contact the HR Department.
            </p>

            <p style="color:#555; font-size:14px; line-height:1.7; margin:0 0 30px;">
                Your attendance PDF is attached.
            </p>

            <p style="color:#333; font-size:14px; margin:0 0 4px;">Kind regards,</p>
            <p style="color:#333; font-size:14px; margin:0 0 15px;">Admin Team</p>
            <img src="https://leap-erp.onrender.com/static/images/leap_logo.jpg" alt="Leap Networks Arabia" style="max-width:160px; margin-bottom:20px; display:block;" />

            <p style="color:#777; font-size:12px; line-height:1.6; margin:0;">
                Leap Networks Arabia<br>
                P.O. Box &ndash; 70005, Al-Khobar-31952, Kingdom of Saudi Arabia<br>
                TEL: (+966) 13 8491867 X 108<br>
                Web: <a href="https://www.leap-arabia.com" style="color:#C41E3A; text-decoration:none;">www.leap-arabia.com</a>
            </p>
        </td>
    </tr>

    <!-- Footer / Disclaimer -->
    <tr>
        <td style="background:#2a2a2a; padding:22px 40px;">
            <p style="color:#999; font-size:10px; line-height:1.6; margin:0;">
                Disclaimer: This email and its attachments are confidential and intended only for the recipient(s).
                If you are not the intended recipient, please notify the sender and delete the message.
                Unauthorized use, disclosure, or distribution of the email or the documents is prohibited.
                Leap Networks Arabia complies with GDPR and ensures the security of personal data.
                While we take steps to protect against malware, we cannot guarantee the email's security.
                Please verify any information before acting on it.
            </p>
        </td>
    </tr>

</table>
</td></tr>
</table>
</body>
</html>'''


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


class MonthlyLatenessReport(models.Model):
    """Month-end snapshot of an employee's lateness for a given month and
    the resulting policy consequence (every 3 late arrivals = 1 day of
    absence). A pure record/summary - never modifies AttendanceRecord
    rows or any existing absence/leave totals; it exists purely so HR
    and the employee can see the pattern and the calculated impact
    together. late_dates is snapshotted at generation time (not derived
    live), so the report reflects what was true when it ran even if
    underlying records are corrected afterward.

    Generated once per employee per month by the
    generate_monthly_lateness_reports management command, on the last
    day of the month - only for employees with at least one late
    arrival that month."""
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='lateness_reports')
    month = models.DateField(help_text='First day of the reported month.')
    total_lates = models.PositiveIntegerField()
    late_dates = models.JSONField(default=list, help_text='ISO date strings, snapshotted at generation time.')
    converted_absences = models.PositiveIntegerField()
    email_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'month')
        ordering = ['-month']
        indexes = [models.Index(fields=['month'])]

    def __str__(self):
        return f"{self.employee.full_name} - {self.month.strftime('%B %Y')} ({self.total_lates} lates)"

    @property
    def late_dates_display(self):
        """['2026-08-03', '2026-08-05', ...] -> '3 Aug, 5 Aug' - shared with
        the email body via hr.lateness_report_services._format_late_dates,
        so the two never drift out of sync."""
        from hr.lateness_report_services import _format_late_dates
        return _format_late_dates(self.late_dates)
