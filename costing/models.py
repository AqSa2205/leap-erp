from django.db import models
from django.conf import settings
from decimal import Decimal


class ExchangeRate(models.Model):
    currency_code = models.CharField(max_length=10, unique=True)
    currency_name = models.CharField(max_length=50)
    rate_to_usd = models.DecimalField(max_digits=12, decimal_places=6, default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['currency_code']

    def __str__(self):
        return f"{self.currency_code} ({self.rate_to_usd})"


class CostingSheet(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('final', 'Final'),
    ]

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='costing_sheets',
    )
    title = models.CharField(max_length=255)
    customer_reference = models.CharField(max_length=255, blank=True)
    margin = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.4000'))
    ddp_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.0000'))
    discount_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.0000'))
    output_currency = models.CharField(max_length=10, default='SAR')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='costing_sheets',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    @property
    def grand_total(self):
        total = Decimal('0')
        for section in self.sections.all():
            total += section.subtotal
        return total

    @property
    def total_cost(self):
        total = Decimal('0')
        for section in self.sections.all():
            total += section.total_cost
        return total


class CostingSection(models.Model):
    costing_sheet = models.ForeignKey(
        CostingSheet,
        on_delete=models.CASCADE,
        related_name='sections',
    )
    section_number = models.CharField(max_length=20)
    title = models.CharField(max_length=255)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'section_number']

    def __str__(self):
        return f"{self.section_number} - {self.title}"

    @property
    def subtotal(self):
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.final_total_price
        return total

    @property
    def total_cost(self):
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.cost_usd * item.quantity
        return total


class CostingLineItem(models.Model):
    UNIT_CHOICES = [
        ('EA', 'EA'),
        ('LOT', 'LOT'),
        ('Mtr', 'Mtr'),
        ('Roll', 'Roll'),
        ('Set', 'Set'),
        ('Pair', 'Pair'),
        ('Box', 'Box'),
        ('Pkt', 'Pkt'),
    ]

    section = models.ForeignKey(
        CostingSection,
        on_delete=models.CASCADE,
        related_name='line_items',
    )
    item_number = models.CharField(max_length=20)
    description = models.CharField(max_length=500)
    make = models.CharField(max_length=100, blank=True)
    model_number = models.CharField(max_length=100, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default='EA')
    vendor_name = models.CharField(max_length=255, blank=True)
    system = models.CharField(max_length=100, blank=True)
    supplier_currency = models.CharField(max_length=10, default='SAR')
    base_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'item_number']

    def __str__(self):
        return f"{self.item_number} - {self.description[:50]}"

    @property
    def sheet(self):
        return self.section.costing_sheet

    @property
    def base_total(self):
        return self.base_price * self.quantity

    @property
    def discounted_price(self):
        return self.base_price * (1 - self.sheet.discount_rate)

    @property
    def exchange_rate(self):
        if self.supplier_currency == 'USD':
            return Decimal('1')
        try:
            return ExchangeRate.objects.get(
                currency_code=self.supplier_currency
            ).rate_to_usd
        except ExchangeRate.DoesNotExist:
            return Decimal('1')

    @property
    def discounted_total(self):
        return self.discounted_price * self.quantity

    @property
    def cost_usd(self):
        rate = self.exchange_rate
        if rate == 0:
            return Decimal('0')
        return self.discounted_price / rate

    @property
    def cost_usd_total(self):
        return self.cost_usd * self.quantity

    @property
    def quoted_usd(self):
        margin = self.sheet.margin
        if margin >= 1:
            return self.cost_usd
        return self.cost_usd / (1 - margin)

    @property
    def output_exchange_rate(self):
        output_cur = self.sheet.output_currency
        if output_cur == 'USD':
            return Decimal('1')
        try:
            return ExchangeRate.objects.get(
                currency_code=output_cur
            ).rate_to_usd
        except ExchangeRate.DoesNotExist:
            return Decimal('1')

    @property
    def quoted_usd_total(self):
        return self.quoted_usd * self.quantity

    @property
    def quoted_local(self):
        return self.quoted_usd * self.output_exchange_rate

    @property
    def ddp_amount(self):
        return self.quoted_local * self.sheet.ddp_rate

    @property
    def final_unit_price(self):
        return self.quoted_local + self.ddp_amount

    @property
    def final_total_price(self):
        return self.final_unit_price * self.quantity
