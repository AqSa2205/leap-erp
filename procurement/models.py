from django.db import models
from django.conf import settings
from decimal import Decimal


class PurchaseOrder(models.Model):
    """Purchase Order header with vendor info, project details, and terms."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('acknowledged', 'Acknowledged'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    COST_CENTER_CHOICES = [
        ('projects', 'Projects'),
        ('operations', 'Operations'),
        ('maintenance', 'Maintenance'),
        ('it', 'IT'),
        ('other', 'Other'),
    ]

    INCOTERM_CHOICES = [
        ('DDP', 'DDP - Delivered Duty Paid'),
        ('DAP', 'DAP - Delivered at Place'),
        ('EXW', 'EXW - Ex Works'),
        ('FOB', 'FOB - Free on Board'),
        ('CIF', 'CIF - Cost, Insurance & Freight'),
        ('CFR', 'CFR - Cost & Freight'),
        ('FCA', 'FCA - Free Carrier'),
        ('CPT', 'CPT - Carriage Paid To'),
        ('CIP', 'CIP - Carriage & Insurance Paid To'),
        ('DAT', 'DAT - Delivered at Terminal'),
    ]

    # Suggested values for the `system` datalist on the PO form.
    # Stored as free text so users can pick from these or type their own.
    SYSTEM_SUGGESTIONS = [
        'Siren',
        'PAGA',
        'PICS',
        'GSM Repeater',
        'CCTV',
        'UPS',
    ]

    # PO Header
    po_date = models.DateField(verbose_name="PO Date")
    po_number = models.CharField(max_length=100, unique=True, verbose_name="PO S. No.")
    cost_center = models.CharField(
        max_length=30, choices=COST_CENTER_CHOICES, default='projects',
        verbose_name="Cost Center"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Vendor Information
    vendor_name = models.CharField(max_length=255, verbose_name="Vendor")
    vendor_contact_person = models.CharField(max_length=255, blank=True, verbose_name="Contact Person")
    vendor_contact_email = models.EmailField(blank=True, verbose_name="Vendor Contact Email")
    vendor_contact_tel = models.CharField(max_length=100, blank=True, verbose_name="Contact Tel")

    # Issuer Information
    po_issued_by = models.CharField(max_length=255, verbose_name="PO Issued By")
    issuer_email = models.EmailField(blank=True, verbose_name="Issuer Contact Email")

    # Project Information
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='purchase_orders',
        verbose_name="Project"
    )
    project_name = models.CharField(max_length=500, blank=True, verbose_name="Project Name")
    end_user = models.CharField(max_length=255, blank=True, verbose_name="End User")
    mr_item_number = models.CharField(max_length=255, blank=True, verbose_name="MR / Item No.")

    # Delivery Information
    delivery_incoterms = models.CharField(
        max_length=10, choices=INCOTERM_CHOICES, blank=True,
        verbose_name="Delivery Incoterms"
    )
    delivery_location = models.CharField(max_length=500, blank=True, verbose_name="Delivery Location")

    # Financial
    discount_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0'),
        verbose_name="Discount %", help_text="e.g. 5 for 5%"
    )
    vat_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('15'),
        verbose_name="VAT %", help_text="e.g. 15 for 15%"
    )

    # Procurement Tracking (drives the External summary)
    lead_time = models.CharField(
        max_length=255, blank=True, verbose_name="Lead Time",
        help_text='e.g. "4-6 weeks from PO & payment", "10-15 days"',
    )
    payment_terms_text = models.TextField(
        blank=True, verbose_name="Payment Terms (Summary)",
        help_text='Short payment-terms summary used in procurement reports — '
                  'e.g. "30% Advance\\n70% upon Delivery". '
                  'Separate from the longer Terms & Conditions text below.',
    )
    warranty = models.CharField(
        max_length=255, blank=True, verbose_name="Warranty",
        help_text='e.g. "Standard", "24 Months after delivery", "3 Years"',
    )

    # Terms & Conditions
    terms_and_conditions = models.TextField(blank=True, verbose_name="Terms & Conditions")
    selected_terms = models.ManyToManyField(
        'costing.TermsTemplate', blank=True, related_name='purchase_orders'
    )

    # Approval workflow — sequential SCM → PM → COO → CEO. CEO is only
    # required when total_value crosses CEO_APPROVAL_THRESHOLD. Each stage
    # carries a timestamp + approver FK so the PO detail page can show a
    # live timeline; exports stay disabled until is_released is True.
    scm_approved_at = models.DateTimeField(null=True, blank=True, verbose_name='SCM Approved At')
    scm_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='scm_approved_pos',
    )
    scm_signature = models.ImageField(
        upload_to='procurement/po_signatures/scm/', null=True, blank=True,
        verbose_name='SCM Signature Image',
    )
    pm_approved_at = models.DateTimeField(null=True, blank=True, verbose_name='PM Approved At')
    pm_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pm_approved_pos',
    )
    pm_signature = models.ImageField(
        upload_to='procurement/po_signatures/pm/', null=True, blank=True,
        verbose_name='PM Signature Image',
    )
    coo_approved_at = models.DateTimeField(null=True, blank=True, verbose_name='COO Approved At')
    coo_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='coo_approved_pos',
    )
    coo_signature = models.ImageField(
        upload_to='procurement/po_signatures/coo/', null=True, blank=True,
        verbose_name='COO Signature Image',
    )
    ceo_approved_at = models.DateTimeField(null=True, blank=True, verbose_name='CEO Approved At')
    ceo_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ceo_approved_pos',
    )
    ceo_signature = models.ImageField(
        upload_to='procurement/po_signatures/ceo/', null=True, blank=True,
        verbose_name='CEO Signature Image',
    )

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_purchase_orders'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Hardcoded signers per stage. Order matters — first to last is the
    # required sequence. CEO is only required for high-value POs.
    APPROVAL_STAGES = (
        ('scm', 'SCM Approval', 'Shaker Alkhalifah'),
        ('pm',  'PM Approval',  'Ali Sultan'),
        ('coo', 'COO Approval', 'Babar Zulfiqar'),
        ('ceo', 'CEO Approval', 'Asif Imam'),
    )
    CEO_APPROVAL_THRESHOLD = Decimal('1000000')

    class Meta:
        ordering = ['-po_date', '-created_at']
        verbose_name = "Purchase Order"
        verbose_name_plural = "Purchase Orders"

    def __str__(self):
        return f"{self.po_number} - {self.vendor_name}"

    @property
    def base_amount(self):
        total = Decimal('0')
        for item in self.items.all():
            total += item.total_value
        return total

    @property
    def discount_amount(self):
        return (self.base_amount * self.discount_rate / Decimal('100')).quantize(Decimal('0.01'))

    @property
    def gross_value(self):
        return self.base_amount - self.discount_amount

    @property
    def vat_amount(self):
        return (self.gross_value * self.vat_rate / Decimal('100')).quantize(Decimal('0.01'))

    @property
    def total_value(self):
        return self.gross_value + self.vat_amount

    @property
    def requires_ceo_approval(self):
        return self.total_value >= self.CEO_APPROVAL_THRESHOLD

    @property
    def required_stages(self):
        """Tuple of stage keys this PO must pass through, in order."""
        if self.requires_ceo_approval:
            return ('scm', 'pm', 'coo', 'ceo')
        return ('scm', 'pm', 'coo')

    @property
    def approval_status(self):
        """Per-stage state for the timeline UI and PDF rendering.

        Returns a list of dicts: key, label, signer, approved_at,
        approved_by, is_approved, is_current. Stages not required for
        this PO (e.g. CEO under threshold) are omitted entirely so the
        UI and PDF don't show empty slots.
        """
        out = []
        seen_pending = False
        for key, label, signer in self.APPROVAL_STAGES:
            if key not in self.required_stages:
                continue
            ts = getattr(self, f'{key}_approved_at')
            by = getattr(self, f'{key}_approved_by')
            is_approved = ts is not None
            is_current = (not is_approved) and (not seen_pending)
            if not is_approved:
                seen_pending = True
            sig = getattr(self, f'{key}_signature', None)
            sig_url = sig.url if sig else None
            out.append({
                'key': key,
                'label': label,
                'signer': signer,
                'approved_at': ts,
                'approved_by': by,
                'is_approved': is_approved,
                'is_current': is_current,
                'signature': sig,
                'signature_url': sig_url,
            })
        return out

    @property
    def current_stage(self):
        """The stage dict awaiting approval right now, or None if released."""
        for s in self.approval_status:
            if not s['is_approved']:
                return s
        return None

    @property
    def is_released(self):
        """All required stages signed → PO can be printed."""
        return self.current_stage is None

    @property
    def approved_stages(self):
        """Just the stages that have been signed, in order."""
        return [s for s in self.approval_status if s['is_approved']]

    def can_user_approve_stage(self, user, stage_key):
        """Permission gate per stage.

        Mapping (super_admin can do anything):
          - SCM  → procurement manager (Shaker)
          - PM   → admin (Ali)
          - COO  → admin (Babar)
          - CEO  → super_admin only (Asif) — high-value escalation
        """
        if not user or not user.is_authenticated:
            return False
        if user.is_super_admin_user:
            return True
        if stage_key == 'scm':
            return getattr(user, 'is_procurement_manager_user', False)
        if stage_key in ('pm', 'coo'):
            return user.is_admin_user
        # CEO stays super-admin-only.
        return False


class PurchaseOrderItem(models.Model):
    """Individual line item in a Purchase Order."""

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='items'
    )
    serial_number = models.PositiveIntegerField(default=1, verbose_name="S.No.")
    system = models.CharField(
        max_length=100, blank=True, verbose_name="System",
        help_text='Pick from the suggestion list (Siren / PAGA / PICS / GSM Repeater / CCTV / UPS) '
                  'or type a custom value. Drives grouping in the procurement summary.',
    )
    make_model = models.CharField(max_length=255, blank=True, verbose_name="Make/Model")
    description = models.TextField(verbose_name="Item Description / Specification")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1'), verbose_name="Quantity")
    uom = models.CharField(max_length=50, default='Nos', verbose_name="UOM")
    rate_per_unit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), verbose_name="Rate/Unit (SAR)")
    remarks = models.TextField(blank=True, verbose_name="Remarks")

    # Procurement-tracking fields (drive the External summary; also editable
    # inline from the External summary table)
    po_value_usd = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        verbose_name="PO Value (USD/EUR)",
    )
    advance_payment_sar = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        verbose_name="Advance Payment (SAR)",
    )
    delivery_status = models.TextField(
        blank=True, verbose_name="Delivery Status",
        help_text='Free-text status, e.g. "Delivered HO", "Received at LNA 04-12-2025".',
    )
    scm = models.CharField(
        max_length=10, blank=True, verbose_name="SCM",
        help_text='Initials of the SCM team member responsible (e.g. ST, ZH).',
    )

    source_bom_item = models.ForeignKey(
        'costing.CostingLineItem',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='procured_po_items',
        help_text='If imported from a costing sheet BOM, points back to the '
                  'source line item — used to mark BOM items "already procured" '
                  'and to show PO traceability per BOM row.',
    )

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'serial_number']

    def __str__(self):
        return f"#{self.serial_number} - {self.description[:50]}"

    @property
    def total_value(self):
        return (self.quantity * self.rate_per_unit).quantize(Decimal('0.01'))


# ─── Procurement Summary (Internal / External) ────────────────


class POSummaryEntry(models.Model):
    """One row of the Internal / External procurement summary, attached
    to a single PurchaseOrderItem. Columns A-F are derived live from the
    line item + its parent PO; everything below is the procurement team's
    tracking data (dates + remarks)."""

    SUMMARY_TYPE_CHOICES = [
        ('internal', 'Internal'),
        ('external', 'External'),
    ]

    purchase_order_item = models.OneToOneField(
        PurchaseOrderItem, on_delete=models.CASCADE,
        related_name='summary_entry',
    )
    summary_type = models.CharField(
        max_length=20, choices=SUMMARY_TYPE_CHOICES, default='internal',
    )

    # PO milestone dates (separate from po_date which is the issuance date)
    po_plan = models.DateField(null=True, blank=True, verbose_name='PO Plan')
    po_forecast = models.DateField(null=True, blank=True, verbose_name='PO Forecast')
    po_actual = models.DateField(null=True, blank=True, verbose_name='PO Actual')

    # Delivery — Plan / Readiness / Forecast / Actual
    delivery_plan = models.DateField(null=True, blank=True, verbose_name='Delivery Plan')
    delivery_readiness = models.DateField(null=True, blank=True, verbose_name='Readiness Date')
    delivery_forecast = models.DateField(null=True, blank=True, verbose_name='Delivery Forecast')
    delivery_actual = models.DateField(null=True, blank=True, verbose_name='Delivery Actual')

    remarks = models.TextField(blank=True, verbose_name='Remarks')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            'purchase_order_item__system',
            'purchase_order_item__purchase_order__po_number',
            'purchase_order_item__serial_number',
        ]
        verbose_name = 'PO Summary Entry'
        verbose_name_plural = 'PO Summary Entries'

    def __str__(self):
        item = self.purchase_order_item
        return f'{item.purchase_order.po_number} #{item.serial_number} ({self.get_summary_type_display()})'


# ─── Delivery Note ────────────────────────────────────────────


class DeliveryNote(models.Model):
    """Delivery Note for goods dispatched to clients/sites."""

    # Sold To
    sold_to_company = models.CharField(max_length=255, verbose_name="Sold To (Company)")
    sold_to_address = models.TextField(blank=True, verbose_name="Sold To (Address)")

    # Delivery
    delivery_address = models.TextField(blank=True, verbose_name="Delivery Address")

    # Contact
    attention = models.CharField(max_length=255, blank=True, verbose_name="Attention")
    mobile = models.CharField(max_length=100, blank=True, verbose_name="Mobile")
    email = models.EmailField(blank=True, verbose_name="Email")
    date = models.DateField(verbose_name="Date")

    # Project / PO
    project_title = models.CharField(max_length=500, blank=True, verbose_name="Project Title")
    leap_po_number = models.CharField(max_length=100, blank=True, verbose_name="LEAP PO No.")
    client_po_number = models.CharField(max_length=255, blank=True, verbose_name="Client PO No.")
    dn_number = models.CharField(max_length=100, blank=True, verbose_name="DN Number / No of Packages")

    # Links
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='delivery_notes',
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='delivery_notes',
    )

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_delivery_notes',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = "Delivery Note"
        verbose_name_plural = "Delivery Notes"

    def __str__(self):
        return f"DN {self.dn_number or self.pk} - {self.sold_to_company}"


class DeliveryNoteItem(models.Model):
    """Line item in a Delivery Note."""

    delivery_note = models.ForeignKey(
        DeliveryNote, on_delete=models.CASCADE, related_name='items'
    )
    serial_number = models.PositiveIntegerField(default=1, verbose_name="S.No.")
    make_part_number = models.CharField(max_length=255, blank=True, verbose_name="Make/Part Number")
    description = models.TextField(verbose_name="Description")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1'), verbose_name="Quantity")
    uom = models.CharField(max_length=50, default='Each', verbose_name="UOM")
    remarks = models.TextField(blank=True, verbose_name="Remarks / LNA PO# ref")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'serial_number']
        verbose_name = "Delivery Note Item"

    def __str__(self):
        return f"#{self.serial_number} - {self.description[:50]}"


# ─── Inventory Report ─────────────────────────────────────────


class InventoryReport(models.Model):
    """Inventory / Store Material Report for a warehouse or project."""

    title = models.CharField(max_length=255, verbose_name="Report Title")
    warehouse_location = models.CharField(max_length=255, blank=True, verbose_name="Warehouse / Location")
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='inventory_reports',
    )
    report_date = models.DateField(auto_now_add=True, verbose_name="Report Date")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_inventory_reports',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = "Inventory Report"
        verbose_name_plural = "Inventory Reports"

    def __str__(self):
        return self.title

    @property
    def total_items(self):
        return self.items.count()

    @property
    def total_stock_value(self):
        return sum(
            (i.unit_cost or Decimal('0')) * (i.balance_qty or Decimal('0'))
            for i in self.items.all()
        )

    @property
    def low_stock_count(self):
        """Count of items where balance_qty <= min_stock_level. Pushed
        into SQL via the InventoryItem queryset's low_stock() method so
        callers don't pay an N+1 cost when this is rendered in a list."""
        return self.items.low_stock().count()


