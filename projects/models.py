import re

from django.db import models
from django.conf import settings


# Auto-generated LNA reference: "LNA <number> - <project name>", numbers
# auto-incrementing from this floor. (Region code 'LNA'.)
LNA_REFERENCE_START = 2870
# Canonical (new) name-bearing format: "LNA 2870 - Project Name" (optionally with
# a trailing revision). Used to decide when it's safe to rebuild the reference
# from the project name. Non-canonical refs (legacy codes, dash-joined imports)
# only get their trailing revision swapped, never reformatted.
CANONICAL_LNA_RE = re.compile(r'^LNA \d+ - ')
LNA_REFERENCE_RE = CANONICAL_LNA_RE  # back-compat alias
# A trailing revision token in any style seen in the data:
#   " (R03)"  ·  "-R03"  ·  "- R03"  ·  "_R03"  ·  " R03"
_TRAILING_REV_RE = re.compile(
    r'\s*(\(\s*R\d+\s*\)|[-_]\s*R\d+|\sR\d+)\s*$', re.IGNORECASE)
# The LNA number right after the 'LNA' prefix (optional separators / zero pad).
_LNA_NUMBER_RE = re.compile(r'^LNA[\s\-_]*0*(\d+)', re.IGNORECASE)


def split_trailing_revision(ref):
    """Split a reference into (base, revision, style). revision normalised to
    'R<n>'; style is 'paren' | 'dash' | 'space' | None. base is everything
    before the trailing revision, left untouched."""
    ref = (ref or '').rstrip()
    m = _TRAILING_REV_RE.search(ref)
    if not m:
        return ref, None, None
    token = m.group(1)
    num = re.search(r'R(\d+)', token, re.IGNORECASE)
    revision = f'R{num.group(1)}' if num else None
    lead = token.lstrip()
    if lead.startswith('('):
        style = 'paren'
    elif lead[:1] in ('-', '_'):
        style = 'dash'
    else:
        style = 'space'
    return ref[:m.start()].rstrip(), revision, style


def join_trailing_revision(base, revision, style='paren'):
    """Re-attach a revision to a base in the given style."""
    if not revision:
        return base
    revision = revision.upper()
    if style == 'dash':
        return f'{base}-{revision}'
    if style == 'space':
        return f'{base} {revision}'
    return f'{base} ({revision})'


def build_lna_reference(number, project_name, revision=None):
    """Compose a canonical LNA reference: 'LNA <number> - <name>', plus a
    ' (R03)' tail when a revision is supplied. Capped to the field length."""
    base = f'LNA {number} - {(project_name or "").strip()}'
    return join_trailing_revision(base, revision, 'paren')[:255]


def parse_lna_reference(ref):
    """Parse any LNA reference into (number, revision_or_None). Handles the
    canonical 'LNA #### - name (R03)', pure legacy codes ('LNA-2289',
    'LNA02158-R03') and dash-joined imports ('LNA-2817-Name-R04'). Returns None
    when there's no recognizable LNA number."""
    ref = (ref or '').strip()
    if not ref:
        return None
    base, revision, _style = split_trailing_revision(ref)
    m = _LNA_NUMBER_RE.match(base) or _LNA_NUMBER_RE.match(ref)
    if not m:
        return None
    return int(m.group(1)), revision


def lna_reference_kind(ref):
    """Classify an LNA reference for how it should be maintained:
      'canonical' — "LNA #### - name …"        → rebuilt from project name
      'code'      — "LNA-2289" / "LNA02158-R03" → safe to canonicalise (no name)
      'named'     — "LNA-2817-Name-R04"         → name embedded; only swap revision
      None        — not an LNA reference        → leave untouched
    """
    ref = (ref or '').strip()
    if not ref:
        return None
    if CANONICAL_LNA_RE.match(ref):
        return 'canonical'
    base, _rev, _style = split_trailing_revision(ref)
    m = _LNA_NUMBER_RE.match(base)
    if not m:
        return None
    remainder = base[m.end():].strip(' -_')
    return 'code' if remainder == '' else 'named'


