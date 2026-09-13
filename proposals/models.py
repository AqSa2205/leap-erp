from django.db import models
from django.conf import settings


PROPOSAL_DEPARTMENT_CHOICES = [
    ('ai', 'AI'),
    ('telecom', 'Telecom'),
    ('procurement', 'Security'),
    ('other', 'Other'),
]

# Every department the export-lock toggle applies to — a Super Admin can
# enable/disable the 'requires a linked client email to export' rule for
# any of these independently, 'Other' included. 'Other' still opts a
# proposal out of the department-headings restriction (see
# ProposalEditContentView) — that's a separate concern from the export lock.
PROPOSAL_LOCKABLE_DEPARTMENTS = PROPOSAL_DEPARTMENT_CHOICES


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
    department = models.CharField(
        max_length=20,
        choices=PROPOSAL_DEPARTMENT_CHOICES,
        blank=True,
        default='',
        help_text='Which department this proposal is for — controls whether the export-lock feature applies to it.',
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

    @property
    def is_export_locked(self):
        """True when this proposal's department currently requires a linked
        client email before its DOCX can be exported, and none has been
        linked yet. A proposal with no department chosen is never locked —
        that's every proposal that existed before this feature shipped, and
        any new one where nobody bothers to pick a department. 'Other' is a
        real, lockable department like the rest — it only opts out of the
        department-headings restriction, not the export lock."""
        if not self.department:
            return False
        return (
            ProposalDepartmentFeature.requires_email(self.department)
            and not self.linked_emails.exists()
        )

    def get_region_display_name(self):
        return dict(self.REGION_CHOICES).get(self.region_entity, self.region_entity)

    # Company entity shown in the document header, by region.
    COMPANY_NAMES = {
        'LNUK': 'LEAP Networks Global Ltd.',
        'LNKSA': 'LEAP Networks Arabia',
    }
    COMPANY_ACRONYMS = {
        'LNUK': 'LNG',
        'LNKSA': 'LNA',
    }

    def get_company_name(self):
        return self.COMPANY_NAMES.get(self.region_entity, 'LEAP Networks Global Ltd.')

    def get_company_acronym(self):
        return self.COMPANY_ACRONYMS.get(self.region_entity, 'LNG')

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


class SectionHeading(models.Model):
    """Admin-editable library of proposal section headings. A proposal is built
    by selecting headings from here (or typing a custom one). Each can carry
    default content that pre-fills a new section."""
    name = models.CharField(max_length=255, unique=True)
    order = models.PositiveIntegerField(default=0)
    default_content = models.TextField(
        blank=True, help_text='Optional default text a new section pre-fills with (editable).')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class ProposalSection(models.Model):
    """One section of a TechnicalProposal — a heading + rich-text content, in
    order. The heading text is stored here (denormalised) so custom headings
    work and library renames don't rewrite existing proposals."""
    proposal = models.ForeignKey(
        TechnicalProposal, on_delete=models.CASCADE, related_name='sections')
    heading = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.proposal_id}: {self.heading}'


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


# ─── Prequalification v2 — PDF library + selective merge ──────────

def prequal_library_upload_path(instance, filename):
    return f'prequal/library/{filename}'


class PrequalLibraryItem(models.Model):
    """One standard prequalification document: a heading and its PDF. Admin-
    managed shared library (the ~25 standard company documents) reused across
    every submission. A submission ticks which of these to combine."""

    heading = models.CharField(max_length=255, unique=True)
    order = models.PositiveIntegerField(default=0)
    pdf = models.FileField(
        upload_to=prequal_library_upload_path, null=True, blank=True,
        help_text='The PDF for this heading (e.g. ISO 9001 certificate).')
    description = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'heading']

    def __str__(self):
        return self.heading

    @property
    def has_pdf(self):
        return bool(self.pdf)


class PrequalSubmission(models.Model):
    """A named prequalification built for a project — remembers which library
    headings were selected, so it can be reopened, re-edited and re-exported
    into a single combined PDF."""

    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='prequal_submissions')
    title = models.CharField(max_length=255)
    client_name = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    # ── Cover-page title-block fields (left metadata panel) ──
    # PROJECT DEP — three stacked initials (also used for prepared/reviewed/approved)
    dep_1 = models.CharField('Project Dep 1', max_length=10, blank=True)
    dep_2 = models.CharField('Project Dep 2', max_length=10, blank=True)
    dep_3 = models.CharField('Project Dep 3', max_length=10, blank=True)
    description_month = models.CharField(max_length=20, blank=True, help_text='e.g. JUN/2026')
    description_text = models.CharField(max_length=100, blank=True, help_text='e.g. 2870 TECHNICAL PROPOSAL')
    cover_date = models.CharField(max_length=30, blank=True, help_text='DATE band value')
    revision = models.CharField(max_length=10, blank=True, help_text='REV, e.g. A')
    report_no = models.CharField(max_length=50, blank=True, help_text='REPORT NO.')
    block_date = models.CharField(max_length=20, blank=True, help_text='Date in PREPARED/REVIEWED/APPROVED blocks, e.g. JUN-2026')

    selected_items = models.ManyToManyField(
        PrequalLibraryItem, blank=True, related_name='submissions')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='prequal_submissions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    def selected_in_order(self):
        """Selected library items that have a PDF, in library (heading) order."""
        return self.selected_items.filter(is_active=True, pdf__isnull=False).exclude(pdf='').order_by('order', 'heading')