class InventoryItemQuerySet(models.QuerySet):
    """Custom queryset that pushes the low-stock condition into the DB."""

    def low_stock(self):
        """Items where balance_qty <= min_stock_level (and both are set).
        Equivalent to the is_low_stock property but expressed in SQL so it
        can be used with .count() / .filter() without a Python loop."""
        return self.filter(
            min_stock_level__isnull=False,
            min_stock_level__gt=0,
            balance_qty__isnull=False,
            balance_qty__lte=models.F('min_stock_level'),
        )


class InventoryItem(models.Model):
    """Individual item in an Inventory Report."""

    objects = InventoryItemQuerySet.as_manager()

    CATEGORY_CHOICES = [
        ('cctv', 'CCTV / Cameras'),
        ('network', 'Network / Switches'),
        ('telecom', 'Telecom / Radio'),
        ('solar', 'Solar / Power'),
        ('electrical', 'Electrical'),
        ('cables', 'Cables / Connectors'),
        ('tools', 'Tools / Equipment'),
        ('safety', 'Safety / PPE'),
        ('furniture', 'Furniture / Appliances'),
        ('consumables', 'Consumables'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('available', 'Available'),
        ('reserved', 'Reserved'),
        ('issued', 'Issued'),
        ('damaged', 'Damaged'),
        ('returned', 'Returned'),
    ]

    report = models.ForeignKey(
        InventoryReport, on_delete=models.CASCADE, related_name='items'
    )
    serial_number = models.PositiveIntegerField(default=1, verbose_name="S.No.")
    item_code = models.CharField(max_length=50, blank=True, verbose_name="Item Code / SKU")
    name = models.CharField(max_length=500, verbose_name="Item Name")
    description = models.TextField(blank=True, verbose_name="Description")
    model_number = models.CharField(max_length=255, blank=True, verbose_name="Model")
    make = models.CharField(max_length=255, blank=True, verbose_name="Make / Manufacturer")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='other', verbose_name="Category")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available', verbose_name="Status")
    unit = models.CharField(max_length=50, default='Each', verbose_name="Unit")

    # Stock
    quantity_received = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), verbose_name="Qty Received")
    quantity_issued = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), verbose_name="Qty Issued")
    balance_qty = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Balance Qty")
    min_stock_level = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Min Stock Level")

    # Financial
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Unit Cost (SAR)")

    # Location
    tag = models.CharField(max_length=100, blank=True, verbose_name="TAG / Serial")
    rack_location = models.CharField(max_length=100, blank=True, verbose_name="Rack / Shelf / Bin")

    # Movement
    received_date = models.DateField(null=True, blank=True, verbose_name="Received Date")
    handover_to = models.CharField(max_length=255, blank=True, verbose_name="Handover To")
    handover_date = models.DateField(null=True, blank=True, verbose_name="Handover Date")
    project_ref = models.CharField(max_length=255, blank=True, verbose_name="Project")
    po_reference = models.CharField(max_length=100, blank=True, verbose_name="PO Reference")
    remarks = models.TextField(blank=True, verbose_name="Remarks")

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'serial_number']
        verbose_name = "Inventory Item"

    def __str__(self):
        return f"#{self.serial_number} - {self.name[:50]}"

    def save(self, *args, **kwargs):
        if self.balance_qty is None:
            self.balance_qty = self.quantity_received - self.quantity_issued
        super().save(*args, **kwargs)

    @property
    def total_value(self):
        if self.unit_cost and self.balance_qty:
            return (self.unit_cost * self.balance_qty).quantize(Decimal('0.01'))
        return Decimal('0')

    @property
    def is_low_stock(self):
        if self.min_stock_level and self.balance_qty is not None:
            return self.balance_qty <= self.min_stock_level
        return False


