from django.db import models
from django.conf import settings


class Vendor(models.Model):
    """Partner, Distributor & Vendor companies"""
    VENDOR_TYPE_CHOICES = [
        ('vendor', 'Vendor'),
        ('distributor', 'Distributor'),
        ('partner', 'Partner'),
        ('oem', 'OEM'),
    ]

    name = models.CharField(max_length=255)
    vendor_type = models.CharField(max_length=20, choices=VENDOR_TYPE_CHOICES, default='vendor')
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    products_services = models.TextField(blank=True, help_text="Products or services provided")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_vendor_type_display()})"


class EPC(models.Model):
    """EPC Contractors"""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    region = models.CharField(max_length=100, blank=True, help_text="Operating regions")
    specialization = models.TextField(blank=True, help_text="Areas of specialization")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'EPC Contractor'
        verbose_name_plural = 'EPC Contractors'

    def __str__(self):
        return self.name


class Exhibition(models.Model):
    """Exhibitions and Trade Shows attended"""
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    year = models.CharField(max_length=4, blank=True)
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    leads_generated = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    is_attended = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-year', 'name']

    def __str__(self):
        return f"{self.name} ({self.year})"


class ProcurementPortal(models.Model):
    """Procurement portals and registrations"""
    REGISTRATION_TYPE_CHOICES = [
        ('free', 'Free Registration'),
        ('freemium', 'Freemium Registration'),
        ('paid', 'Paid Registration'),
    ]

    name = models.CharField(max_length=255)
    registration_type = models.CharField(max_length=20, choices=REGISTRATION_TYPE_CHOICES, default='free')
    website = models.URLField(max_length=500, blank=True)
    username = models.CharField(max_length=255, blank=True)
    registration_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    annual_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_registration_type_display()})"


class Certification(models.Model):
    """Company certifications and accreditations"""
    STATUS_CHOICES = [
        ('obtained', 'Obtained'),
        ('in_progress', 'In Progress'),
        ('pending', 'Pending'),
        ('expired', 'Expired'),
    ]

    name = models.CharField(max_length=255)
    issuing_body = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    certificate_number = models.CharField(max_length=100, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class SalesContact(models.Model):
    """Sales call contacts tracking"""
    CATEGORY_CHOICES = [
        ('oil_gas', 'Oil & Gas'),
        ('energy', 'Energy'),
        ('construction', 'Construction'),
        ('manufacturing', 'Manufacturing'),
        ('government', 'Government'),
        ('other', 'Other'),
    ]

    company_name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='other')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_contacted = models.BooleanField(default=False)
    last_contact_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company_name']

    def __str__(self):
        return f"{self.company_name} - {self.contact_name}"
