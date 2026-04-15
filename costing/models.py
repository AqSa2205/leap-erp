from django.db import models
from django.conf import settings
from decimal import Decimal


class TermsTemplate(models.Model):
    CATEGORY_CHOICES = [
        ('terms_and_conditions', 'Terms & Conditions'),
        ('exclusions', 'Exclusions'),
        ('payment_terms', 'Payment Terms'),
        ('conclusion', 'Conclusion'),
    ]
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    content = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='terms_templates',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return f"{self.get_category_display()} - {self.name}"


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
    # PDF header fields
    customer_name = models.CharField(max_length=255, blank=True)
    end_user = models.CharField(max_length=255, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    telephone = models.CharField(max_length=100, blank=True)
    fax = models.CharField(max_length=100, blank=True)
    # PDF content sections
    terms_and_conditions = models.TextField(blank=True)
    exclusions = models.TextField(blank=True)
    payment_terms = models.TextField(blank=True)
    conclusion = models.TextField(blank=True)
    selected_terms = models.ManyToManyField('TermsTemplate', blank=True, related_name='costing_sheets')

    # Scope of Work (A.2 section in the commercial offer)
    scope_of_work_total = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0'),
        verbose_name="Scope of Work Total",
        help_text="Total value for A.2 Scope of Work"
    )

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    include_optional_in_total = models.BooleanField(
        default=False,
        help_text='If checked, optional sections are included in the grand total. Otherwise they are shown but not counted.'
    )
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

    def _compute_totals(self):
        """Compute all sheet totals in a single pass. Results are cached on the instance.

        Optional sections (is_optional=True) are tracked separately and only
        added to the main totals if include_optional_in_total is enabled on
        the sheet. Their subtotals are still exposed via optional_subtotal.
        """
        if hasattr(self, '_totals'):
            return self._totals
        t = {
            'grand_total': Decimal('0'),
            'total_cost': Decimal('0'),
            'total_base_cost': Decimal('0'),
            'total_discount': Decimal('0'),
            'total_margin_amount': Decimal('0'),
            'total_shipping_amount': Decimal('0'),
            'total_customs_amount': Decimal('0'),
            'total_finances_amount': Decimal('0'),
            'total_installation_amount': Decimal('0'),
            'optional_subtotal': Decimal('0'),
        }
        include_opt = self.include_optional_in_total
        for section in self.sections.all():
            section_is_optional = section.is_optional
            for item in section.line_items.all():
                qty = item.quantity
                if section_is_optional:
                    t['optional_subtotal'] += item.final_total_price
                    if not include_opt:
                        continue
                t['grand_total'] += item.final_total_price
                t['total_cost'] += item.total_cost
                t['total_base_cost'] += item.base_unit_cost * qty
                t['total_discount'] += item.discount_amount * qty
                t['total_margin_amount'] += item.base_total_price - item.total_cost
                ucs = item.unit_cost_sar
                t['total_shipping_amount'] += ucs * item.effective_shipping_pct * qty
                t['total_customs_amount'] += ucs * item.effective_customs_pct * qty
                t['total_finances_amount'] += ucs * item.effective_finances_pct * qty
                t['total_installation_amount'] += ucs * item.effective_installation_pct * qty
        for key in t:
            t[key] = t[key].quantize(Decimal('0.01'))
        self._totals = t
        return t

    @property
    def optional_subtotal(self):
        return self._compute_totals()['optional_subtotal']

    @property
    def grand_total(self):
        return self._compute_totals()['grand_total']

    @property
    def total_cost(self):
        return self._compute_totals()['total_cost']

    @property
    def total_base_cost(self):
        return self._compute_totals()['total_base_cost']

    @property
    def total_discount(self):
        return self._compute_totals()['total_discount']

    @property
    def total_margin_amount(self):
        return self._compute_totals()['total_margin_amount']

    @property
    def total_shipping_amount(self):
        return self._compute_totals()['total_shipping_amount']

    @property
    def total_customs_amount(self):
        return self._compute_totals()['total_customs_amount']

    @property
    def total_finances_amount(self):
        return self._compute_totals()['total_finances_amount']

    @property
    def total_installation_amount(self):
        return self._compute_totals()['total_installation_amount']