# ─── FRC / PPE Uniform Tracker ─────────────────────────────────


class FRCReport(models.Model):
    """FRC (Fire Retardant Clothing) / PPE Uniform issuance report."""

    title = models.CharField(max_length=255, verbose_name="Report Title")
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='frc_reports',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_frc_reports',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = "FRC / PPE Report"
        verbose_name_plural = "FRC / PPE Reports"

    def __str__(self):
        return self.title

    @property
    def total_employees(self):
        return self.entries.count()

    @property
    def total_shirts(self):
        return sum(e.shirt_qty for e in self.entries.all())

    @property
    def total_pants(self):
        return sum(e.pants_qty for e in self.entries.all())

    @property
    def total_shoes(self):
        return sum(e.shoes_qty for e in self.entries.all())


class FRCEntry(models.Model):
    """Individual employee FRC/PPE issuance record."""

    report = models.ForeignKey(
        FRCReport, on_delete=models.CASCADE, related_name='entries'
    )
    serial_number = models.PositiveIntegerField(default=1, verbose_name="Sr.")
    employee_name = models.CharField(max_length=255, verbose_name="Name")
    designation = models.CharField(max_length=255, blank=True, verbose_name="Designation")
    project_name = models.CharField(max_length=255, blank=True, verbose_name="Project")

    # PPE Items
    shirt_size = models.CharField(max_length=100, blank=True, verbose_name="Shirt Size")
    shirt_qty = models.PositiveIntegerField(default=0, verbose_name="Shirts")
    pants_size = models.CharField(max_length=100, blank=True, verbose_name="Pants Size")
    pants_qty = models.PositiveIntegerField(default=0, verbose_name="Pants")
    safety_glasses = models.CharField(max_length=50, blank=True, verbose_name="Safety Glasses")
    safety_shoes = models.CharField(max_length=100, blank=True, verbose_name="Safety Shoes")
    shoes_qty = models.PositiveIntegerField(default=0, verbose_name="Shoes Qty")
    safety_helmet = models.CharField(max_length=50, blank=True, verbose_name="Safety Helmet")

    # Tracking
    receiving_date = models.CharField(max_length=255, blank=True, verbose_name="Receiving Date")
    handed_to = models.CharField(max_length=255, blank=True, verbose_name="Handed To / Remarks")

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'serial_number']
        verbose_name = "FRC Entry"
        verbose_name_plural = "FRC Entries"

    def __str__(self):
        return f"#{self.serial_number} - {self.employee_name}"

    @property
    def total_items_issued(self):
        count = self.shirt_qty + self.pants_qty + self.shoes_qty
        if self.safety_glasses and self.safety_glasses not in ('N/A', 'n/a', '-', ''):
            try:
                count += int(self.safety_glasses)
            except ValueError:
                count += 1
        if self.safety_helmet and self.safety_helmet not in ('N/A', 'n/a', '-', ''):
            try:
                count += int(self.safety_helmet)
            except ValueError:
                count += 1
        return count


