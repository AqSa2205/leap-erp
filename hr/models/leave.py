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
