from datetime import date

from django.db import models
from django.conf import settings


class ManpowerSheet(models.Model):
    title = models.CharField(max_length=255, verbose_name='Title')
    project_reference = models.CharField(
        max_length=255, blank=True, verbose_name='Project Reference'
    )
    date = models.DateField(verbose_name='Date')
    notes = models.TextField(blank=True, verbose_name='Notes')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='manpower_sheets',
    )
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
    def total_cost(self):
        return sum(item.total_yearly_cost for item in self.line_items.all())


class ManpowerLineItem(models.Model):
    sheet = models.ForeignKey(
        ManpowerSheet,
        on_delete=models.CASCADE,
        related_name='line_items',
    )
    order = models.PositiveIntegerField(default=0)

    # ── Employee Information ──
    employee_name = models.CharField(max_length=255, verbose_name='Employee Name')
    department = models.CharField(max_length=255, blank=True, verbose_name='Department')
    classification = models.CharField(
        max_length=255, blank=True, verbose_name='Classification of Staff'
    )
    location_project = models.CharField(
        max_length=255, blank=True, verbose_name='Location/Project'
    )
    designation = models.CharField(max_length=255, blank=True, verbose_name='Designation')
    doj = models.DateField(null=True, blank=True, verbose_name='Date of Joining')
    demobilization_date = models.DateField(
        null=True, blank=True, verbose_name='Demobilization Date'
    )
    date_of_birth = models.DateField(null=True, blank=True, verbose_name='Date of Birth')

    # ── Costing Breakdown (yearly amounts) ──
    budget_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Budget Cost'
    )
    gross_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Gross Salary'
    )
    iqama_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Iqama Cost'
    )
    service_transfer_visa_fee = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Services Transfer & Visa Fee'
    )
    gosi_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='GOSI Cost'
    )
    vacation_pay = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Vacation Pay'
    )
    exe_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='EXE Cost'
    )
    eosb = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='EOSB'
    )
    air_ticket = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Air Ticket'
    )
    insurance_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Insurance Cost'
    )
    project_allowance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Project Allowance'
    )
    ppe = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='PPE'
    )
    engineering_council_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        verbose_name='Engineering Council Cost'
    )
    other_expenditures = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name='Other Expenditures'
    )

    class Meta:
        ordering = ['order', 'pk']

    def __str__(self):
        return self.employee_name

    @property
    def employee_age(self):
        if not self.date_of_birth:
            return None
        today = date.today()
        return (
            today.year - self.date_of_birth.year
            - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        )

    @property
    def total_yearly_cost(self):
        return (
            self.gross_salary + self.iqama_cost + self.service_transfer_visa_fee
            + self.gosi_cost + self.vacation_pay + self.exe_cost + self.eosb
            + self.air_ticket + self.insurance_cost + self.project_allowance
            + self.ppe + self.engineering_council_cost + self.other_expenditures
        )

    @property
    def monthly_cost(self):
        yearly = self.total_yearly_cost
        if yearly:
            return yearly / 12
        return 0

    @property
    def daily_cost(self):
        yearly = self.total_yearly_cost
        if yearly:
            return yearly / 365
        return 0

    @property
    def hourly_cost(self):
        yearly = self.total_yearly_cost
        if yearly:
            return yearly / 2920  # 365 days * 8 hours
        return 0