# ─── FRC Inventory (PPE Stock) ─────────────────────────────────


class FRCInventory(models.Model):
    """FRC/PPE stock inventory — tracks available stock by item type and size."""

    ITEM_TYPE_CHOICES = [
        ('shirt', 'FRC Shirt'),
        ('pants', 'FRC Pants'),
        ('shoes', 'Safety Shoes'),
        ('helmet', 'Safety Helmet'),
        ('glasses', 'Safety Glasses'),
        ('gloves', 'Safety Gloves'),
        ('vest', 'High-Vis Vest'),
        ('harness', 'Safety Harness'),
        ('coverall', 'Coverall'),
        ('other', 'Other PPE'),
    ]

    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES, verbose_name="Item Type")
    size = models.CharField(max_length=50, blank=True, verbose_name="Size")
    color = models.CharField(max_length=50, blank=True, verbose_name="Color")
    description = models.CharField(max_length=500, blank=True, verbose_name="Description / Notes")
    supplier = models.CharField(max_length=255, blank=True, verbose_name="Supplier")

    # Stock levels
    total_purchased = models.PositiveIntegerField(default=0, verbose_name="Total Purchased")
    total_issued = models.PositiveIntegerField(default=0, verbose_name="Total Issued")
    damaged_lost = models.PositiveIntegerField(default=0, verbose_name="Damaged / Lost")
    min_stock = models.PositiveIntegerField(default=5, verbose_name="Min Stock Level")

    # Financial
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Unit Cost (SAR)")

    # Metadata
    last_restocked = models.DateField(null=True, blank=True, verbose_name="Last Restocked")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_frc_inventory',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item_type', 'size']
        verbose_name = "FRC Inventory Item"
        verbose_name_plural = "FRC Inventory"
        unique_together = ['item_type', 'size', 'color']

    def __str__(self):
        parts = [self.get_item_type_display()]
        if self.size:
            parts.append(f"({self.size})")
        if self.color:
            parts.append(f"- {self.color}")
        return ' '.join(parts)

    @property
    def available_stock(self):
        return self.total_purchased - self.total_issued - self.damaged_lost

    @property
    def is_low_stock(self):
        return self.available_stock <= self.min_stock

    @property
    def stock_value(self):
        if self.unit_cost:
            return self.unit_cost * Decimal(str(self.available_stock))
        return Decimal('0')
