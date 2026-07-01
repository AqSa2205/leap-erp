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
