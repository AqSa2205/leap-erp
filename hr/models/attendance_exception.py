from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# Manager decision window: opens this long before the event starts, closes
# this long after it starts. Outside this window, decide/override calls in
# the service layer reject with a clear message.
DECISION_WINDOW_BEFORE = timedelta(days=7)
DECISION_WINDOW_AFTER = timedelta(hours=24)


class AttendanceException(models.Model):
    """An employee's request to excuse an off-site event (site visit, outside
    meeting, etc.) that would otherwise get them flagged late/absent by the
    Wi-Fi attendance tracker. Approved by the employee's manager (snapshotted
    at submission, so a later reassignment doesn't retroactively change who
    owned the decision); HR or anyone upstream in the manager chain can
    override with a mandatory reason. Left undecided 24h after the event
    starts, it auto-expires and the day is marked absent."""

    REASON_CHOICES = [
        ('site_visit', 'Site Visit'),
        ('outside_meeting', 'Outside Meeting'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ]

    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='attendance_exceptions')
    event_date = models.DateField()
    event_start_time = models.TimeField()
    reason_category = models.CharField(max_length=20, choices=REASON_CHOICES)
    custom_reason = models.TextField(blank=True)
    employee_comment = models.TextField(
        blank=True, help_text='Optional extra context from the employee, independent of the reason category.')
    # Snapshotted from employee.main_manager at submission time — see docstring.
    main_manager = models.ForeignKey(
        'hr.Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='attendance_exceptions_to_decide')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    decision_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    is_overridden = models.BooleanField(default=False)
    overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    override_reason = models.TextField(blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event_date', '-event_start_time']
        indexes = [models.Index(fields=['status', 'event_date'])]

    def __str__(self):
        return f"{self.employee.full_name} {self.event_date} {self.get_reason_category_display()} ({self.status})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.reason_category == 'other' and not self.custom_reason.strip():
            raise ValidationError({'custom_reason': 'A custom reason is required when "Other" is selected.'})

    @property
    def event_start(self):
        """Combined event_date + event_start_time as an aware datetime."""
        if not self.event_date or not self.event_start_time:
            return None
        naive = datetime.combine(self.event_date, self.event_start_time)
        return timezone.make_aware(naive) if timezone.is_naive(naive) else naive

    @property
    def decision_window_opens_at(self):
        start = self.event_start
        return start - DECISION_WINDOW_BEFORE if start else None

    @property
    def decision_deadline(self):
        start = self.event_start
        return start + DECISION_WINDOW_AFTER if start else None

    def is_within_decision_window(self, now=None):
        now = now or timezone.now()
        opens, deadline = self.decision_window_opens_at, self.decision_deadline
        return bool(opens and deadline and opens <= now <= deadline)

    def is_overdue(self, now=None):
        now = now or timezone.now()
        deadline = self.decision_deadline
        return bool(deadline and now > deadline)

    def time_left_seconds(self, now=None):
        """Seconds remaining in the 24h post-event approval window (negative
        once overdue) — used by the manager UI's countdown."""
        now = now or timezone.now()
        deadline = self.decision_deadline
        if not deadline:
            return None
        return (deadline - now).total_seconds()