class ScopeOfWorkItem(models.Model):
    """Line item under A.2 SCOPE OF WORK in the commercial offer."""
    costing_sheet = models.ForeignKey(
        'CostingSheet', on_delete=models.CASCADE, related_name='scope_of_work_items'
    )
    serial_number = models.PositiveIntegerField(default=1, verbose_name="S.No.")
    description = models.TextField(verbose_name="Description")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1'))
    uom = models.CharField(max_length=50, default='LOT', verbose_name="UOM")
    total_price = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'), verbose_name="Total Price")
    price_text = models.CharField(max_length=100, blank=True, verbose_name="Price Text",
        help_text='If set, shows this text instead of the number (e.g. "Included", "TBD")')
    order = models.PositiveIntegerField(default=0)

    @property
    def display_price(self):
        """Show price_text if set, otherwise the numeric total_price."""
        if self.price_text:
            return self.price_text
        return f'{self.total_price:,.2f}'

    class Meta:
        ordering = ['order', 'serial_number']

    def __str__(self):
        return f"#{self.serial_number} - {self.description[:50]}"


class CostingSection(models.Model):
    costing_sheet = models.ForeignKey(
        CostingSheet,
        on_delete=models.CASCADE,
        related_name='sections',
    )
    section_number = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=255)
    order = models.IntegerField(default=0)
    is_optional = models.BooleanField(
        default=False,
        help_text='Mark this section as optional. Its subtotal is shown but excluded from the grand total unless the sheet setting is enabled.'
    )

    # Section-level rate overrides (optional — blank falls back to sheet rates)
    margin = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Section margin %. Blank = use sheet margin.')
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Section discount %. Blank = use sheet rate.')
    shipping_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Section shipping %. Blank = use sheet rate.')
    customs_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Section customs %. Blank = use sheet rate.')
    finances_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Section finances %. Blank = use sheet rate.')
    installation_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Section installation %. Blank = use sheet rate.')

    class Meta:
        ordering = ['order', 'section_number']

    def __str__(self):
        if self.section_number:
            return f"{self.section_number} - {self.title}"
        return self.title

    def _compute_subtotals(self):
        """Compute all section subtotals in a single pass. Results are cached on the instance."""
        if hasattr(self, '_subtotals'):
            return self._subtotals
        t = {
            'subtotal': Decimal('0'),
            'total_cost': Decimal('0'),
            'base_unit_cost': Decimal('0'),
            'discount': Decimal('0'),
            'unit_cost': Decimal('0'),
            'base_unit_price': Decimal('0'),
            'base_total_price': Decimal('0'),
        }
        for item in self.line_items.all():
            qty = item.quantity
            t['subtotal'] += item.final_total_price
            t['total_cost'] += item.total_cost
            t['base_unit_cost'] += item.base_unit_cost * qty
            t['discount'] += item.discount_amount * qty
            t['unit_cost'] += item.unit_cost * qty
            t['base_unit_price'] += item.base_unit_price * qty
            t['base_total_price'] += item.base_total_price
        for key in t:
            t[key] = t[key].quantize(Decimal('0.01'))
        self._subtotals = t
        return t

    @property
    def subtotal(self):
        return self._compute_subtotals()['subtotal']

    @property
    def total_cost(self):
        return self._compute_subtotals()['total_cost']

    @property
    def subtotal_base_unit_cost(self):
        return self._compute_subtotals()['base_unit_cost']

    @property
    def subtotal_discount(self):
        return self._compute_subtotals()['discount']

    @property
    def subtotal_unit_cost(self):
        return self._compute_subtotals()['unit_cost']

    @property
    def subtotal_base_unit_price(self):
        return self._compute_subtotals()['base_unit_price']

    @property
    def subtotal_base_total_price(self):
        return self._compute_subtotals()['base_total_price']


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rates_cache = None
        self._sheet_cache = None
        self._computed = {}

    def __str__(self):
        return f"{self.item_number} - {self.description[:50]}"

    def set_exchange_rates_cache(self, rates_dict):
        """Set pre-loaded exchange rates to avoid per-item DB queries."""
        self._rates_cache = rates_dict

    def set_sheet_cache(self, sheet):
        """Set parent sheet reference to avoid FK traversal."""
        self._sheet_cache = sheet

    @property
    def sheet(self):
        if self._sheet_cache is not None:
            return self._sheet_cache
        return self.section.costing_sheet

    # Pricing math safety bounds. Margins are stored as whole-number percents
    # (e.g. 40 = 40%) and converted to decimals (0.40) for the price formula
    # selling_price = cost / (1 - margin). Without bounds, a margin of 99.99
    # produces a 10000x markup; a negative margin produces an under-priced
    # selling price. Clamp to [0%, 99%] so the math is always sane.
    MIN_MARGIN_DECIMAL = Decimal('0')
    MAX_MARGIN_DECIMAL = Decimal('0.99')

    def _resolve_rate(self, item_field, section_field, sheet_field):
        """Resolve a rate using the hierarchy: item → section → sheet.
        Returns the raw value (whole-number %) before dividing by 100."""
        # 1. Item-specific override
        item_val = getattr(self, item_field)
        if item_val is not None:
            return item_val
        # 2. Section-level override
        section_val = getattr(self.section, section_field, None)
        if section_val is not None:
            return section_val
        # 3. Sheet-level default
        return getattr(self.sheet, sheet_field)

    @property
    def effective_margin(self):
        """Item → section → sheet margin. Returned as a decimal (40% -> 0.40),
        clamped to [0, 0.99] to keep the selling-price formula stable."""
        raw = self._resolve_rate('margin', 'margin', 'margin')
        if raw is None:
            return Decimal('0')
        margin_decimal = Decimal(raw) / Decimal('100')
        if margin_decimal < self.MIN_MARGIN_DECIMAL:
            return self.MIN_MARGIN_DECIMAL
        if margin_decimal > self.MAX_MARGIN_DECIMAL:
            return self.MAX_MARGIN_DECIMAL
        return margin_decimal

    @property
    def effective_discount_pct(self):
        """Item → section → sheet discount rate. Divide by 100."""
        raw = self._resolve_rate('discount_pct', 'discount_rate', 'discount_rate')
        return (Decimal(raw) / Decimal('100')) if raw else Decimal('0')

    @property
    def effective_shipping_pct(self):
        """Item → section → sheet shipping rate. Divide by 100."""
        raw = self._resolve_rate('shipping_pct', 'shipping_rate', 'shipping_rate')
        return (Decimal(raw) / Decimal('100')) if raw else Decimal('0')

    @property
    def effective_customs_pct(self):
        """Item → section → sheet customs rate. Divide by 100."""
        raw = self._resolve_rate('customs_pct', 'customs_rate', 'customs_rate')
        return (Decimal(raw) / Decimal('100')) if raw else Decimal('0')

    @property
    def effective_finances_pct(self):
        """Item → section → sheet finances rate. Divide by 100."""
        raw = self._resolve_rate('finances_pct', 'finances_rate', 'finances_rate')
        return (Decimal(raw) / Decimal('100')) if raw else Decimal('0')

    @property
    def effective_installation_pct(self):
        """Item → section → sheet installation rate. Divide by 100."""
        raw = self._resolve_rate('installation_pct', 'installation_rate', 'installation_rate')
        return self.sheet.installation_rate / Decimal('100')

    @property
    def discount_amount(self):
        """Calculate discount amount from base_unit_cost * discount_pct"""
        if 'discount_amount' in self._computed:
            return self._computed['discount_amount']
        result = (self.base_unit_cost * self.effective_discount_pct).quantize(Decimal('0.01'))
        self._computed['discount_amount'] = result
        return result

    @property
    def unit_cost(self):
        """Base Unit Cost - Discount Amount"""
        if 'unit_cost' in self._computed:
            return self._computed['unit_cost']
        result = (self.base_unit_cost - self.discount_amount).quantize(Decimal('0.01'))
        self._computed['unit_cost'] = result
        return result

    @property
    def total_cost(self):
        """Unit Cost * Quantity"""
        if 'total_cost' in self._computed:
            return self._computed['total_cost']
        result = (self.unit_cost * self.quantity).quantize(Decimal('0.01'))
        self._computed['total_cost'] = result
        return result

    @property
    def exchange_rate_to_sar(self):
        """Get exchange rate from supplier currency to SAR"""
        if 'exchange_rate_to_sar' in self._computed:
            return self._computed['exchange_rate_to_sar']
        if self.supplier_currency == 'SAR':
            result = Decimal('1')
        elif self._rates_cache is not None:
            supplier_rate = self._rates_cache.get(self.supplier_currency)
            sar_rate = self._rates_cache.get('SAR')
            if supplier_rate and sar_rate:
                result = (sar_rate / supplier_rate).quantize(Decimal('0.000001'))
            else:
                result = Decimal('1')
        else:
            try:
                supplier_rate = ExchangeRate.objects.get(currency_code=self.supplier_currency).rate_to_usd
                sar_rate = ExchangeRate.objects.get(currency_code='SAR').rate_to_usd
                # Convert: supplier -> USD -> SAR
                result = (sar_rate / supplier_rate).quantize(Decimal('0.000001'))
            except ExchangeRate.DoesNotExist:
                result = Decimal('1')
        self._computed['exchange_rate_to_sar'] = result
        return result

    @property
    def unit_cost_sar(self):
        """Unit Cost converted to SAR"""
        if 'unit_cost_sar' in self._computed:
            return self._computed['unit_cost_sar']
        result = (self.unit_cost * self.exchange_rate_to_sar).quantize(Decimal('0.01'))
        self._computed['unit_cost_sar'] = result
        return result

    @property
    def base_unit_price(self):
        """Selling Price = Cost / (1 - Margin), where selling price is 100%.

        effective_margin is guaranteed to be in [0, 0.99] so (1 - margin) is
        always in [0.01, 1.0] - never zero, never negative, never explosive.
        """
        if 'base_unit_price' in self._computed:
            return self._computed['base_unit_price']
        margin = self.effective_margin
        result = (self.unit_cost_sar / (Decimal('1') - margin)).quantize(Decimal('0.01'))
        self._computed['base_unit_price'] = result
        return result

    @property
    def base_total_price(self):
        """Base Unit Price * Quantity"""
        if 'base_total_price' in self._computed:
            return self._computed['base_total_price']
        result = (self.base_unit_price * self.quantity).quantize(Decimal('0.01'))
        self._computed['base_total_price'] = result
        return result

    @property
    def total_addon_pct(self):
        """Sum of shipping + customs + finances + installation percentages"""
        return (self.effective_shipping_pct + self.effective_customs_pct +
                self.effective_finances_pct + self.effective_installation_pct)

    @property
    def final_unit_price(self):
        """Base Unit Price + (Unit Cost SAR * total addon percentages)"""
        if 'final_unit_price' in self._computed:
            return self._computed['final_unit_price']
        result = (self.base_unit_price + (self.unit_cost_sar * self.total_addon_pct)).quantize(Decimal('0.01'))
        self._computed['final_unit_price'] = result
        return result

    @property
    def final_total_price(self):
        """Final Unit Price * Quantity"""
        if 'final_total_price' in self._computed:
            return self._computed['final_total_price']
        result = (self.final_unit_price * self.quantity).quantize(Decimal('0.01'))
        self._computed['final_total_price'] = result
        return result

    # Aliases for template compatibility
    @property
    def price_in_sar(self):
        """Alias for final_unit_price"""
        return self.final_unit_price

    # Display properties for percentage fields (show as whole numbers)
    @property
    def display_margin(self):
        """Return margin as whole number for display (item → section → sheet)."""
        if self.margin is not None:
            return self.margin
        section_margin = getattr(self.section, 'margin', None)
        if section_margin is not None:
            return section_margin
        return self.sheet.margin

    def _display_rate(self, item_field, section_field, sheet_field):
        """Return rate as whole number for display (item → section → sheet)."""
        item_val = getattr(self, item_field)
        if item_val is not None:
            return item_val
        section_val = getattr(self.section, section_field, None)
        if section_val is not None:
            return section_val
        return getattr(self.sheet, sheet_field)

    @property
    def display_discount_pct(self):
        return self._display_rate('discount_pct', 'discount_rate', 'discount_rate')

    @property
    def display_shipping_pct(self):
        return self._display_rate('shipping_pct', 'shipping_rate', 'shipping_rate')

    @property
    def display_customs_pct(self):
        return self._display_rate('customs_pct', 'customs_rate', 'customs_rate')

    @property
    def display_finances_pct(self):
        return self._display_rate('finances_pct', 'finances_rate', 'finances_rate')

    @property
    def display_installation_pct(self):
        return self._display_rate('installation_pct', 'installation_rate', 'installation_rate')
