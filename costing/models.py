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
    # Sheet-level default parameters (rates as whole numbers, e.g., 40 = 40%)
    margin = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('40'))
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    shipping_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    customs_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    finances_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    installation_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
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
        return total.quantize(Decimal('0.01'))

    @property
    def total_cost(self):
        total = Decimal('0')
        for section in self.sections.all():
            total += section.total_cost
        return total.quantize(Decimal('0.01'))

    @property
    def total_base_cost(self):
        """Sum of all base_unit_cost * quantity"""
        total = Decimal('0')
        for section in self.sections.all():
            for item in section.line_items.all():
                total += item.base_unit_cost * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def total_discount(self):
        """Sum of all discount_amount * quantity"""
        total = Decimal('0')
        for section in self.sections.all():
            for item in section.line_items.all():
                total += item.discount_amount * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def total_margin_amount(self):
        """Total margin amount (base_total_price - total_cost)"""
        total = Decimal('0')
        for section in self.sections.all():
            for item in section.line_items.all():
                total += item.base_total_price - item.total_cost
        return total.quantize(Decimal('0.01'))

    @property
    def total_shipping_amount(self):
        """Total shipping amount (based on cost after discount)"""
        total = Decimal('0')
        for section in self.sections.all():
            for item in section.line_items.all():
                total += item.unit_cost_sar * item.effective_shipping_pct * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def total_customs_amount(self):
        """Total customs amount (based on cost after discount)"""
        total = Decimal('0')
        for section in self.sections.all():
            for item in section.line_items.all():
                total += item.unit_cost_sar * item.effective_customs_pct * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def total_finances_amount(self):
        """Total finances amount (based on cost after discount)"""
        total = Decimal('0')
        for section in self.sections.all():
            for item in section.line_items.all():
                total += item.unit_cost_sar * item.effective_finances_pct * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def total_installation_amount(self):
        """Total installation amount (based on cost after discount)"""
        total = Decimal('0')
        for section in self.sections.all():
            for item in section.line_items.all():
                total += item.unit_cost_sar * item.effective_installation_pct * item.quantity
        return total.quantize(Decimal('0.01'))


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
        return total.quantize(Decimal('0.01'))

    @property
    def total_cost(self):
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.total_cost
        return total.quantize(Decimal('0.01'))

    @property
    def subtotal_base_unit_cost(self):
        """Sum of base_unit_cost * quantity"""
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.base_unit_cost * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def subtotal_discount(self):
        """Sum of discount_amount * quantity"""
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.discount_amount * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def subtotal_unit_cost(self):
        """Sum of unit_cost * quantity (after discount)"""
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.unit_cost * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def subtotal_base_unit_price(self):
        """Sum of base_unit_price * quantity"""
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.base_unit_price * item.quantity
        return total.quantize(Decimal('0.01'))

    @property
    def subtotal_base_total_price(self):
        """Sum of base_total_price"""
        total = Decimal('0')
        for item in self.line_items.all():
            total += item.base_total_price
        return total.quantize(Decimal('0.01'))


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
    # Currency for cost fields
    supplier_currency = models.CharField(max_length=10, default='SAR')
    # Cost breakdown fields
    base_unit_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, help_text='Raw cost from supplier')
    # Percentage fields (as whole numbers, e.g., 3 = 3%)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='Discount %. If blank, uses sheet rate.')
    shipping_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='Shipping %. If blank, uses sheet rate.')
    customs_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='Customs %. If blank, uses sheet rate.')
    finances_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='Finances %. If blank, uses sheet rate.')
    installation_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='Installation %. If blank, uses sheet rate.')
    margin = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='Item-specific margin. If blank, uses sheet margin.')
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'item_number']

    def __str__(self):
        return f"{self.item_number} - {self.description[:50]}"

    @property
    def sheet(self):
        return self.section.costing_sheet

    @property
    def effective_margin(self):
        """Use item-specific margin if set, otherwise fall back to sheet margin. Divide by 100 for calculation."""
        if self.margin is not None:
            return self.margin / Decimal('100')
        return self.sheet.margin / Decimal('100')

    @property
    def effective_discount_pct(self):
        """Use item-specific discount % if set, otherwise fall back to sheet rate. Divide by 100 for calculation."""
        if self.discount_pct is not None:
            return self.discount_pct / Decimal('100')
        return self.sheet.discount_rate / Decimal('100')

    @property
    def effective_shipping_pct(self):
        """Use item-specific shipping % if set, otherwise fall back to sheet rate. Divide by 100 for calculation."""
        if self.shipping_pct is not None:
            return self.shipping_pct / Decimal('100')
        return self.sheet.shipping_rate / Decimal('100')

    @property
    def effective_customs_pct(self):
        """Use item-specific customs % if set, otherwise fall back to sheet rate. Divide by 100 for calculation."""
        if self.customs_pct is not None:
            return self.customs_pct / Decimal('100')
        return self.sheet.customs_rate / Decimal('100')

    @property
    def effective_finances_pct(self):
        """Use item-specific finances % if set, otherwise fall back to sheet rate. Divide by 100 for calculation."""
        if self.finances_pct is not None:
            return self.finances_pct / Decimal('100')
        return self.sheet.finances_rate / Decimal('100')

    @property
    def effective_installation_pct(self):
        """Use item-specific installation % if set, otherwise fall back to sheet rate. Divide by 100 for calculation."""
        if self.installation_pct is not None:
            return self.installation_pct / Decimal('100')
        return self.sheet.installation_rate / Decimal('100')

    @property
    def discount_amount(self):
        """Calculate discount amount from base_unit_cost * discount_pct"""
        result = self.base_unit_cost * self.effective_discount_pct
        return result.quantize(Decimal('0.01'))

    @property
    def unit_cost(self):
        """Base Unit Cost - Discount Amount"""
        result = self.base_unit_cost - self.discount_amount
        return result.quantize(Decimal('0.01'))

    @property
    def total_cost(self):
        """Unit Cost * Quantity"""
        result = self.unit_cost * self.quantity
        return result.quantize(Decimal('0.01'))

    @property
    def exchange_rate_to_sar(self):
        """Get exchange rate from supplier currency to SAR"""
        if self.supplier_currency == 'SAR':
            return Decimal('1')
        try:
            supplier_rate = ExchangeRate.objects.get(currency_code=self.supplier_currency).rate_to_usd
            sar_rate = ExchangeRate.objects.get(currency_code='SAR').rate_to_usd
            # Convert: supplier -> USD -> SAR
            return (sar_rate / supplier_rate).quantize(Decimal('0.000001'))
        except ExchangeRate.DoesNotExist:
            return Decimal('1')

    @property
    def unit_cost_sar(self):
        """Unit Cost converted to SAR"""
        result = self.unit_cost * self.exchange_rate_to_sar
        return result.quantize(Decimal('0.01'))

    @property
    def base_unit_price(self):
        """Selling Price = Cost / (1 - Margin), where selling price is 100%"""
        margin = self.effective_margin
        if margin >= 1:
            return self.unit_cost_sar
        result = self.unit_cost_sar / (1 - margin)
        return result.quantize(Decimal('0.01'))

    @property
    def base_total_price(self):
        """Base Unit Price * Quantity"""
        result = self.base_unit_price * self.quantity
        return result.quantize(Decimal('0.01'))

    @property
    def total_addon_pct(self):
        """Sum of shipping + customs + finances + installation percentages"""
        return (self.effective_shipping_pct + self.effective_customs_pct +
                self.effective_finances_pct + self.effective_installation_pct)

    @property
    def final_unit_price(self):
        """Base Unit Price + (Unit Cost SAR * total addon percentages)"""
        result = self.base_unit_price + (self.unit_cost_sar * self.total_addon_pct)
        return result.quantize(Decimal('0.01'))

    @property
    def final_total_price(self):
        """Final Unit Price * Quantity"""
        result = self.final_unit_price * self.quantity
        return result.quantize(Decimal('0.01'))

    # Aliases for template compatibility
    @property
    def price_in_sar(self):
        """Alias for final_unit_price"""
        return self.final_unit_price

    # Display properties for percentage fields (show as whole numbers)
    @property
    def display_margin(self):
        """Return margin as whole number for display"""
        if self.margin is not None:
            return self.margin
        return self.sheet.margin

    @property
    def display_discount_pct(self):
        """Return discount % as whole number for display"""
        if self.discount_pct is not None:
            return self.discount_pct
        return self.sheet.discount_rate

    @property
    def display_shipping_pct(self):
        """Return shipping % as whole number for display"""
        if self.shipping_pct is not None:
            return self.shipping_pct
        return self.sheet.shipping_rate

    @property
    def display_customs_pct(self):
        """Return customs % as whole number for display"""
        if self.customs_pct is not None:
            return self.customs_pct
        return self.sheet.customs_rate

    @property
    def display_finances_pct(self):
        """Return finances % as whole number for display"""
        if self.finances_pct is not None:
            return self.finances_pct
        return self.sheet.finances_rate

    @property
    def display_installation_pct(self):
        """Return installation % as whole number for display"""
        if self.installation_pct is not None:
            return self.installation_pct
        return self.sheet.installation_rate