def next_lna_reference_number():
    """Next LNA sequence number: the LOWEST free number from LNA_REFERENCE_START
    upward — so gaps left by an earlier skip are reused rather than jumped over.

    Every used number counts as taken (canonical, code AND imported dash-joined
    refs) so a new number never collides with an existing project.
    """
    used = set()
    refs = (Project.objects
            .filter(proposal_reference__istartswith='LNA')
            .values_list('proposal_reference', flat=True))
    for ref in refs:
        parsed = parse_lna_reference(ref)
        if parsed:
            used.add(parsed[0])
    n = LNA_REFERENCE_START
    while n in used:
        n += 1
    return n


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


class ProjectManager(models.Manager):
    """Default manager — hides soft-deleted projects.

    Every existing queryset (`Project.objects...`) therefore skips deleted
    rows automatically. Use `Project.all_objects` to include them, which the
    recycle bin does.
    """

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


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
        max_length=255,
        unique=True,
        help_text="Leap Proposal Reference. Auto-generated for LNA (LNA #### - project name)."
    )
    client_rfq_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="Client RFQ Reference Number"
    )
    po_number = models.CharField(max_length=100, blank=True, help_text="Purchase Order Number")

    # Dates
    submission_deadline = models.DateField(null=True, blank=True)
    submission_deadline = models.DateField(null=True, blank=True)
    bom_started_deadline = models.DateField(
        null=True, blank=True,
        help_text='Planned date for BOM to start. Used to show early/late variance.')
    handed_over_deadline = models.DateField(
        null=True, blank=True,
        help_text='Planned date to hand the BOM to sales.')
    costing_started_deadline = models.DateField(
        null=True, blank=True,
        help_text='Planned date for sales to start costing.')
    finalized_deadline = models.DateField(
        null=True, blank=True,
        help_text='Planned date to finalise the sheet.')
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
    customer = models.CharField(
        max_length=200,
        blank=True,
        help_text="Customer"
    )
    end_user = models.CharField(
        max_length=200,
        blank=True,
        help_text="End User"
    )

    PROJECT_STAGE_CHOICES = [
        ('', '-'),
        ('procurement', 'Procurement Stage'),
        ('building', 'Bidding Stage'),  # stored code kept as 'building' (legacy); label corrected
    ]
    project_stage = models.CharField(
        max_length=20,
        choices=PROJECT_STAGE_CHOICES,
        blank=True,
        verbose_name="Project Stage"
    )

    PRIORITY_CHOICES = [
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        blank=True,
        help_text="Pipeline priority"
    )

    status = models.ForeignKey(
        ProjectStatus,
        on_delete=models.PROTECT,
        related_name='projects'
    )

    # Why an opportunity was lost. Captured as a choice rather than free text
    # so "why are we losing work" is answerable later — a comment field alone
    # cannot be grouped or counted.
    LOST_PRICE = 'price'
    LOST_COMPETITOR = 'competitor'
    LOST_CANCELLED = 'cancelled'
    LOST_BUDGET = 'budget'
    LOST_TIMELINE = 'timeline'
    LOST_TECHNICAL = 'technical'
    LOST_NO_DECISION = 'no_decision'
    LOST_NO_BID = 'no_bid'
    LOST_OTHER = 'other'
    LOST_REASON_CHOICES = [
        (LOST_PRICE, 'Price — we were too expensive'),
        (LOST_COMPETITOR, 'Lost to competitor'),
        (LOST_CANCELLED, 'Client cancelled — project shelved'),
        (LOST_BUDGET, 'Budget — client could not fund it'),
        (LOST_TIMELINE, 'Timeline — could not meet required dates'),
        (LOST_TECHNICAL, 'Technical — solution did not meet requirements'),
        (LOST_NO_DECISION, 'No decision — client went quiet'),
        # Deliberately separate from the losses above: choosing not to pursue
        # something is not the same as being beaten, and lumping them together
        # would distort any analysis of why work is lost.
        (LOST_NO_BID, 'Did not bid — we chose not to pursue'),
        (LOST_OTHER, 'Other (comment required)'),
    ]
    lost_reason = models.CharField(
        max_length=20, choices=LOST_REASON_CHOICES, blank=True,
        help_text='Why the opportunity was lost. Required once the status is Lost.')
    lost_comment = models.TextField(
        blank=True,
        help_text='Context for the loss. Required when the reason is Other.')

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

    # Soft delete — a "deleted" project is hidden everywhere but retained in
    # the database so it can be restored from the recycle bin.
    #
    # A hard delete is genuinely destructive here: it CASCADEs into
    # ProjectFinance, CashOutflowRow, ProjectHistory and ProjectRevision, and
    # SET_NULLs the project link on costing sheets, purchase orders and
    # proposals — so even restoring the project row later leaves those
    # relationships broken. Soft delete keeps every relationship intact.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='deleted_projects'
    )

    # `objects` hides soft-deleted rows so existing code needs no changes;
    # `all_objects` sees everything and backs the recycle bin.
    objects = ProjectManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.proposal_reference} - {self.project_name}"

    def soft_delete(self, user=None):
        """Hide the project without touching any related record."""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user if (user and user.is_authenticated) else None
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'updated_at'])


    def _milestone_variance(self, deadline, actual_datetime):
        # Positive = ahead of schedule (finished before the deadline).
        # Negative = behind schedule (finished after the deadline).
        # None = not enough data yet (missing deadline or not reached).
        if not deadline or not actual_datetime:
            return None
        actual_date = actual_datetime.date() if hasattr(actual_datetime, 'date') else actual_datetime
        return (deadline - actual_date).days

    @property
    def milestone_display_rows(self):
        # One combined row per milestone - date, who did it, and the +/- day
        # variance versus this project's deadline - ready for the template to
        # loop through directly with no separate lookup needed.
        from django.utils import timezone
        sheet = getattr(self, 'cycle_sheet', None)
        base_rows = sheet.milestone_rows() if sheet else [
            {'label': lbl, 'date': '\u2014', 'person': None} for lbl in
            ('BOM started', 'Handed to sales', 'Costing started', 'Finalised')
        ]
        deadlines = {
            'BOM started': self.bom_started_deadline,
            'Handed to sales': self.handed_over_deadline,
            'Costing started': self.costing_started_deadline,
            'Finalised': self.finalized_deadline,
        }
        actuals = {
            'BOM started': getattr(sheet, 'bom_started_at', None),
            'Handed to sales': getattr(sheet, 'handed_over_at', None),
            'Costing started': getattr(sheet, 'costing_started_at', None),
            'Finalised': getattr(sheet, 'finalized_at', None),
        }
        out = []
        for row in base_rows:
            label = row['label']
            variance = self._milestone_variance(deadlines.get(label), actuals.get(label))
            variance_display = None
            if variance is not None:
                variance_display = f'+{variance}d' if variance >= 0 else f'{variance}d'
            out.append({
                'label': label,
                'date': row['date'],
                'person': row['person'],
                'variance_display': variance_display,
                'variance_class': 'text-success' if (variance or 0) >= 0 else 'text-danger',
            })
        return out
    @property
    def milestone_overall_status(self):
        # 'success' (green) = Finalised is done.
        # 'danger' (red) = today is already past a milestone's deadline and
        #   that milestone still hasn't happened.
        # 'warning' (orange) = still in progress, nothing overdue yet.
        # '' = not enough data to judge (no deadlines set at all).
        from django.utils import timezone
        sheet = getattr(self, 'cycle_sheet', None)
        if sheet and getattr(sheet, 'finalized_at', None):
            return 'success'
        today = timezone.now().date()
        pairs = [
            (self.bom_started_deadline, getattr(sheet, 'bom_started_at', None)),
            (self.handed_over_deadline, getattr(sheet, 'handed_over_at', None)),
            (self.costing_started_deadline, getattr(sheet, 'costing_started_at', None)),
            (self.finalized_deadline, getattr(sheet, 'finalized_at', None)),
        ]
        any_deadline_set = any(dl for dl, _ in pairs)
        if not any_deadline_set:
            return ''
        for deadline, actual in pairs:
            if deadline and not actual and today > deadline:
                return 'danger'
        return 'warning'
    def restore(self):
        """Bring a soft-deleted project back, relationships intact."""
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'updated_at'])

    def save(self, *args, **kwargs):
        # Maintain the auto LNA reference on EVERY save path (form, admin,
        # scripts). Canonical refs and pure legacy codes are (re)built as
        # "LNA #### - <name> (R03)" so renaming reflects, preserving number +
        # revision. Refs with a name already embedded in a non-canonical format
        # (e.g. "LNA-2817-Name-R04") are left as-is — only their revision is
        # edited, in place, via the form. Non-LNA refs are untouched.
        if (self.region_id and getattr(self.region, 'code', None) == 'LNA'
                and self.proposal_reference):
            kind = lna_reference_kind(self.proposal_reference)
            if kind in ('canonical', 'code'):
                number, revision = parse_lna_reference(self.proposal_reference)
                self.proposal_reference = build_lna_reference(
                    number, self.project_name, revision)
        super().save(*args, **kwargs)

    @property
    def weighted_value(self):
        """Calculate weighted value based on success quotient"""
        return self.estimated_value * self.success_quotient

    @property
    def status_category(self):
        """Get the status category"""
        return self.status.category if self.status else None

    @property
    def is_lost(self):
        return self.status_category == 'lost'

    @property
    def lost_summary(self):
        """One-line 'why we lost it', or '' when the project is not lost.

        Deliberately does not clear itself when a project moves back out of
        Lost — revivals happen, and wiping the reason would lose the record of
        what went wrong the first time.
        """
        if not self.is_lost or not self.lost_reason:
            return ''
        label = dict(self.LOST_REASON_CHOICES).get(self.lost_reason, self.lost_reason)
        return f'{label} — {self.lost_comment}' if self.lost_comment else label


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


