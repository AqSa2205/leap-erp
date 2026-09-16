"""Project execution milestones — the WBS the delivery team works to.

This replaces the ten per-project milestone sheets in the projects overview
workbook. Each project has a small tree of activities: parents (1, 2, 3) that
group, and children (1.1, 1.2) that carry the weight. Weight is a fraction of
the project, the children under a parent summing to 1.00, and completion is
weight times how much of each child is done.

Deliberately *not* the same table as `finance.PaymentMilestone`. That is the
billing schedule, seeded during budgeting and locked while finance owns the
sheet — the delivery team updates progress weekly and cannot work behind that
lock. The two are joined by an optional FK instead, so a milestone that bills
can say which invoice it bills against without either side owning the other.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import models

ONE = Decimal('1')
ZERO = Decimal('0')

# Weights are compared with one ten-thousandth of slack. Not for floating-point
# error — these are Decimals at four places, so the arithmetic is exact — but
# because a whole split three ways is 0.3333 × 3 = 0.9999 and there is no way
# to write it otherwise. Forgiving the last digit keeps the warning meaningful;
# a tolerance any wider would start hiding a missing activity.
WEIGHTAGE_TOLERANCE = Decimal('0.0001')


class ProjectMilestone(models.Model):
    """One activity row. A parent groups; a leaf carries weight and progress.

    Progress lives only on leaves. A parent's figures are the sum of its
    children, computed rather than stored — the workbook stored both and the
    stored copy is what went stale.
    """

    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, related_name='milestones')

    # Null for a top-level activity. Self-referential rather than a depth
    # field: the sheets are two levels today, but nothing here assumes that.
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='children')

    order = models.PositiveIntegerField(
        default=0,
        help_text='Position within the parent. Drives the 1, 1.1, 1.2 numbering.')
    activity = models.CharField(max_length=500)

    weightage = models.DecimalField(
        max_digits=6, decimal_places=4, default=ZERO,
        help_text='Fraction of the project this activity represents (0–1). '
                  'The activities that carry weight sum to 1.00 across the '
                  'project — either the top-level rows, or their children '
                  'where the parent is left blank.')

    # The one field that changes week to week. Everything else on this row is
    # set once when the WBS is agreed.
    completed_fraction = models.DecimalField(
        max_digits=6, decimal_places=4, default=ZERO,
        help_text='How much of this activity is done, 0–1.')

    completion_date = models.DateField(
        null=True, blank=True,
        help_text='Date the activity was actually achieved.')
    invoice_prerequisite = models.TextField(
        blank=True,
        help_text='The document needed before this can be invoiced — delivery '
                  'note, transmittal, completion report.')
    responsible_party = models.CharField(
        max_length=100, blank=True,
        help_text='Whose court this activity is in right now — e.g. "Client", '
                  '"LNA", "MASCO". Free text to match how delivery actually '
                  'names the parties involved, project to project.')

    # Optional, and SET_NULL on purpose: finance re-seeding its schedule must
    # never delete a delivery milestone.
    payment_milestone = models.ForeignKey(
        'finance.PaymentMilestone', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='delivery_milestones',
        help_text='The billing row this activity invoices against, if any. '
                  'Where set, cash-in is read from finance rather than retyped.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'pk']
        indexes = [
            models.Index(fields=['project', 'order']),
        ]

    def __str__(self):
        return f'{self.number} {self.activity}'[:80]

    # ── shape ───────────────────────────────────────────────────────────────

    @property
    def is_leaf(self):
        """A row with no children carries the weight and the progress.

        Uses the prefetched cache when there is one, so rendering a whole WBS
        does not issue a query per row.
        """
        return not self.children.all().exists()

    @property
    def number(self):
        """The 1 / 1.1 display number, derived from position rather than typed.

        The workbook typed these by hand, which is why inserting a row there
        silently renumbered nothing.
        """
        if self.parent_id is None:
            return str(self.order)
        return f'{self.parent.order}.{self.order}'

    # ── progress ────────────────────────────────────────────────────────────

    @property
    def pending_fraction(self):
        return ONE - self.completed_fraction

    @property
    def completed_weightage(self):
        """Weight actually earned. For a parent, the sum of its children."""
        children = list(self.children.all())
        if children:
            return sum((c.completed_weightage for c in children), ZERO)
        return self.weightage * self.completed_fraction

    @property
    def pending_weightage(self):
        """Weight still outstanding.

        Computed as total minus completed rather than independently, so
        `completed + pending == total` holds by construction. The workbook
        computed the two separately and nothing ever checked they agreed.
        """
        return self.total_weightage - self.completed_weightage

    @property
    def total_weightage(self):
        """This row's weight — for a parent, the sum of its children's."""
        children = list(self.children.all())
        if children:
            return sum((c.weightage for c in children), ZERO)
        return self.weightage


class MilestoneProgressEntry(models.Model):
    """One weekly update, appended rather than overwritten.

    The workbook used TODAY() as a stored field in about forty places, so it
    could never say when a number was actually last touched — the date always
    read as today, whether the figure moved this morning or last March. Each
    update is a row here instead, which answers that and gives progress over
    time as a side effect.
    """

    milestone = models.ForeignKey(
        ProjectMilestone, on_delete=models.CASCADE, related_name='progress_entries')
    completed_fraction = models.DecimalField(max_digits=6, decimal_places=4)
    note = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='milestone_updates')

    class Meta:
        ordering = ['-recorded_at']
        verbose_name_plural = 'Milestone progress entries'

    def __str__(self):
        return f'{self.milestone_id} → {self.completed_fraction} at {self.recorded_at:%Y-%m-%d}'


# ── Client-facing project documents (PM Dashboard) ───────────────────────────
#
# Two small JSON-grid records per project — fully customizable columns rather
# than a fixed schema, since this is a first version and the exact columns a
# PM wants are still settling. Same columns/rows shape as
# costing.ClientRemarkTemplate: columns = [{"key","name"}], rows =
# [{"cells": {key: {"text": str}}}].

def default_responsibility_columns():
    """Starting columns for a fresh Responsibility Matrix — a RACI table."""
    return [
        {'key': 'c1', 'name': 'Task / Activity'},
        {'key': 'c2', 'name': 'Responsible'},
        {'key': 'c3', 'name': 'Accountable'},
        {'key': 'c4', 'name': 'Consulted'},
        {'key': 'c5', 'name': 'Informed'},
        {'key': 'c6', 'name': 'Notes'},
    ]


def default_communication_columns():
    """Starting columns for a fresh Communication Matrix."""
    return [
        {'key': 'c1', 'name': 'Communication Item'},
        {'key': 'c2', 'name': 'Frequency'},
        {'key': 'c3', 'name': 'Owner'},
        {'key': 'c4', 'name': 'Client Audience'},
        {'key': 'c5', 'name': 'Channel'},
        {'key': 'c6', 'name': 'Deliverable'},
        {'key': 'c7', 'name': 'Notes'},
    ]


def sanitize_grid(columns_raw, rows_raw, fallback_columns):
    """Coerce posted columns/rows JSON into the stored grid shape, dropping
    anything malformed. Mirrors costing._sanitize_remark_grid's contract.

    columns -> [{"key": "c1", "name": "..."}, ...]  (keys forced unique,
                blank names allowed — the header is free-text)
    rows    -> [{"cells": {key: {"text": str}}}, ...]  (only cells whose key
                maps to a surviving column are kept; all-empty rows dropped)
    """
    columns, seen_keys = [], set()
    for idx, col in enumerate(columns_raw if isinstance(columns_raw, list) else []):
        if not isinstance(col, dict):
            continue
        key = str(col.get('key') or '').strip() or f'c{idx + 1}'
        while key in seen_keys:
            key = f'{key}_{idx}'
        seen_keys.add(key)
        name = str(col.get('name') or '').strip()
        columns.append({'key': key, 'name': name})

    if not columns:
        columns = list(fallback_columns)
        seen_keys = {c['key'] for c in columns}

    rows = []
    for row in rows_raw if isinstance(rows_raw, list) else []:
        cells_raw = (row or {}).get('cells') if isinstance(row, dict) else None
        cells = {}
        if isinstance(cells_raw, dict):
            for key, data in cells_raw.items():
                if key not in seen_keys or not isinstance(data, dict):
                    continue
                cells[key] = {'text': str(data.get('text') or '')}
        if any((c.get('text') or '').strip() for c in cells.values()):
            rows.append({'cells': cells})
    return columns, rows


class ResponsibilityMatrix(models.Model):
    """Who does what on a project — a RACI-style grid, columns customizable
    per project rather than a fixed schema."""
    project = models.OneToOneField(
        'projects.Project', on_delete=models.CASCADE,
        related_name='responsibility_matrix',
    )
    columns = models.JSONField(default=default_responsibility_columns)
    rows = models.JSONField(default=list)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Responsibility Matrix — {self.project.project_name}"


class CommunicationMatrix(models.Model):
    """The project's communication plan, shared with the client as a PDF
    report. Same customizable JSON-grid shape as ResponsibilityMatrix."""
    project = models.OneToOneField(
        'projects.Project', on_delete=models.CASCADE,
        related_name='communication_matrix',
    )
    columns = models.JSONField(default=default_communication_columns)
    rows = models.JSONField(default=list)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Communication Matrix — {self.project.project_name}"


# ── Manpower Status ───────────────────────────────────────────────────────
#
# Project Management's own view onto a resource — site or office — adding
# the handful of fields this department tracks that HR has no reason to
# (which project-facing title, which contract period, discipline,
# seniority level). Identity (name, ID number, whether they're still
# active) always comes from the linked hr.Employee record rather than
# being retyped here, so there is exactly one place that can go stale.

class ManpowerResource(models.Model):
    ENGAGEMENT_CHOICES = [
        ('direct', 'Direct'),
        ('subcontract', 'Subcontract'),
        ('agency', 'Agency'),
    ]
    DISCIPLINE_CHOICES = [
        ('engineering', 'Engineering'),
        ('technical', 'Technical'),
    ]
    LEVEL_CHOICES = [
        ('entry', 'Entry-Level'),
        ('mid_senior', 'Mid-Senior Level'),
        ('senior', 'Senior Level'),
    ]

    employee = models.OneToOneField(
        'hr.Employee', on_delete=models.CASCADE, related_name='manpower_resource',
        help_text='Name, ID number and active/inactive status all come from this record.')
    title = models.CharField(
        max_length=255, blank=True,
        help_text="This assignment's working title, if different from the "
                  "employee's HR designation.")
    start_contract_date = models.DateField(null=True, blank=True)
    end_contract_date = models.DateField(
        null=True, blank=True,
        help_text='Leave blank while still on an open-ended assignment.')
    engagement_type = models.CharField(max_length=20, choices=ENGAGEMENT_CHOICES, blank=True)
    discipline = models.CharField(max_length=20, choices=DISCIPLINE_CHOICES, blank=True)
    employment_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='manpower_resources_added',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee__full_name']

    def __str__(self):
        return f'Manpower — {self.employee.full_name}'

    @property
    def title_display(self):
        return self.title or self.employee.designation

    @property
    def experience_days(self):
        """Calendar days between the contract start and either its end date
        (once demobilized) or today (while still ongoing). Computed rather
        than stored, so it's never out of date."""
        if not self.start_contract_date:
            return None
        end = self.end_contract_date or date.today()
        return (end - self.start_contract_date).days

    @property
    def experience_months(self):
        days = self.experience_days
        return round(days / 30.44, 2) if days is not None else None

    @property
    def experience_years(self):
        days = self.experience_days
        return round(days / 365.25, 2) if days is not None else None


