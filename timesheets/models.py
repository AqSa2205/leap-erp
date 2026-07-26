from django.conf import settings
from django.db import models


class ActivityCode(models.Model):
    """Admin-managed list of payroll activity codes (e.g. 'COS_0009').
    HR/Super Admin maintain this list — no code changes needed to add one."""
    code = models.CharField(max_length=20, unique=True)
    label = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.label}" if self.label else self.code


class TimesheetEntry(models.Model):
    """One row = one day's work log for one employee."""
    STATUS_DRAFT = 'draft'
    STATUS_SUBMITTED = 'submitted'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SUBMITTED, 'Submitted'),
    ]

    employee = models.ForeignKey(
        'hr.Employee', on_delete=models.CASCADE, related_name='timesheet_entries')
    date = models.DateField()
    task_description = models.TextField()
    activity_code = models.ForeignKey(
        ActivityCode, on_delete=models.PROTECT, related_name='entries')
    hours = models.DecimalField(max_digits=4, decimal_places=2)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.employee} - {self.date} ({self.hours}h)"


class TimesheetMonth(models.Model):
    """Tracks the lock/submit state for one employee's one calendar month.
    This is what HR actually reviews and reopens — individual TimesheetEntry
    rows don't carry their own lock state, this single row per month does."""
    employee = models.ForeignKey(
        'hr.Employee', on_delete=models.CASCADE, related_name='timesheet_months')
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()  # 1-12

    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+')

    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-year', '-month']
        unique_together = ('employee', 'year', 'month')

    def __str__(self):
        return f"{self.employee} - {self.month}/{self.year}"

    @property
    def is_submitted(self):
        """Locked = submitted and not since reopened."""
        if not self.submitted_at:
            return False
        if self.reopened_at and self.reopened_at > self.submitted_at:
            return False
        return True

class HRSettings(models.Model):
        """Singleton: the one fixed HR email every employee sends their
        timesheet to. Set once by HR/admin — never typed by employees, so a
        typo or misdirection can't send payroll data to the wrong inbox."""
        hr_email = models.EmailField()

        def __str__(self):
            return self.hr_email

        @classmethod
        def load(cls):
            obj, _ = cls.objects.get_or_create(pk=1)
            return obj


class TimesheetRequest(models.Model):
        """One row = HR asked everyone for their timesheet for a given month."""
        year = models.PositiveSmallIntegerField()
        month = models.PositiveSmallIntegerField()
        requested_by = models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
        requested_at = models.DateTimeField(auto_now_add=True)

        class Meta:
            ordering = ['-year', '-month']

        def __str__(self):
            return f"Timesheet request {self.month}/{self.year}"


class TimesheetRequestAck(models.Model):
        """Tracks which employee confirmed they sent their timesheet for a given
        request. Self-reported — Option B (opening the employee's own Outlook)
        means the server has no way to verify the email was actually sent, only
        that the employee clicked 'I've sent this'."""
        request = models.ForeignKey(TimesheetRequest, on_delete=models.CASCADE, related_name='acks')
        employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='timesheet_acks')
        acknowledged_at = models.DateTimeField(auto_now_add=True)

        class Meta:
            unique_together = ('request', 'employee')

        def __str__(self):
            return f"{self.employee} ack'd {self.request}"