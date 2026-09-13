"""Manpower costing: what an employee costs us, and what we charge for them.

Replaces the `manpower` app. Two defects there drove the redesign, and both
were also present in the spreadsheets this module is built from:

1. `manpower.ManpowerLineItem` summed its cost fields with no x12, while its
   importer read the source spreadsheet's *monthly* figures straight into
   them. Every imported sheet therefore reported a yearly cost that was
   really a monthly one. Here the period is part of the field names and is
   recorded on the sheet, so the ambiguity cannot recur.

2. The source `REAL COST` sheet labels its steps "Overhead 10%", "Profit 25%"
   and "Monthly Inv 10.5 months" while the formulas behind them compute 5%,
   20% and a division by 11. Those numbers are parameters on CostBasis here,
   so what a rate was built from is recorded rather than described.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


# Cost components are entered per month, matching how payroll is actually
# run and how the source sheets are laid out. Named here once so the model,
# the importer and the export all agree on the order and the labels.
COST_COMPONENTS = (
    ('gross_salary', 'Gross Salary'),
    ('iqama_cost', 'Iqama Cost'),
    ('service_transfer_visa_fee', 'Services Transfer & Visa Fee'),
    ('gosi_cost', 'GOSI Cost'),
    ('vacation_pay', 'Vacation Pay'),
    ('exe_cost', 'EXE Cost'),
    ('eosb', 'EOSB'),
    ('air_ticket', 'Air Ticket'),
    ('insurance_cost', 'Insurance Cost'),
    ('project_allowance', 'Project Allowance'),
    ('ppe', 'PPE'),
    ('engineering_council_cost', 'Engineering Council Cost'),
    ('other_expenditures', 'Other Expenditures'),
)

COST_COMPONENT_FIELDS = tuple(name for name, _label in COST_COMPONENTS)


class Classification(models.TextChoices):
    """The nationality band that drives the rate spread.

    The Jubail sheet prices the same role at 1,187 (Eastern), 1,525 (Other
    Arabs) and 2,069 (Saudi) SAR/day, so this is a pricing dimension, not a
    description.
    """

    SAUDI = 'saudi', 'Saudi'
    EASTERN = 'eastern', 'Eastern'
    OTHER_ARABS = 'other_arabs', 'Other Arabs'
    OTHER = 'other', 'Other'


class CostBasis(models.Model):
    """The named parameter set a charge-out rate is built with.

    Exists because the source workbooks disagreed with themselves. Two of
    them divided a monthly cost by different hour counts - 240 (30 days x 8h)
    in one, 176 in the other - which moves an hourly rate by about 36% with
    nothing on the page to say why. Here the numbers are stored, shown, and
    attached to every figure derived from them.
    """

    name = models.CharField(max_length=120, unique=True)
    overhead_pct = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        verbose_name='Overhead %',
        help_text='Applied to salary + benefits.')
    profit_pct = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        verbose_name='Profit %',
        help_text='Applied to salary + benefits, not to salary + overhead.')
    billable_months = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('12'),
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Months a year actually invoiced. Below 12 recovers the '
                  'yearly cost over fewer invoices.')
    hours_per_month = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('176'),
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Billable hours in a month. 176 = 22 working days x 8h.')
    working_days_per_month = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('22'),
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Used for the daily cost. Working days, not calendar days.')
    is_default = models.BooleanField(
        default=False,
        help_text='The basis used when a sheet does not name one.')
    effective_from = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-is_default', 'name']
        verbose_name_plural = 'Cost bases'

    def __str__(self):
        return self.name

    @property
    def hours_per_day(self):
        """Derived rather than stored, so it can never disagree with the two
        fields it comes from."""
        if not self.working_days_per_month:
            return Decimal('0')
        return self.hours_per_month / self.working_days_per_month

    def save(self, *args, **kwargs):
        """Marking a basis default un-marks the others.

        Two rows claiming to be the default would make `get_default()` pick
        one arbitrarily, and every rate derived afterwards would depend on
        insertion order. Enforced here rather than by a constraint because
        the fix is to demote the old default, not to refuse the new one.
        """
        super().save(*args, **kwargs)
        if self.is_default:
            (type(self).objects
             .filter(is_default=True)
             .exclude(pk=self.pk)
             .update(is_default=False))

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_default=True).first()


class ManpowerCostSheet(models.Model):
    """A register of what a set of people cost, on one stated basis."""

    title = models.CharField(max_length=255)
    project_reference = models.CharField(max_length=255, blank=True)
    date = models.DateField()
    basis = models.ForeignKey(
        CostBasis, on_delete=models.PROTECT, related_name='sheets',
        null=True, blank=True,
        help_text='Falls back to the default basis when blank.')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='manpower_cost_sheets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['title']),
            models.Index(fields=['project_reference']),
        ]

    def __str__(self):
        return self.title

    @property
    def effective_basis(self):
        return self.basis or CostBasis.get_default()

    @property
    def monthly_total(self):
        return sum((line.monthly_cost for line in self.lines.all()),
                   Decimal('0'))

    @property
    def yearly_total(self):
        return self.monthly_total * 12

    @property
    def incomplete_lines(self):
        """Lines with no gross salary.

        Both source files reported a total that silently excluded salary -
        one because the column had been emptied under a live SUM, the other
        because the importer zeroed anything it could not map. A total that
        quietly leaves people out is the specific failure this module is
        meant to make impossible, so the lines are surfaced rather than
        summed as zero.
        """
        return [line for line in self.lines.all() if not line.gross_salary]


class ManpowerCostLine(models.Model):
    """One person (or one unfilled position) and what they cost per month."""

    sheet = models.ForeignKey(
        ManpowerCostSheet, on_delete=models.CASCADE, related_name='lines')
    order = models.PositiveIntegerField(default=0)

    employee = models.ForeignKey(
        'hr.Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='manpower_cost_lines',
        help_text='Optional: a bid can price a role before anyone is hired.')
    employee_name = models.CharField(
        max_length=255, blank=True,
        help_text='Snapshotted at entry. Renaming an employee later must not '
                  'rewrite what a quoted sheet says it was priced on.')
    position = models.ForeignKey(
        'costing.ResourceCatalogueItem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='manpower_cost_lines',
        help_text='The role, from the shared A.4 resource catalogue.')
    designation = models.CharField(max_length=255, blank=True)
    department = models.CharField(max_length=255, blank=True)
    classification = models.CharField(
        max_length=20, choices=Classification.choices,
        blank=True, default='')
    location_project = models.CharField(max_length=255, blank=True)
    doj = models.DateField(null=True, blank=True, verbose_name='Date of Joining')
    demobilization_date = models.DateField(null=True, blank=True)

    gross_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Gross Salary (monthly)')
    iqama_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Iqama Cost (monthly)')
    service_transfer_visa_fee = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Services Transfer & Visa Fee (monthly)')
    gosi_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='GOSI Cost (monthly)')
    vacation_pay = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Vacation Pay (monthly)')
    exe_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='EXE Cost (monthly)')
    eosb = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='EOSB (monthly)')
    air_ticket = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Air Ticket (monthly)')
    insurance_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Insurance Cost (monthly)')
    project_allowance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Project Allowance (monthly)')
    ppe = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='PPE (monthly)')
    engineering_council_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Engineering Council Cost (monthly)')
    other_expenditures = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Other Expenditures (monthly)')

    class Meta:
        ordering = ['order', 'pk']

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        """The linked employee's name wins for display, but employee_name is
        what the sheet was quoted on and is never overwritten."""
        if self.employee_id and self.employee:
            return self.employee.full_name
        return self.employee_name or self.designation or f'Line {self.pk}'

    @property
    def monthly_cost(self):
        return sum(
            (getattr(self, field) or Decimal('0')
             for field in COST_COMPONENT_FIELDS),
            Decimal('0'))

    @property
    def yearly_cost(self):
        return self.monthly_cost * 12

    def daily_cost(self, basis=None):
        basis = basis or self.sheet.effective_basis
        if not basis or not basis.working_days_per_month:
            return Decimal('0')
        return self.monthly_cost / basis.working_days_per_month

    def hourly_cost(self, basis=None):
        basis = basis or self.sheet.effective_basis
        if not basis or not basis.hours_per_month:
            return Decimal('0')
        return self.monthly_cost / basis.hours_per_month


class ChargeRate(models.Model):
    """What we charge for a role, derived from what it costs.

    One row per (position, classification, basis). The derived value is
    recomputed on read rather than stored, so a change to the basis cannot
    leave a stale rate behind; `manual_rate` is the deliberate exception for
    a negotiated or client-imposed price.
    """

    position = models.ForeignKey(
        'costing.ResourceCatalogueItem', on_delete=models.CASCADE,
        related_name='charge_rates')
    classification = models.CharField(
        max_length=20, choices=Classification.choices,
        blank=True, default='')
    basis = models.ForeignKey(
        CostBasis, on_delete=models.PROTECT, related_name='charge_rates')
    monthly_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Fully burdened monthly cost this rate is built from.')
    manual_rate = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        verbose_name='Manual hourly rate',
        help_text='Overrides the derived rate. Leave blank to follow cost.')
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position__order', 'position__name', 'classification']
        constraints = [
            models.UniqueConstraint(
                fields=['position', 'classification', 'basis'],
                name='unique_charge_rate_per_position_class_basis'),
        ]

    def __str__(self):
        label = self.position.name if self.position_id else 'Rate'
        if self.classification:
            label = f'{label} ({self.get_classification_display()})'
        return label

    @property
    def is_overridden(self):
        return self.manual_rate is not None
