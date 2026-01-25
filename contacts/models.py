from django.db import models
from django.conf import settings


class ContactDatabase(models.Model):
    """
    Centralized Contact Database for all technology categories.
    Stores leads and contacts from CCTV, Radios, ACS, IoT, IIoT, Servers,
    Network & Security, Firewall, Cyber Security, Windows, OT categories.
    """

    CATEGORY_CHOICES = [
        ('cctv', 'CCTV'),
        ('radios', 'Radios'),
        ('acs', 'Access Control Systems (ACS)'),
        ('iot', 'IoT'),
        ('iiot', 'IIoT'),
        ('servers', 'Servers'),
        ('network_security', 'Network & Security'),
        ('firewall', 'Firewall'),
        ('cyber_security', 'Cyber Security'),
        ('windows', 'Windows'),
        ('ot', 'Operational Technology (OT)'),
    ]

    NOTICE_TYPE_CHOICES = [
        ('contract', 'Contract'),
        ('tender', 'Tender'),
        ('award', 'Award'),
        ('opportunity', 'Opportunity'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('awarded', 'Awarded'),
        ('open', 'Open'),
        ('closed', 'Closed'),
        ('pending', 'Pending'),
        ('unknown', 'Unknown'),
    ]

    # Category
    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES,
        verbose_name="System Category"
    )

    # Notice/Tender Information
    notice_identifier = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Notice Identifier"
    )
    notice_type = models.CharField(
        max_length=30,
        choices=NOTICE_TYPE_CHOICES,
        default='contract',
        verbose_name="Notice Type"
    )
    serial_number = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="SR#"
    )
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default='unknown',
        verbose_name="Status"
    )
    published_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Published Date"
    )

    # Organisation Information
    organisation_name = models.CharField(
        max_length=255,
        verbose_name="Organisation Name"
    )
    title = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Title/Project"
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description"
    )

    # Location Information
    nationwide = models.BooleanField(
        default=False,
        verbose_name="Nationwide"
    )
    postcode = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Postcode"
    )
    region = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Region"
    )

    # Contact Information
    contact_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Contact Name"
    )
    contact_email = models.EmailField(
        blank=True,
        verbose_name="Contact Email"
    )
    contact_address = models.TextField(
        blank=True,
        verbose_name="Contact Address"
    )
    contact_telephone = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Contact Telephone"
    )
    contact_website = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="Contact Website"
    )

    # Additional Information
    cpv_codes = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="CPV Codes"
    )
    last_contact = models.DateField(
        null=True,
        blank=True,
        verbose_name="Last Contact"
    )
    comments = models.TextField(
        blank=True,
        verbose_name="Comments"
    )

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_contacts'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['category']),
            models.Index(fields=['organisation_name']),
            models.Index(fields=['status']),
            models.Index(fields=['region']),
        ]

    def __str__(self):
        return f"{self.organisation_name} - {self.get_category_display()}"