class SectionHeadingTemplate(models.Model):
    """A department-specific, ready-to-load version of a section heading's
    content (rich text + images). Lets the editor pre-fill a section with a
    fully composed template instead of the single generic default_content.
    Does not replace or modify SectionHeading.default_content in any way —
    that fallback keeps working exactly as before for anyone who ignores this.
    """
    DEPARTMENT_CHOICES = [
        ('ai', 'AI'),
        ('telecom', 'Telecom'),
        # DB value stays 'procurement' (existing rows, URL params, etc. are
        # unaffected) — only the display label changed to match the toggle
        # button, which is labelled "Security" in the editor UI.
        ('procurement', 'Security'),
    ]

    heading = models.ForeignKey(
        SectionHeading, on_delete=models.CASCADE, related_name='dept_templates')
    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES)
    content = models.TextField(
        blank=True, help_text='Rich HTML content, same format as a proposal section.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('heading', 'department')
        ordering = ['heading__order', 'department']

    def __str__(self):
        return f'{self.heading.name} — {self.get_department_display()}'


class ProposalMailbox(models.Model):
    """One employee's own mailbox for linking client emails to a Technical
    Proposal — never shared. Same exact design as costing.RevisionMailbox /
    projects.MonitoredMailbox (one row per user, OneToOne both ways, an
    admin links each employee to their own real mailbox address) — kept as
    a separate, self-contained copy in this app rather than a cross-app
    import, since those models live on different, not-yet-merged branches
    and this app shouldn't depend on their migration state.

    The privacy guarantee is identical: only the linked employee can ever
    browse their own mailbox through this feature, and which mailbox to use
    is always derived from request.user server-side (see
    proposals/views.py:_user_proposal_mailbox) — never from anything the
    client sends."""

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='proposal_mailbox',
        help_text='The employee this mailbox belongs to. Only they can browse it.',
    )
    email_address = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        help_text='The admin who assigned this mailbox.',
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        help_text='The admin who last revoked this mailbox. Cleared on reactivation.',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['owner__username']

    def __str__(self):
        return f'{self.owner} — {self.email_address}'


class ProposalDepartmentFeature(models.Model):
    """Super-Admin-only per-department switch: does exporting a Technical
    Proposal for this department require a client email to be linked first?
    One row per department, created on demand — a department with no row
    yet behaves as 'not required' (see requires_email()), so this feature
    changes nothing until a Super Admin explicitly turns it on."""

    department = models.CharField(
        max_length=20, choices=PROPOSAL_LOCKABLE_DEPARTMENTS, unique=True)
    requires_client_email_to_export = models.BooleanField(
        default=False,
        help_text='If checked, a Technical Proposal in this department cannot be '
                   'exported as DOCX until a client email has been linked to it.',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        ordering = ['department']

    def __str__(self):
        return f'{self.get_department_display()} — {"locked" if self.requires_client_email_to_export else "unlocked"}'

    @classmethod
    def requires_email(cls, department):
        """False for an empty/unknown department, and False the very first
        time a department is checked (get_or_create with the field's own
        default) — a brand new department is unlocked until a Super Admin
        opts it in, never the other way around."""
        if not department:
            return False
        obj, _ = cls.objects.get_or_create(department=department)
        return obj.requires_client_email_to_export


class ProposalLinkedEmail(models.Model):
    """One client email linked to a Technical Proposal - read live from the
    linking employee's own mailbox via Microsoft Graph (Mail.Read) and
    recorded here as metadata only. Always an inbound client email; there is
    no 'sent by us' concept for this feature (proposals aren't emailed out
    from inside the ERP, only linked back once a client has replied)."""

    proposal = models.ForeignKey(
        TechnicalProposal,
        on_delete=models.CASCADE,
        related_name='linked_emails',
    )
    graph_message_id = models.CharField(max_length=255, unique=True)
    mailbox = models.EmailField(
        help_text='The mailbox this was read from - recorded at link time so a later '
                   'attachment download always uses this, never the current viewer\'s own.',
    )
    sender_name = models.CharField(max_length=255, blank=True)
    sender_email = models.EmailField(blank=True)
    to_recipients = models.CharField(max_length=1000, blank=True)
    cc_recipients = models.CharField(max_length=1000, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    body_html = models.TextField(blank=True)
    body_text = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    has_attachments = models.BooleanField(default=False)
    attachment_meta = models.JSONField(null=True, blank=True)
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='proposal_emails_linked',
    )
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Display order should match the order a person actually linked
        # these, not the email's own sent_at - see the identical reasoning
        # already applied to costing.RevisionEmailMessage.
        ordering = ['pk']

    def __str__(self):
        return f'{self.proposal} — {self.subject}'