class ProjectRevision(models.Model):
    """Point-in-time snapshot of a Commercial Proposal (Project).

    Created explicitly via the "Create Revision" button. The snapshot
    JSON is immutable — it never gets overwritten, so past states can
    always be retrieved exactly as they were at the time of the revision.
    """
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='revisions',
    )
    revision_label = models.CharField(
        max_length=16,
        help_text='Auto-assigned label like R00, R01, R02, ...',
    )
    snapshot = models.JSONField(
        help_text='Full snapshot of the project fields at save time',
    )
    notes = models.TextField(
        blank=True,
        help_text='Optional note about why this revision was created',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_revisions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('project', 'revision_label')
        verbose_name = 'Commercial Proposal Revision'
        verbose_name_plural = 'Commercial Proposal Revisions'

    def __str__(self):
        return f'{self.project.proposal_reference} — {self.revision_label}'

    @classmethod
    def next_label_for(cls, project):
        """Return the next R-label for a given project (R00 if none)."""
        last = cls.objects.filter(project=project).order_by('-created_at').first()
        if not last:
            return 'R00'
        label = last.revision_label or ''
        if label.startswith('R') and label[1:].isdigit():
            n = int(label[1:]) + 1
            return f'R{n:02d}'
        # Fallback: count-based
        count = cls.objects.filter(project=project).count()
        return f'R{count:02d}'


def document_upload_path(instance, filename):
    """Generate upload path for documents"""
    return f'documents/{instance.document_type}/{filename}'


class Document(models.Model):
    """Document management for projects"""
    DOCUMENT_TYPE_CHOICES = [
        ('rfq', 'Client RFQ'),
        ('vendor_quotation', 'Vendor Quotation'),
        ('proposal', 'Proposal'),
        ('customer_document', 'Customer Document'),
        ('technical_document', 'Technical Document'),
        ('po_document', 'Purchase Order'),
        ('contract', 'Contract'),
        ('drawing', 'Drawing'),
        ('scope_of_work', 'Scope of Work'),
        ('instructions', 'Instructions'),
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
