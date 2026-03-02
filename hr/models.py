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
