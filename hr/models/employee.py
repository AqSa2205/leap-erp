from django.db import models
from django.conf import settings


class Employee(models.Model):
    MARITAL_CHOICES = [
        ('single', 'Single'),
        ('married', 'Married'),
    ]
    BLOOD_GROUP_CHOICES = [
        ('A+', 'A+'), ('A-', 'A-'),
        ('B+', 'B+'), ('B-', 'B-'),
        ('O+', 'O+'), ('O-', 'O-'),
        ('AB+', 'AB+'), ('AB-', 'AB-'),
    ]
    CONTRACT_CHOICES = [
        ('permanent', 'Permanent'),
        ('yearly', 'Yearly'),
        ('ajeer', 'AJEER'),
    ]
    WORK_LOCATION_CHOICES = [
        ('office', 'Office'),
        ('site', 'Site'),
    ]

    iqama_number = models.CharField(max_length=50, unique=True, verbose_name='Iqama/Passport No.')
    iqama_issued_on = models.DateField(null=True, blank=True, verbose_name='Iqama Issue Date')
    iqama_expires_on = models.DateField(null=True, blank=True, verbose_name='Iqama Expiry Date')
    medical_insurance_issued_on = models.DateField(null=True, blank=True, verbose_name='Medical Insurance Issue Date')
    medical_insurance_expires_on = models.DateField(null=True, blank=True, verbose_name='Medical Insurance Expiry Date')
    full_name = models.CharField(max_length=255, verbose_name='Full Name')
    designation = models.CharField(max_length=255, blank=True, verbose_name='Designation')
    qualification = models.CharField(max_length=255, blank=True, verbose_name='Qualification')
    date_of_birth = models.DateField(null=True, blank=True, verbose_name='Date of Birth')
    joining_date = models.DateField(null=True, blank=True, verbose_name='Joining Date')
    nationality = models.CharField(max_length=100, blank=True, verbose_name='Nationality')
    marital_status = models.CharField(max_length=20, choices=MARITAL_CHOICES, blank=True, verbose_name='Marital Status')
    blood_group = models.CharField(max_length=5, choices=BLOOD_GROUP_CHOICES, blank=True, verbose_name='Blood Group')
    personal_email = models.EmailField(blank=True, verbose_name='Personal Email')
    documents_link = models.URLField(max_length=500, blank=True, verbose_name='Documents Link')
    deployment = models.CharField(max_length=100, blank=True, verbose_name='Deployment')
    work_location = models.CharField(
        max_length=10, choices=WORK_LOCATION_CHOICES, blank=True,
        verbose_name='Office / Site')
    contract_type = models.CharField(max_length=20, choices=CONTRACT_CHOICES, blank=True, verbose_name='Contract Type')
    work_email = models.EmailField(blank=True, verbose_name='Work Email')
    mobile_number = models.CharField(max_length=20, blank=True, verbose_name='Mobile Number')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    inactive_from = models.DateField(
        null=True, blank=True, verbose_name='Inactive From',
        help_text='Date the employee became inactive (today or earlier).')
    # Login account this employee record belongs to — powers the self-service
    # "My Profile" portal. Optional; auto-matched by email / employee_code and
    # settable manually by admins.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='employee_profile',
        verbose_name='Login Account',
        help_text='The user account that logs in as this employee.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_employees',
    )
    # Org-chart reporting line — per-employee, not per-role, so two people who
    # both hold e.g. the "AI Head" role can each have their own manager
    # assignment. Self-referential; SET_NULL so removing/deactivating a
    # manager never cascades into deleting their reports.
    #
    # Split into two concepts to decouple system access roles from the real
    # reporting hierarchy: main_manager is the single formal reporting line
    # (drives the recursive hierarchy walks — get_downstream_employee_ids,
    # is_manager_of); secondary_managers is a flat, non-hierarchical set of
    # additional people who also get visibility/override rights over this
    # employee's requests without being part of that formal chain.
    main_manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='main_reports',
        verbose_name='Main Manager',
        help_text='This employee\'s primary/formal manager, for approval routing.',
    )
    secondary_managers = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        related_name='secondary_reports',
        verbose_name='Secondary Managers',
        help_text='Additional people who also have visibility/override rights over this '
                  'employee\'s requests, without being part of the formal reporting line.',
    )

    # Threshold (days) below which an upcoming expiry is considered "expiring soon".
    EXPIRY_WARN_DAYS = 30

    class Meta:
        ordering = ['full_name']
        indexes = [
            models.Index(fields=['full_name']),
            models.Index(fields=['nationality']),
            models.Index(fields=['deployment']),
            models.Index(fields=['contract_type']),
            models.Index(fields=['iqama_expires_on']),
            models.Index(fields=['medical_insurance_expires_on']),
        ]

    def __str__(self):
        return self.full_name

    def clean(self):
        """Guards against main_manager assignments that would break the
        formal reporting line: direct self-assignment, and indirect cycles
        (assigning a main_manager who is themselves — however many hops
        upstream — already a downstream report of self). Walks upward from
        the *proposed* main_manager via repeated .main_manager hops (the
        mirror image of get_downstream_employee_ids's downward walk, and
        using the same depth-guard style as is_manager_of) — if self's own
        pk is ever encountered in that upward walk, saving this assignment
        would close a loop back to self, so it's rejected before saving.

        The `self.pk is not None` guards are deliberate: a brand-new
        (unsaved) employee can never already be part of an existing cycle,
        since nothing can reference an unsaved row yet, so the check is only
        meaningful once self has an identity to be found in someone else's
        chain.

        Deliberately out of scope here: secondary_managers self-assignment.
        M2M relations require both sides already saved, so an unsaved
        instance's M2M manager can't be queried inside clean() — Django's
        full_clean() doesn't run m2m validation for exactly this reason.
        That guard belongs in the (later, separate) Org-Chart management
        view instead.
        """
        from django.core.exceptions import ValidationError
        if self.main_manager_id is not None and self.pk is not None and self.main_manager_id == self.pk:
            raise ValidationError({'main_manager': 'An employee cannot be their own manager.'})
        if self.main_manager_id and self.pk is not None:
            current = self.main_manager
            for _ in range(50):
                if current is None:
                    break
                if current.pk == self.pk:
                    raise ValidationError({'main_manager':
                        'This assignment would create a circular reporting chain '
                        f'(a manager upstream of {self.main_manager.full_name} already reports, '
                        'directly or indirectly, to this employee).'})
                current = current.main_manager

    def get_downstream_employee_ids(self, max_depth=10):
        """This employee's full downstream reporting chain (direct reports,
        their reports, etc.), as a flat list of employee ids. Depth-guarded
        rather than a recursive DB query — no recursive-hierarchy precedent
        exists elsewhere in this codebase, and a handful of manual hops is
        simpler and safer than a CTE for an org chart this size."""
        ids = []
        frontier = [self.pk]
        seen = {self.pk}
        for _ in range(max_depth):
            if not frontier:
                break
            reports = list(Employee.objects.filter(main_manager_id__in=frontier).values_list('pk', flat=True))
            reports = [pk for pk in reports if pk not in seen]
            if not reports:
                break
            ids.extend(reports)
            seen.update(reports)
            frontier = reports
        return ids

    def is_manager_of(self, other_employee, max_depth=10):
        """True if self is anywhere in other_employee's manager chain (direct
        manager, their manager's manager, etc.), up to max_depth hops. Mirror
        image of get_downstream_employee_ids: that walks downward via
        main_manager_id__in, this walks upward via .main_manager. False if
        other_employee has no manager at all, or the chain is exhausted
        without finding self."""
        current = other_employee.main_manager
        for _ in range(max_depth):
            if current is None:
                return False
            if current.pk == self.pk:
                return True
            current = current.main_manager
        return False

    @staticmethod
    def _days_until(target):
        if not target:
            return None
        from django.utils import timezone
        return (target - timezone.now().date()).days

    @staticmethod
    def _expiry_status(days):
        if days is None:
            return 'unknown'
        if days < 0:
            return 'expired'
        if days <= Employee.EXPIRY_WARN_DAYS:
            return 'expiring_soon'
        return 'valid'

    @property
    def iqama_days_until_expiry(self):
        return self._days_until(self.iqama_expires_on)

    @property
    def iqama_status(self):
        return self._expiry_status(self.iqama_days_until_expiry)

    @property
    def medical_insurance_days_until_expiry(self):
        return self._days_until(self.medical_insurance_expires_on)

    @property
    def medical_insurance_status(self):
        return self._expiry_status(self.medical_insurance_days_until_expiry)


