from django.db import models
from django.conf import settings


class Region(models.Model):
    """Geographic regions for sales operations"""
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, unique=True)  # UK, LNA, PA
    currency = models.CharField(max_length=3, default='GBP')  # GBP, SAR, USD
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class ProjectStatus(models.Model):
    """Status categories for projects"""
    CATEGORY_CHOICES = [
        ('active', 'Active'),
        ('hot_lead', 'Hot Lead'),
        ('won', 'Won'),
        ('lost', 'Lost'),
        ('ongoing', 'Ongoing'),
    ]

    name = models.CharField(max_length=50)  # IP, Open, Submitted, Hold, Won, Lost, Closed
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    color = models.CharField(max_length=7, default='#6c757d')  # Bootstrap color
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Project statuses'

    def __str__(self):
        return self.name


class Project(models.Model):
    """Main project/bid tracking model"""
    QUARTER_CHOICES = [
        ('Q1', 'Q1'),
        ('Q2', 'Q2'),
        ('Q3', 'Q3'),
        ('Q4', 'Q4'),
    ]

    # Generate year choices dynamically
    import datetime
    current_year = datetime.datetime.now().year
    YEAR_CHOICES = [(str(y), str(y)) for y in range(2020, current_year + 5)]

    # Basic Information
    serial_number = models.IntegerField(null=True, blank=True)
    project_name = models.CharField(max_length=500)
    proposal_reference = models.CharField(
        max_length=50,
        unique=True,
        help_text="Leap Proposal Reference (e.g., LNUK-P02125038)"
    )
    client_rfq_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="Client RFQ Reference Number"
    )
    po_number = models.CharField(max_length=100, blank=True, help_text="Purchase Order Number")

    # Dates
    submission_deadline = models.DateField(null=True, blank=True)
    estimated_po_date = models.DateField(null=True, blank=True)

    # Relationships
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_projects',
        help_text="Project owner/responsible person"
    )
    epc = models.CharField(
        max_length=200,
        blank=True,
        help_text="EPC Contractor"
    )
    status = models.ForeignKey(
        ProjectStatus,
        on_delete=models.PROTECT,
        related_name='projects'
    )
    region = models.ForeignKey(
        Region,
        on_delete=models.PROTECT,
        related_name='projects'
    )

    # Financial
    estimated_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Estimated value in region currency"
    )
    estimated_value_usd = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Estimated value in USD"
    )
    estimated_value_per_annum = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Estimated value (SAR) per annum"
    )
    estimated_gp = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Estimated Gross Profit"
    )
    po_award_quarter = models.CharField(
        max_length=5,
        choices=QUARTER_CHOICES,
        blank=True
    )
    success_quotient = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Success probability (0-1)"
    )
    minimum_achievement = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True
    )

    # Year and Actual Sales tracking
    year = models.CharField(
        max_length=4,
        blank=True,
        help_text="Financial year for this project"
    )
    actual_sales = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Actual sales/revenue achieved"
    )

    # Additional Info
    contact_with = models.CharField(max_length=255, blank=True)
    remarks = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    portal_url = models.URLField(max_length=500, blank=True)

    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_projects'
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.proposal_reference} - {self.project_name}"

    @property
    def weighted_value(self):
        """Calculate weighted value based on success quotient"""
        return self.estimated_value * self.success_quotient

    @property
    def status_category(self):
        """Get the status category"""
        return self.status.category if self.status else None


class ProjectHistory(models.Model):
    """Track status changes for projects"""
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='history'
    )
    old_status = models.ForeignKey(
        ProjectStatus,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+'
    )
    new_status = models.ForeignKey(
        ProjectStatus,
        on_delete=models.SET_NULL,
        null=True,
        related_name='+'
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']
        verbose_name_plural = 'Project histories'

    def __str__(self):
        return f"{self.project} - {self.old_status} -> {self.new_status}"


def document_upload_path(instance, filename):
    """Generate upload path for documents"""
    return f'documents/{instance.document_type}/{filename}'


class Document(models.Model):
    """Document management for projects"""
    DOCUMENT_TYPE_CHOICES = [
        ('vendor_quotation', 'Vendor Quotation'),
        ('proposal', 'Proposal'),
        ('customer_document', 'Customer Document'),
        ('technical_document', 'Technical Document'),
        ('po_document', 'Purchase Order'),
        ('contract', 'Contract'),
        ('other', 'Other'),
    ]

    # Document Information
    name = models.CharField(max_length=255, help_text="Document name/title")
    document_type = models.CharField(
        max_length=30,
        choices=DOCUMENT_TYPE_CHOICES,
        default='other'
    )
    description = models.TextField(blank=True, help_text="Brief description of the document")

    # File
    file = models.FileField(
        upload_to=document_upload_path,
        help_text="Upload document (PDF, Excel, Word, etc.)"
    )
    file_size = models.PositiveIntegerField(default=0, help_text="File size in bytes")

    # Link to Project (optional - documents can exist independently)
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
        help_text="Associated project (optional)"
    )

    # Metadata
    reference_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="Reference number (e.g., quotation number, proposal ref)"
    )
    vendor_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Vendor name (for vendor quotations)"
    )
    document_date = models.DateField(
        null=True,
        blank=True,
        help_text="Document date"
    )
    expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Expiry date (for quotations)"
    )

    # Audit fields
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_documents'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.name} ({self.get_document_type_display()})"

    def save(self, *args, **kwargs):
        if self.file:
            self.file_size = self.file.size
        super().save(*args, **kwargs)

    @property
    def file_extension(self):
        """Get file extension"""
        if self.file:
            return self.file.name.split('.')[-1].lower()
        return ''

    @property
    def file_size_display(self):
        """Human readable file size"""
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
