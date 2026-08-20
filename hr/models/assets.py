from django.db import models
from django.conf import settings


class Asset(models.Model):
    CONDITION_CHOICES = [
        ('new', 'New'),
        ('used', 'Used'),
    ]

    asset_name = models.CharField(max_length=255, verbose_name='Asset Name')
    asset_type = models.CharField(max_length=50, blank=True, verbose_name='Asset Type')
    serial_number = models.CharField(max_length=255, blank=True, verbose_name='Serial No.')
    specifications = models.CharField(max_length=255, blank=True, verbose_name='Specifications')
    model = models.CharField(max_length=255, blank=True, verbose_name='Model')
    part_number = models.CharField(max_length=100, blank=True, verbose_name='Part Number')
    tag_number = models.CharField(max_length=100, blank=True, verbose_name='Tag Number')
    item_description = models.TextField(blank=True, verbose_name='Item Description')
    accessories = models.TextField(blank=True, verbose_name='Accessories')
    software_installed = models.TextField(blank=True, verbose_name='Software Installed')
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
    # Dead / retired laptops etc. — kept for records but can never be in stock.
    is_decommissioned = models.BooleanField(
        default=False, verbose_name='Out of Service',
        help_text='Dead / no longer usable. Kept for records but never in stock.')
    decommissioned_on = models.DateField(
        null=True, blank=True, verbose_name='Out-of-Service Date')
    decommission_reason = models.CharField(
        max_length=255, blank=True, verbose_name='Reason')
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

    @property
    def active_assignment(self):
        """The currently-open AssetAssignment, or None."""
        return self.assignments.filter(returned_at__isnull=True).select_related('employee').first()

    @property
    def active_handover(self):
        """The currently-active AssetHandover for this asset, or None. This
        is the source of truth for current custody - active_assignment
        (above) is the legacy AssetAssignment record, kept only for the
        one-time migration and the duplicate-serial-number check below."""
        return self.handovers.filter(status='active').select_related('employee').first()

    @property
    def pending_handover(self):
        # A handover for this asset that has not reached Active yet (still
        # being authorized, or awaiting the employee's signature).
        # Distinct from active_handover - checked separately so "Issue to
        # Employee" doesn't offer a second handover for an item that
        # already has one in flight, even before it's in someone's custody.
        return self.handovers.filter(
            status__in=('pending_authorization', 'pending_receipt')
        ).select_related('employee').first()

    @property
    def current_holder(self):
        h = self.active_handover
        return h.employee if h else None

    @property
    def duplicate_active_assignments(self):
        """Other Asset rows that share this serial number and have an open
        assignment right now. Non-empty means the same physical device
        appears to be checked out twice — usually one of:
          1. Data-entry mistake (two Asset rows for the same hardware).
          2. The previous handover was never closed out before reissue.
          3. Two different people each hold an Asset row sharing the SN.

        Empty serial numbers are never flagged.
        """
        if not self.serial_number:
            return Asset.objects.none()
        return Asset.objects.exclude(pk=self.pk).filter(
            serial_number__iexact=self.serial_number,
            assignments__returned_at__isnull=True,
        ).select_related().distinct()

    @property
    def has_duplicate_serial_conflict(self):
        return self.duplicate_active_assignments.exists()


class AssetAssignment(models.Model):
    """One issue/return cycle of an Asset to an Employee.

    Lifetime: created at issue (returned_at=None means active). On return the
    same row is closed by setting returned_at + condition_in. Reassignment
    creates a fresh row, so each asset's full history is preserved.
    """

    CONDITION_CHOICES = [
        ('new', 'New'),
        ('used', 'Used'),
        ('damaged', 'Damaged'),
    ]

    asset = models.ForeignKey('Asset', on_delete=models.CASCADE, related_name='assignments')
    employee = models.ForeignKey('Employee', on_delete=models.PROTECT, related_name='asset_assignments')

    # Issue
    assigned_at = models.DateField(verbose_name='Handover Date')
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assets_issued',
    )
    condition_out = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='new', verbose_name='Condition at Handover')
    handover_form = models.FileField(
        upload_to='hr/asset_handovers/', null=True, blank=True,
        verbose_name='Signed Handover Form',
        help_text='Optional: scanned handover acknowledgement or photo of asset condition.',
    )
    issue_notes = models.TextField(blank=True, verbose_name='Issue Notes')

    # Return (filled when closed)
    returned_at = models.DateField(null=True, blank=True, verbose_name='Return Date')
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assets_returned',
    )
    condition_in = models.CharField(max_length=20, choices=CONDITION_CHOICES, blank=True, verbose_name='Condition on Return')
    return_notes = models.TextField(blank=True, verbose_name='Return Notes')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-assigned_at', '-created_at']
        constraints = [
            # Only one open assignment per asset at a time.
            models.UniqueConstraint(
                fields=['asset'],
                condition=models.Q(returned_at__isnull=True),
                name='hr_assetassignment_one_active_per_asset',
            ),
        ]

    @property
    def is_active(self):
        return self.returned_at is None

    def __str__(self):
        suffix = 'active' if self.is_active else f'returned {self.returned_at}'
        return f'{self.asset.asset_name} → {self.employee.full_name} ({suffix})'


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

    @property
    def active_handover(self):
        """The currently-active AssetHandover for this vehicle, or None."""
        return self.handovers.filter(status='active').first()

    @property
    def pending_handover(self):
        # A handover for this vehicle that has not reached Active yet -
        # see the matching Asset.pending_handover note above.
        return self.handovers.filter(
            status__in=('pending_authorization', 'pending_receipt')
        ).select_related('employee').first()


class VehicleDocument(models.Model):
    """Documents attached to a vehicle (registration, insurance, handover, etc.)."""

    DOC_TYPE_CHOICES = [
        ('registration', 'Registration (Istimara)'),
        ('insurance', 'Insurance'),
        ('handover', 'Handover Form'),
        ('inspection', 'Inspection (MVPI)'),
        ('other', 'Other'),
    ]

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.CASCADE, related_name='documents'
    )
    document_type = models.CharField(
        max_length=30, choices=DOC_TYPE_CHOICES, default='other',
        verbose_name="Document Type")
    custom_type = models.CharField(
        max_length=100, blank=True,
        help_text="Label used when the type is 'Other'.")
    title = models.CharField(max_length=255, verbose_name="Document Title")
    file = models.FileField(upload_to='vehicle_documents/%Y/%m/', verbose_name="File")
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text="Optional — e.g. registration / insurance expiry.")
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='uploaded_vehicle_docs',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = "Vehicle Document"
        verbose_name_plural = "Vehicle Documents"

    def __str__(self):
        return f"{self.title} ({self.type_label})"

    @property
    def type_label(self):
        """Custom label when 'Other', else the choice display."""
        if self.document_type == 'other' and self.custom_type:
            return self.custom_type
        return self.get_document_type_display()

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
