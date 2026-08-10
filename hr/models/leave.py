from django.db import models
from django.conf import settings


class LeaveType(models.Model):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=40, unique=True)
    default_annual_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    site_default_annual_days = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
        help_text='Override default for Site employees. Blank = same as the default above (Office).')
    is_paid = models.BooleanField(default=True)
    color = models.CharField(max_length=20, default='secondary', help_text='Bootstrap color name for badges')
    requires_medical_certificate = models.BooleanField(
        default=False,
        help_text='If set, a medical certificate must be attached when recording this leave (e.g. Sick).')
    is_accumulative = models.BooleanField(
        default=True,
        help_text='The standard accrued allowance (Annual) counts toward the top-level '
                   'entitlement totals. Conditional/incidental leaves (Sick, Marriage, Death of '
                   'Family Member, Umrah, New Born, etc.) should have this unchecked so they '
                   "don't inflate the summary total — they still show in the per-type breakdown.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def default_days_for(self, work_location):
        if work_location == 'site' and self.site_default_annual_days is not None:
            return self.site_default_annual_days
        return self.default_annual_days

    def save(self, *args, **kwargs):
        old_days = None
        old_site_days = None
        if self.pk:
            prev = type(self).objects.filter(pk=self.pk).only(
                'default_annual_days', 'site_default_annual_days').first()
            if prev is not None:
                old_days = prev.default_annual_days
                old_site_days = prev.site_default_annual_days
        super().save(*args, **kwargs)
        # When either day count changes, propagate it to ALL existing
        # entitlements of this type (every year, every employee) — the leave
        # type is the source of truth. Office and Site employees get their
        # own default; this never touches LeaveExceptionGrant rows, which
        # live outside entitled_days entirely.
        if old_days is not None and (old_days != self.default_annual_days or old_site_days != self.site_default_annual_days):
            # work_location is blank for employees never assigned one — treat
            # that the same as 'office' (today's only behavior) rather than
            # silently skipping them.
            self.entitlements.exclude(employee__work_location='site').update(
                entitled_days=self.default_annual_days)
            self.entitlements.filter(employee__work_location='site').update(
                entitled_days=self.default_days_for('site'))


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

    @property
    def exception_days(self):
        from decimal import Decimal
        agg = self.employee.leave_exception_grants.filter(
            leave_type=self.leave_type, year=self.year,
        ).aggregate(models.Sum('days'))
        return agg['days__sum'] or Decimal('0')

    @property
    def effective_entitled_days(self):
        return self.entitled_days + self.exception_days

    @property
    def effective_remaining_days(self):
        return self.effective_entitled_days - self.taken_days


class LeaveExceptionGrant(models.Model):
    """One HR-granted addition to an employee's standard entitlement for a
    year — an audit log (one row per grant action), not a single
    overwritable counter, so multiple grants across a year each keep their
    own reason/date. LeaveEntitlement.exception_days sums these rather than
    storing a redundant total."""
    employee = models.ForeignKey('hr.Employee', on_delete=models.CASCADE, related_name='leave_exception_grants')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='exception_grants')
    year = models.PositiveIntegerField()
    days = models.DecimalField(max_digits=5, decimal_places=1)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    granted_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()

    class Meta:
        ordering = ['-granted_at']

    def __str__(self):
        return f"{self.employee.full_name} +{self.days} {self.leave_type.name} ({self.year})"


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


