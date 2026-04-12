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

    iqama_number = models.CharField(max_length=50, unique=True, verbose_name='Iqama/Passport No.')
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
    contract_type = models.CharField(max_length=20, choices=CONTRACT_CHOICES, blank=True, verbose_name='Contract Type')
    work_email = models.EmailField(blank=True, verbose_name='Work Email')
    mobile_number = models.CharField(max_length=20, blank=True, verbose_name='Mobile Number')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_employees',
    )

    class Meta:
        ordering = ['full_name']
        indexes = [
            models.Index(fields=['full_name']),
            models.Index(fields=['nationality']),
            models.Index(fields=['deployment']),
            models.Index(fields=['contract_type']),
        ]

    def __str__(self):
        return self.full_name


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


class Asset(models.Model):
    CONDITION_CHOICES = [
        ('new', 'New'),
        ('used', 'Used'),
    ]

    asset_name = models.CharField(max_length=255, verbose_name='Asset Name')
    asset_type = models.CharField(max_length=50, blank=True, verbose_name='Asset Type')
    serial_number = models.CharField(max_length=255, blank=True, verbose_name='Serial No.')
    specifications = models.CharField(max_length=255, blank=True, verbose_name='Specifications')
    invoice_number = models.CharField(max_length=100, blank=True, verbose_name='Invoice Number')
    employee_name = models.CharField(max_length=255, blank=True, verbose_name='Employee Name')
    department = models.CharField(max_length=100, blank=True, verbose_name='Department')
    designation = models.CharField(max_length=255, blank=True, verbose_name='Designation')
    handover_date = models.DateField(null=True, blank=True, verbose_name='Handover Date')
    handover_by = models.CharField(max_length=255, blank=True, verbose_name='Handover By')
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, blank=True, verbose_name='Condition')
    return_date = models.CharField(max_length=100, blank=True, verbose_name='Return Date')
    return_to = models.CharField(max_length=255, blank=True, verbose_name='Return To')
    quantity = models.PositiveIntegerField(default=1, verbose_name='QTY')
    purchase_date = models.DateField(null=True, blank=True, verbose_name='Purchase Date')
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Price (SAR)')
    planned_life = models.CharField(max_length=50, blank=True, verbose_name='Planned Asset Life')
    in_stock = models.BooleanField(default=False, verbose_name='In Stock?')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_assets',
    )

    class Meta:
        ordering = ['asset_name']
        indexes = [
            models.Index(fields=['asset_name']),
            models.Index(fields=['asset_type']),
            models.Index(fields=['employee_name']),
            models.Index(fields=['serial_number']),
        ]

    def __str__(self):
        return f"{self.asset_name} ({self.serial_number})" if self.serial_number else self.asset_name

    @property
    def current_age(self):
        if self.purchase_date:
            from django.utils import timezone
            delta = timezone.now().date() - self.purchase_date
            years = delta.days // 365
            months = (delta.days % 365) // 30
            if years > 0:
                return f"{years} Year{'s' if years != 1 else ''}, {months} Month{'s' if months != 1 else ''}"
            return f"{months} Month{'s' if months != 1 else ''}"
        return "-"


class Vehicle(models.Model):
    """Company vehicle fleet management."""

    VEHICLE_STATUS_CHOICES = [
        ('valid', 'Valid'),
        ('expired', 'Expired'),
        ('suspended', 'Suspended'),
        ('sold', 'Sold'),
    ]

    MVPI_STATUS_CHOICES = [
        ('valid', 'Valid'),
        ('expired', 'Expired'),
        ('not_exist', 'Not Exist'),
    ]

    INSURANCE_STATUS_CHOICES = [
        ('valid', 'Valid'),
        ('expired', 'Expired'),
        ('not_exist', 'Not Exist'),
    ]

    RESTRICTION_CHOICES = [
        ('unrestricted', 'Unrestricted'),
        ('restricted', 'Restricted'),
    ]

    # Plate & Identity
    plate_number = models.CharField(max_length=100, verbose_name="Plate Number")
    plate_type = models.CharField(max_length=100, blank=True, verbose_name="Plate Type")
    sequence_number = models.CharField(max_length=100, blank=True, verbose_name="Sequence Number")
    chassis_number = models.CharField(max_length=100, blank=True, verbose_name="Chassis Number")

    # Vehicle Details
    vehicle_maker = models.CharField(max_length=100, verbose_name="Vehicle Maker")
    vehicle_model = models.CharField(max_length=100, blank=True, verbose_name="Vehicle Model")
    model_year = models.CharField(max_length=10, blank=True, verbose_name="Model Year")
    major_color = models.CharField(max_length=50, blank=True, verbose_name="Color")
    body_type = models.CharField(max_length=100, blank=True, verbose_name="Body Type")

    # Status & Compliance
    vehicle_status = models.CharField(max_length=20, choices=VEHICLE_STATUS_CHOICES, default='valid', verbose_name="Vehicle Status")
    mvpi_status = models.CharField(max_length=20, choices=MVPI_STATUS_CHOICES, default='valid', verbose_name="MVPI Status")
    insurance_status = models.CharField(max_length=20, choices=INSURANCE_STATUS_CHOICES, default='valid', verbose_name="Insurance Status")
    restriction_status = models.CharField(max_length=20, choices=RESTRICTION_CHOICES, default='unrestricted', verbose_name="Restriction Status")

    # Dates
    ownership_date = models.CharField(max_length=50, blank=True, verbose_name="Ownership Date")
    license_expiry = models.CharField(max_length=50, blank=True, verbose_name="License Expiry Date")
    license_issue_date = models.CharField(max_length=50, blank=True, verbose_name="License Issue Date")
    inspection_expiry = models.CharField(max_length=50, blank=True, verbose_name="Inspection Expiry")

    # Driver
    driver_id = models.CharField(max_length=50, blank=True, verbose_name="Driver ID")
    driver_name = models.CharField(max_length=255, blank=True, verbose_name="Driver Name")

    # Organization
    branch_name = models.CharField(max_length=255, blank=True, verbose_name="Branch")

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_vehicles',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['plate_number']
        verbose_name = "Vehicle"
        verbose_name_plural = "Vehicles"

    def __str__(self):
        return f"{self.plate_number} - {self.vehicle_maker} {self.vehicle_model}"

    @property
    def has_compliance_issue(self):
        return (
            self.mvpi_status == 'expired' or
            self.insurance_status == 'expired' or
            self.vehicle_status != 'valid'
        )