class EmployeeDocument(models.Model):
    """Documents uploaded for an employee (joining letter, leave forms, etc.)."""

    DOC_TYPE_CHOICES = [
        ('joining_letter', 'Joining Letter'),
        ('asset_handover', 'Asset Handover'),
        ('leave_form', 'Leave Form'),
        ('contract', 'Contract'),
        ('iqama', 'Iqama / ID Copy'),
        ('passport', 'Passport Copy'),
        ('visa', 'Visa'),
        ('certificate', 'Certificate'),
        ('warning_letter', 'Warning Letter'),
        ('termination', 'Termination Letter'),
        ('salary_slip', 'Salary Slip'),
        ('other', 'Other'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='documents'
    )
    document_type = models.CharField(max_length=30, choices=DOC_TYPE_CHOICES, default='other', verbose_name="Document Type")
    title = models.CharField(max_length=255, verbose_name="Document Title")
    file = models.FileField(upload_to='employee_documents/%Y/%m/', verbose_name="File")
    notes = models.TextField(blank=True, verbose_name="Notes")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='uploaded_employee_docs',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = "Employee Document"
        verbose_name_plural = "Employee Documents"

    def __str__(self):
        return f"{self.title} ({self.get_document_type_display()})"

    @property
    def filename(self):
        import os
        return os.path.basename(self.file.name) if self.file else ''

    @property
    def file_extension(self):
        import os
        _, ext = os.path.splitext(self.file.name) if self.file else ('', '')
        return ext.lower()

    @property
    def is_image(self):
        return self.file_extension in ('.jpg', '.jpeg', '.png', '.gif', '.webp')

    @property
    def is_pdf(self):
        return self.file_extension == '.pdf'
