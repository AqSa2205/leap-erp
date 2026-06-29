from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models


class ProjectFinance(models.Model):
    """Finance workflow record for a Won project — Step 2 of finance approval.

    Holds the approved Project P.O Value (carried over from the approved margin
    scenario in Step 1) and the kickoff date that drives the milestone schedule.
    One per project.
    """

    MARGIN_CHOICES = [
        ('M1', 'M1 — Current'),
        ('M2', 'M2 — High'),
        ('M3', 'M3 — Medium'),
        ('M4', 'M4 — Low'),
    ]

    project = models.OneToOneField(
        'projects.Project', on_delete=models.CASCADE, related_name='finance')
    po_value = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal('0'),
        help_text='Project P.O Value — the approved contract price (from the chosen margin).')
    approved_margin = models.CharField(
        max_length=2, choices=MARGIN_CHOICES, blank=True,
        help_text='Which margin scenario (M1–M4) the P.O Value came from.')
    source_sheet = models.ForeignKey(
        'costing.CostingSheet', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', help_text='Costing sheet the approved margin was taken from.')
    kickoff_date = models.DateField(
        null=True, blank=True,
        help_text='Project kickoff meeting date — milestone dates are offset from this.')
    estimated_start_date = models.DateField(
        null=True, blank=True, help_text='Estimated project start date.')
    estimated_end_date = models.DateField(
        null=True, blank=True, help_text='Estimated project completion date.')
    # Standard gaps (days) used to chain the four milestone dates off the
    # kickoff anchor: prep = kickoff + from_kickoff_days; approval = prep + gap;
    # submission = approval + gap; payment = submission + gap.
    cert_approval_gap = models.PositiveIntegerField(default=2)
    invoice_gap = models.PositiveIntegerField(default=30)
    payment_gap = models.PositiveIntegerField(default=30)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='project_finances')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'Finance: {self.project.project_name}'

    # Default milestone template seeded on first open. (name, is_subrow).
    DEFAULT_MILESTONES = [
        ('Upon Design Submission', False),
        ('Design Approval', False),
        ('Vendor PO Acknowledgement (Progressive Invoice)', False),
        ('Progressive Invoice (Weightage Wise)', True),
        ('Upon Material Delivery (Progressive Invoice)', False),
        ('Progressive Invoice (Weightage Wise)', True),
        ('Progressive Invoice (Weightage Wise)', True),
        ('Progressive Invoice (Weightage Wise)', True),
        ('Project Sign Off', False),
    ]

    def seed_default_milestones(self):
        """Create the standard milestone rows if none exist yet."""
        if self.milestones.exists():
            return
        for order, (name, is_sub) in enumerate(self.DEFAULT_MILESTONES):
            PaymentMilestone.objects.create(
                project_finance=self, name=name, is_subrow=is_sub, order=order)

    @property
    def submitted_total_pct(self):
        return sum((m.submitted_pct or Decimal('0')) for m in self.milestones.all())

    def recompute_dates(self):
        """Fill every milestone's four dates from the kickoff + day offsets +
        standard gaps. Overwrites existing dates (the 'recompute' action)."""
        if not self.kickoff_date:
            return
        for m in self.milestones.all():
            if m.from_kickoff_days is None:
                continue
            prep = self.kickoff_date + timedelta(days=m.from_kickoff_days)
            approval = prep + timedelta(days=self.cert_approval_gap)
            submission = approval + timedelta(days=self.invoice_gap)
            payment = submission + timedelta(days=self.payment_gap)
            m.work_cert_prep_date = prep
            m.work_cert_approval_date = approval
            m.invoice_submission_date = submission
            m.payment_receive_date = payment
            m.save(update_fields=[
                'work_cert_prep_date', 'work_cert_approval_date',
                'invoice_submission_date', 'payment_receive_date'])


class PaymentMilestone(models.Model):
    """One row of a project's payment / cash-flow schedule.

    Each milestone carries weightages under four scenarios (Best / Average /
    Proposed / Submitted) — Submitted is the final mix-and-match used for the
    billed amount. Dates are offset from the project's kickoff date.
    """

    project_finance = models.ForeignKey(
        ProjectFinance, on_delete=models.CASCADE, related_name='milestones')
    name = models.CharField(max_length=255)
    # Progressive "(Weightage Wise)" lines are indented sub-rows of the
    # milestone above them and are not numbered in the S.No column.
    is_subrow = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    invoice_no = models.CharField(max_length=50, blank=True)
    from_kickoff_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Days from kickoff to this milestone (drives the dates).')
    remarks = models.TextField(blank=True)

    # Weightage scenarios (percent, e.g. 10 = 10%).
    best_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    average_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    proposed_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    submitted_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # Dates — auto-computed from kickoff + offsets unless overridden.
    work_cert_prep_date = models.DateField(null=True, blank=True)
    work_cert_approval_date = models.DateField(null=True, blank=True)
    invoice_submission_date = models.DateField(null=True, blank=True)
    payment_receive_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.name

    @property
    def amount(self):
        """Billed amount for this milestone = Submitted % × Project P.O Value."""
        pct = self.submitted_pct or Decimal('0')
        return (self.project_finance.po_value * pct / Decimal('100')).quantize(Decimal('0.01'))
