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

    def _final_margin_sheet(self):
        """The costing sheet whose final (M5) margin drives the P.O Value — the
        approved source sheet, else any project sheet with a final margin set."""
        if self.source_sheet_id and self.source_sheet.margin_final is not None:
            return self.source_sheet
        from costing.models import CostingSheet
        return (CostingSheet.objects
                .filter(project=self.project, margin_final__isnull=False)
                .order_by('-updated_at').first())

    def po_value_from_final_margin(self):
        """P.O Value fetched from the final approved margin (M5) grand price on
        the costing sheet, or None if no final margin is set anywhere."""
        sheet = self._final_margin_sheet()
        if sheet is None:
            return None
        scenario = next((s for s in sheet.margin_scenarios() if s['key'] == 'M5'), None)
        if scenario and scenario['configured']:
            return scenario['grand_price']
        return None

    def sync_po_value_from_final_margin(self):
        """Pull the P.O Value from the final approved margin if available.
        Returns the source sheet when synced, else None."""
        value = self.po_value_from_final_margin()
        if value is None:
            return None
        sheet = self._final_margin_sheet()
        if self.po_value != value or self.source_sheet_id != getattr(sheet, 'id', None):
            self.po_value = value
            self.source_sheet = sheet
            self.approved_margin = 'M5'
            self.save(update_fields=['po_value', 'source_sheet', 'approved_margin', 'updated_at'])
        return sheet

    def recompute_dates(self):
        """Re-derive every milestone's auto dates (approval, submission,
        payment-receive) from its manual Work-Cert-Prep date + the standard
        gaps and the project's estimated start. Manual dates are untouched."""
        for m in self.milestones.all():
            m.apply_derived_dates()
            m.save(update_fields=[
                'work_cert_approval_date', 'invoice_submission_date',
                'payment_receive_date'])

    def timeline_periods(self):
        """Month columns spanning the project timeline, from the estimated
        start through the estimated end, stepped by the payment gap (≈30 days
        per month). Returns [{'key': 'YYYY-MM', 'label': 'Jan 2026'}, …].

        e.g. start Jan / end Jun, gap 30 → Jan Feb Mar Apr May Jun (6);
             gap 60 → Jan Mar May (3).
        """
        import calendar
        start, end = self.estimated_start_date, self.estimated_end_date
        if not start or not end or (start.year, start.month) > (end.year, end.month):
            return []
        step = max(1, round((self.payment_gap or 30) / 30))
        periods, y, m, guard = [], start.year, start.month, 0
        while (y, m) <= (end.year, end.month) and guard < 240:
            periods.append({'key': f'{y:04d}-{m:02d}',
                            'label': f'{calendar.month_abbr[m]} {y}'})
            m += step
            while m > 12:
                m -= 12
                y += 1
            guard += 1
        return periods


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

    # Work-Cert-Prep is entered manually; approval and invoice-submission are
    # derived from it + the standard gaps. Payment-receive is derived from the
    # project's estimated start + payment gap. Actual payment-receive is manual.
    work_cert_prep_date = models.DateField(null=True, blank=True)
    work_cert_approval_date = models.DateField(null=True, blank=True)
    invoice_submission_date = models.DateField(null=True, blank=True)
    payment_receive_date = models.DateField(null=True, blank=True)
    actual_payment_receive_date = models.DateField(null=True, blank=True)

    # Manually-scheduled payment amounts spread across the project timeline
    # months (see ProjectFinance.timeline_periods). Keyed 'YYYY-MM' -> amount.
    period_amounts = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.name

    def apply_derived_dates(self):
        """Fill the auto dates from the manual Work-Cert-Prep date + the
        project gaps, and the estimated-start-driven payment-receive date.
        Manual fields (prep, actual payment) are left as-is."""
        pf = self.project_finance
        if self.work_cert_prep_date:
            self.work_cert_approval_date = (
                self.work_cert_prep_date + timedelta(days=pf.cert_approval_gap or 0))
            self.invoice_submission_date = (
                self.work_cert_approval_date + timedelta(days=pf.invoice_gap or 0))
        else:
            self.work_cert_approval_date = None
            self.invoice_submission_date = None
        if pf.estimated_start_date:
            self.payment_receive_date = (
                pf.estimated_start_date + timedelta(days=pf.payment_gap or 0))
        else:
            self.payment_receive_date = None

    @property
    def amount(self):
        """Billed amount for this milestone = Submitted % × Project P.O Value."""
        pct = self.submitted_pct or Decimal('0')
        return (self.project_finance.po_value * pct / Decimal('100')).quantize(Decimal('0.01'))

    def period_amount(self, key):
        """The scheduled payment amount for a timeline month key, or ''."""
        val = (self.period_amounts or {}).get(key)
        return '' if val is None else val


class CashOutflowRow(models.Model):
    """One row of a project's cash-OUTFLOW schedule — money the company pays
    out to vendors, the mirror of the inbound payment milestones.

    Rows are generated from the final costing sheet's two commercial parts —
    A.1 Scope of Supply (costing line items) and A.2 Scope of Services
    (scope-of-work items) — then edited by finance: a procurement PO number,
    up to six staged vendor payments (date + %), and the amount / VAT / total
    in SAR.
    """

    PART_CHOICES = [
        ('A1', 'A.1 — Scope of Supply'),
        ('A2', 'A.2 — Scope of Services'),
    ]
    VAT_RATE = Decimal('0.15')  # standard KSA VAT

    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, related_name='cash_outflow_rows')
    part = models.CharField(max_length=2, choices=PART_CHOICES)
    order = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    po_number = models.CharField(
        max_length=100, blank=True,
        help_text='Procurement PO covering this outflow.')

    # Up to six staged vendor payments (date + % of the row amount).
    date_1 = models.DateField(null=True, blank=True)
    date_2 = models.DateField(null=True, blank=True)
    date_3 = models.DateField(null=True, blank=True)
    date_4 = models.DateField(null=True, blank=True)
    date_5 = models.DateField(null=True, blank=True)
    date_6 = models.DateField(null=True, blank=True)
    pct_1 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    pct_2 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    pct_3 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    pct_4 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    pct_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    pct_6 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal('0'),
        help_text='Amount in SAR (net, before VAT).')
    vat = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal('0'),
        help_text='VAT in SAR (15% of amount by default; editable).')
    total_amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal('0'),
        help_text='Total in SAR (amount + VAT).')
    remarks = models.TextField(blank=True)

    # Traces the costing line that seeded this row, so re-generation is
    # idempotent (an already-listed line is skipped). Blank = manual row.
    source_ref = models.CharField(max_length=40, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['part', 'order', 'id']

    def __str__(self):
        return f'{self.get_part_display()}: {self.description[:40]}'