class LeaveDashboardAccess(models.Model):
    """A user granted visibility into the master Leave Request dashboard
    (the FIFO queue + detail pages) AND approval authority over conditional
    leave requests — a single merged roster (this table used to be split
    into a separate LeaveApprover model, but "who can see the dashboard" and
    "who approves requests" turned out to always be the same people in
    practice, so they were combined). Super Admins can always view
    regardless of this table, but being a Super Admin does NOT by itself
    grant approval authority — only an active row here does. Override
    authority (a separate, narrower escape hatch) is governed independently
    by OverrideAccessSettings below. A DB fact, never a hardcoded username."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_dashboard_access')
    is_active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} (leave dashboard access)"


class OverrideAccessSettings(models.Model):
    """Singleton (always pk=1) controlling who can use the Super Admin
    'override' escape hatch on leave requests (force-approve/disapprove when
    the real approver is unavailable). Exactly one mode is authoritative at
    a time — switching modes does not clear the other modes' saved
    selections, so toggling back and forth doesn't lose data, but only the
    active mode's grants actually affect who can override (see
    hr.views.has_override_access)."""
    MODE_ALL_SUPER_ADMINS = 'all_super_admins'
    MODE_SPECIFIC_ROLES = 'specific_roles'
    MODE_SPECIFIC_EMPLOYEES = 'specific_employees'
    MODE_CHOICES = [
        (MODE_ALL_SUPER_ADMINS, 'All Super Admins'),
        (MODE_SPECIFIC_ROLES, 'Specific Roles'),
        (MODE_SPECIFIC_EMPLOYEES, 'Specific Employees'),
    ]
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_ALL_SUPER_ADMINS)
    allow_site_balance_hold = models.BooleanField(
        default=True,
        help_text='Site employees who submit leave exceeding their available balance get a held, '
                   'reviewable request instead of a hard block.')
    allow_office_balance_hold = models.BooleanField(
        default=False,
        help_text='Same as above, for Office employees. Off by default — Office keeps the plain hard block.')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return f"Override Access Settings ({self.get_mode_display()})"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def balance_hold_enabled_for(self, work_location):
        return self.allow_site_balance_hold if work_location == 'site' else self.allow_office_balance_hold


class OverrideAccessRole(models.Model):
    """A role granted override access — only actually confers it while
    OverrideAccessSettings.mode == 'specific_roles'."""
    role = models.OneToOneField('accounts.Role', on_delete=models.CASCADE, related_name='override_access_grant')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['role__name']

    def __str__(self):
        return f"{self.role} (override access)"


class OverrideAccessEmployee(models.Model):
    """A specific user granted override access — only actually confers it
    while OverrideAccessSettings.mode == 'specific_employees'."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='override_access_grant')
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} (override access)"


class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('disapproved', 'Disapproved'),
        ('cancelled', 'Cancelled'),
        ('revoked', 'Revoked'),
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
    exceeds_balance = models.BooleanField(
        default=False,
        help_text='True if this request was held (not hard-blocked) because it exceeds the '
                   "employee's effective balance — only possible for work locations where balance "
                   'holding is enabled. Requires a Super Admin override to approve.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='leave_requests_created')
    logged_by_manager = models.BooleanField(
        default=False,
        help_text="True if created_by was the employee's direct main_manager at the time this request "
                  'was submitted (not the employee themselves, not HR). Captured at submission time — '
                  "deliberately not live-recomputed, so it stays accurate even if the employee's manager "
                  'changes later.')
    leave_record = models.OneToOneField(LeaveRecord, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='source_request')
    salary_deduction_applicable = models.BooleanField(default=False)
    salary_deduction_note = models.TextField(blank=True)
    is_overridden = models.BooleanField(default=False)
    overridden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='leave_requests_overridden')
    override_reason = models.TextField(blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='leave_requests_revoked')
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
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
        if self.leave_type_id and self.leave_type.requires_medical_certificate and not self.document:
            raise ValidationError({'document':
                                   'A medical certificate/document is required for this leave type.'})

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

    @property
    def document_filename(self):
        import os
        return os.path.basename(self.document.name) if self.document else ''

    @property
    def decided_by_display(self):
        """Human-readable 'by X' summary for a finalized request — None
        while still pending, and None if there's no actor info to add.
        Deliberately does NOT repeat the Approved/Disapproved verb: every
        caller already renders a status badge right next to this, so
        including the verb here reads as 'Disapproved / Disapproved by X'.
        Covers both the normal dual-approval path (joins every approver
        whose decision matches the final status — for 'approved' this is
        always every approver, since _reconcile only finalizes as approved
        once all have approved) and the Super Admin override path
        (is_overridden/overridden_by)."""
        if self.status not in ('approved', 'disapproved'):
            return None
        if self.is_overridden:
            if self.overridden_by_id:
                name = self.overridden_by.get_full_name() or self.overridden_by.username
                return f'by {name} (override)'
            return '(override — approver account no longer exists)'
        actors = [
            a.approver.get_full_name() or a.approver.username
            for a in self.approvals.all() if a.decision == self.status
        ]
        if actors:
            return f'by {", ".join(actors)}'
        return None


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


class LeaveRevokeRequest(models.Model):
    """An employee's request to void their own already-approved leave (e.g.
    a project emergency means they can no longer take it) — decided by the
    same roster that decides normal leave requests (LeaveDashboardAccess +
    Super Admins), NOT by the employee's manager specifically. Distinct from
    a Super Admin's direct revoke (see LeaveRequest.revoked_by/revoked_at/
    revoke_reason) — this model exists only to track the request-and-review
    step; applying the revoke itself still goes through the same mechanism
    (see hr.leave_approval_services.revoke_leave_request)."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='revoke_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(
        blank=True, help_text='Required when rejecting; optional when approving (the reason already explains why).')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Revoke request for leave request #{self.leave_request_id} ({self.status})"


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
