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
        ('LNUK', 'United Kingdom'),
        ('LNIRL', 'Ireland'),
        ('LNKSA', 'Saudi Arabia'),
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