# ── Issue Log ─────────────────────────────────────────────────────────────
#
# The project delivery team's own risk/issue register — replaces the
# "All Projects Overview" issue log sheet. Browsed and exported one month
# at a time (by date_identified), same shape as engineer_calendar's
# calendar: a live table for the current month, plus an Excel export in the
# same column order the workbook already used, so a PM can hand the export
# straight to a client without reformatting it first.

class ProjectIssue(models.Model):
    STATUS_OPEN = 'open'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_CLOSED, 'Closed'),
    ]

    PRIORITY_CRITICAL = 'critical'
    PRIORITY_HIGH = 'high'
    PRIORITY_MEDIUM = 'medium'
    PRIORITY_LOW = 'low'
    PRIORITY_CHOICES = [
        (PRIORITY_CRITICAL, 'Critical'),
        (PRIORITY_HIGH, 'High'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_LOW, 'Low'),
    ]
    # Matches the severity legend on the source workbook exactly (colour ->
    # meaning), reused for both the on-screen badge and the Excel export so
    # the two can never show a different colour for the same priority.
    PRIORITY_COLORS = {
        PRIORITY_CRITICAL: 'FF0000',
        PRIORITY_HIGH: 'ED7D31',
        PRIORITY_MEDIUM: 'FFC000',
        PRIORITY_LOW: '70AD47',
    }

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    description = models.TextField()
    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, related_name='issues')
    owner = models.CharField(
        max_length=255, blank=True,
        help_text='Who owns resolving this — a client, a vendor, or Leap itself '
                  '(e.g. "Aramco", "MASCO", "LEAP").')
    estimated_resolution_date = models.DateField(null=True, blank=True)
    escalation_needed = models.BooleanField(default=False)
    impact = models.TextField(blank=True)
    actions = models.TextField(blank=True, help_text='What is being done about it.')
    date_identified = models.DateField(
        help_text='Drives which month this issue is filed/exported under.')
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='logged_issues')
    actual_resolution_date = models.DateField(
        null=True, blank=True, help_text='Left blank while still open.')
    final_resolution = models.TextField(
        blank=True, verbose_name='Final Resolution & Follow-on Actions')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_identified', '-pk']

    def __str__(self):
        return f'Issue #{self.pk} — {self.project.project_name}'

    @property
    def priority_color(self):
        return self.PRIORITY_COLORS.get(self.priority, '6c757d')
