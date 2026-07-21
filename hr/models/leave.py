from django.db import models
from django.conf import settings


class LeaveType(models.Model):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=40, unique=True)
    default_annual_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    is_paid = models.BooleanField(default=True)
    color = models.CharField(max_length=20, default='secondary', help_text='Bootstrap color name for badges')
    requires_medical_certificate = models.BooleanField(
        default=False,
        help_text='If set, a medical certificate must be attached when recording this leave (e.g. Sick).')
    is_accumulative = models.BooleanField(
        default=True,
        help_text='Standard accrued allowances (Annual, Sick) count toward the top-level '
                   'entitlement totals. Conditional/incidental leaves (Marriage, Death of '
                   'Family Member, Umrah, New Born, etc.) should have this unchecked so they '
                   "don't inflate the summary total — they still show in the per-type breakdown.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        old_days = None
        if self.pk:
            prev = type(self).objects.filter(pk=self.pk).only('default_annual_days').first()
            if prev is not None:
                old_days = prev.default_annual_days
        super().save(*args, **kwargs)
        # When the day count changes, propagate it to ALL existing entitlements
        # of this type (every year, every employee) — the leave type is the
        # source of truth. Applies to every type, including Annual (flat).
        if old_days is not None and old_days != self.default_annual_days:
            self.entitlements.update(entitled_days=self.default_annual_days)


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
    medical_certificate = models.FileField(
        upload_to='leave_certificates/%Y/%m/', null=True, blank=True,
        help_text='Required for leave types that mandate a medical certificate (e.g. Sick).')
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
        if self.leave_type_id and self.leave_type.requires_medical_certificate \
                and not self.medical_certificate:
            raise ValidationError({'medical_certificate':
                                   'A medical certificate is required for this leave type.'})

    def computed_days(self):
        """Leave is counted in calendar days, inclusive — weekends (and any
        holidays) within the range still count (1st–5th = 5 days)."""
        from decimal import Decimal
        if not self.start_date or not self.end_date or self.end_date < self.start_date:
            return Decimal('0')
        return Decimal((self.end_date - self.start_date).days + 1)

    def save(self, *args, **kwargs):
        if self.days is None:
            self.days = self.computed_days()
        super().save(*args, **kwargs)


class LeaveApprover(models.Model):
    """A user with authority to approve/reject conditional leave requests.
    Approval authority is a DB fact (this table), never a hardcoded username."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_approver_profile')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} (leave approver)"


class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('disapproved', 'Disapproved'),
        ('cancelled', 'Cancelled'),
    ]

    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='requests')
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                               help_text='Auto-computed from the date range if left blank.')
    employee_reason = models.TextField(blank=True)
    document = models.FileField(upload_to='leave_requests/%Y/%m/', null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='leave_requests_created')
    leave_record = models.OneToOneField(LeaveRecord, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='source_request')
    salary_deduction_applicable = models.BooleanField(default=False)
    salary_deduction_note = models.TextField(blank=True)
    is_overridden = models.BooleanField(default=False)
    overridden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='leave_requests_overridden')
    override_reason = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']  # FIFO by default
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return f"{self.employee.full_name} {self.leave_type.name} {self.start_date}..{self.end_date} ({self.status})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})
        if self.leave_type_id and self.leave_type.is_accumulative:
            raise ValidationError({'leave_type': 'The approval workflow is only for conditional leave types.'})

    def computed_days(self):
        from decimal import Decimal
        if not self.start_date or not self.end_date or self.end_date < self.start_date:
            return Decimal('0')
        return Decimal((self.end_date - self.start_date).days + 1)

    def save(self, *args, **kwargs):
        if self.days is None:
            self.days = self.computed_days()
        super().save(*args, **kwargs)

    def pending_approvers(self):
        """Users whose decision is still outstanding (used for the employee-facing
        'Pending — waiting on X' message)."""
        return [a.approver for a in self.approvals.filter(decision='pending')]


class LeaveRequestApproval(models.Model):
    DECISION_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('disapproved', 'Disapproved'),
        ('skipped', 'Skipped'),  # set when a superadmin override finalizes the request first
    ]

    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='approvals')
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    decision = models.CharField(max_length=12, choices=DECISION_CHOICES, default='pending')
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('leave_request', 'approver')
        ordering = ['id']

    def __str__(self):
        return f"{self.approver} -> {self.decision} on request #{self.leave_request_id}"


class LeaveRequestNote(models.Model):
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    note = models.TextField()
    is_internal = models.BooleanField(default=False, help_text='Internal notes are hidden from the employee.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Note on request #{self.leave_request_id} by {self.author}"
