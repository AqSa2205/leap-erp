from django.db import models
from django.conf import settings


class ProposalBoilerplate(models.Model):
    SECTION_CHOICES = [
        ('covering_letter', 'Covering Letter'),
        ('executive_summary', 'Executive Summary'),
        ('company_overview', 'Company Overview'),
        ('understanding_of_requirements', 'Understanding of Requirements'),
        ('proposed_technical_solution', 'Proposed Technical Solution'),
        ('delivery_implementation', 'Delivery & Implementation'),
        ('risk_management', 'Risk Management'),
        ('service_management', 'Service Management'),
        ('data_protection', 'Data Protection'),
        ('assumptions_constraints', 'Assumptions & Constraints'),
    ]
    name = models.CharField(max_length=255)
    section = models.CharField(max_length=40, choices=SECTION_CHOICES)
    content = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='proposal_boilerplates',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['section', 'name']

    def __str__(self):
        return f"{self.get_section_display()} - {self.name}"


class TechnicalProposal(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('review', 'In Review'),
        ('final', 'Final'),
        ('submitted', 'Submitted'),
    ]
    REGION_CHOICES = [
        ('LNUK', 'Global'),
        ('LNKSA', 'Arabia'),
    ]

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='proposals',
    )
    title = models.CharField(max_length=255)
    proposal_reference = models.CharField(max_length=50, unique=True)
    document_type = models.CharField(max_length=100, default='Technical Proposal')
    client_name = models.CharField(max_length=255)
    project_description = models.CharField(max_length=500, blank=True)
    region_entity = models.CharField(max_length=10, choices=REGION_CHOICES, default='LNUK')
    revision = models.CharField(max_length=10, default='A')
    revision_date = models.DateField()
    prepared_by_initials = models.CharField(max_length=10)
    checked_by_initials = models.CharField(max_length=10, blank=True)
    approved_by_initials = models.CharField(max_length=10, blank=True)
    # Section content fields
    covering_letter = models.TextField(blank=True)
    executive_summary = models.TextField(blank=True)
    company_overview = models.TextField(blank=True)
    understanding_of_requirements = models.TextField(blank=True)
    proposed_technical_solution = models.TextField(blank=True)
    delivery_implementation = models.TextField(blank=True)
    risk_management = models.TextField(blank=True)
    service_management = models.TextField(blank=True)
    data_protection = models.TextField(blank=True)
    assumptions_constraints = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='proposals',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    def get_region_display_name(self):
        return dict(self.REGION_CHOICES).get(self.region_entity, self.region_entity)

    # Company entity shown in the document header, by region.
    COMPANY_NAMES = {
        'LNUK': 'LEAP Networks Global Ltd.',
        'LNKSA': 'LEAP Networks Arabia',
    }

    def get_company_name(self):
        return self.COMPANY_NAMES.get(self.region_entity, 'LEAP Networks Global Ltd.')

    SECTION_FIELDS = [
        ('covering_letter', 'Covering Letter'),
        ('executive_summary', 'Executive Summary'),
        ('company_overview', 'Company Overview'),
        ('understanding_of_requirements', 'Understanding of Requirements'),
        ('proposed_technical_solution', 'Proposed Technical Solution'),
        ('delivery_implementation', 'Delivery & Implementation'),
        ('risk_management', 'Risk Management'),
        ('service_management', 'Service Management'),
        ('data_protection', 'Data Protection'),
        ('assumptions_constraints', 'Assumptions & Constraints'),
    ]


class EngineeringDocument(models.Model):
    proposal = models.ForeignKey(
        TechnicalProposal,
        on_delete=models.CASCADE,
        related_name='engineering_documents',
    )
    doc_type = models.CharField(max_length=100)
    doc_number = models.CharField(max_length=100)
    doc_title = models.CharField(max_length=255)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'doc_number']

    def __str__(self):
        return f"{self.doc_number} - {self.doc_title}"


class PrequalificationDocument(models.Model):
    """A Prequalification Document (PQD) — similar cover/header/footer to
    TechnicalProposal but with 7 sections, mixing rich-text content with
    attached files (PDF, Word, PowerPoint, images)."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('review', 'In Review'),
        ('final', 'Final'),
        ('submitted', 'Submitted'),
    ]
    REGION_CHOICES = [
        ('LNUK', 'Global'),
        ('LNKSA', 'Arabia'),
    ]

    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='prequalification_documents',
    )
    title = models.CharField(max_length=255)
    pqd_reference = models.CharField(max_length=100, unique=True)
    document_type = models.CharField(max_length=100, default='Prequalification')
    client_name = models.CharField(max_length=255)
    project_description = models.CharField(max_length=500, blank=True)
    region_entity = models.CharField(max_length=10, choices=REGION_CHOICES, default='LNKSA')
    revision = models.CharField(max_length=10, default='0')
    revision_date = models.DateField()
    prepared_by_initials = models.CharField(max_length=10)
    checked_by_initials = models.CharField(max_length=10, blank=True)
    approved_by_initials = models.CharField(max_length=10, blank=True)
    # Rich-text body for all 7 sections (HTML, stored from TinyMCE)
    company_profile = models.TextField(blank=True)
    list_of_material = models.TextField(blank=True)
    product_catalogues = models.TextField(blank=True)
    government_documents = models.TextField(blank=True)
    iso_certificates = models.TextField(blank=True)
    qualifications = models.TextField(blank=True)
    list_of_projects = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='prequalification_documents',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    def get_region_display_name(self):
        return dict(self.REGION_CHOICES).get(self.region_entity, self.region_entity)

    # All 7 sections support BOTH rich text AND file uploads.
    SECTIONS = [
        ('company_profile', 'Company Profile'),
        ('list_of_material', 'List of Material'),
        ('product_catalogues', 'Product Catalogues'),
        ('government_documents', 'Valid Government Documents'),
        ('iso_certificates', 'ISO Certificates'),
        ('qualifications', 'Qualifications (CVs)'),
        ('list_of_projects', 'List of Complete Projects'),
    ]

    TEXT_SECTION_FIELDS = SECTIONS  # every section has a text field

    FILE_SECTION_KEYS = [key for key, _ in SECTIONS]  # every section accepts uploads


class PQDAttachment(models.Model):
    """A file uploaded into one of the PQD's sections."""

    SECTION_CHOICES = [
        ('company_profile', 'Company Profile'),
        ('list_of_material', 'List of Material'),
        ('product_catalogues', 'Product Catalogues'),
        ('government_documents', 'Valid Government Documents'),
        ('iso_certificates', 'ISO Certificates'),
        ('qualifications', 'Qualifications (CVs)'),
        ('list_of_projects', 'List of Complete Projects'),
    ]

    pqd = models.ForeignKey(
        PrequalificationDocument, on_delete=models.CASCADE,
        related_name='attachments',
    )
    section = models.CharField(max_length=30, choices=SECTION_CHOICES)
    file = models.FileField(upload_to='pqd/attachments/')
    original_filename = models.CharField(max_length=255, blank=True)
    order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['section', 'order', 'pk']

    def __str__(self):
        return f'{self.get_section_display()} — {self.original_filename or self.file.name}'

    @property
    def extension(self):
        import os
        _, ext = os.path.splitext((self.original_filename or self.file.name).lower())
        return ext.lstrip('.')

    @property
    def is_pdf(self):
        return self.extension == 'pdf'

    @property
    def is_image(self):
        return self.extension in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp')

    @property
    def is_word(self):
        return self.extension in ('doc', 'docx')

    @property
    def is_powerpoint(self):
        return self.extension in ('ppt', 'pptx')
