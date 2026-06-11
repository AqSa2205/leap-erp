from django.db import models
from django.conf import settings


class LeaveType(models.Model):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=40, unique=True)
    default_annual_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    is_paid = models.BooleanField(default=True)
    color = models.CharField(max_length=20, default='secondary', help_text='Bootstrap color name for badges')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class LeaveEntitlement(models.Model):
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_entitlements')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='entitlements')
    year = models.PositiveIntegerField()
    entitled_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'leave_type', 'year')
        ordering = ['-year', 'leave_type__name']

    def __str__(self):
        return f"{self.employee.full_name} {self.leave_type.name} {self.year}: {self.entitled_days}"

    @property
    def taken_days(self):
        from decimal import Decimal
        agg = self.employee.leave_records.filter(
            leave_type=self.leave_type, start_date__year=self.year,
        ).aggregate(models.Sum('days'))
        return agg['days__sum'] or Decimal('0')

    @property
    def remaining_days(self):
        return self.entitled_days - self.taken_days


class LeaveRecord(models.Model):
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_records')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='records')
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                               help_text='Working days; auto-computed from the range if left blank.')
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        indexes = [models.Index(fields=['employee', 'start_date'])]

    def __str__(self):
        return f"{self.employee.full_name} {self.leave_type.name} {self.start_date}..{self.end_date}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})

    def computed_days(self):
        from decimal import Decimal
        from hr.models import AttendanceSettings, Holiday
        from hr.work_calendar import count_working_days
        weekends = AttendanceSettings.load().weekend_day_set()
        holidays = set(Holiday.objects.filter(is_active=True).values_list('date', flat=True))
        return Decimal(count_working_days(self.start_date, self.end_date, weekends, holidays))

    def save(self, *args, **kwargs):
        if self.days is None:
            self.days = self.computed_days()
        super().save(*args, **kwargs)
