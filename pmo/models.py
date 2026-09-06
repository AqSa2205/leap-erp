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
