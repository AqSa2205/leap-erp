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

    # Terms & Conditions
    terms_and_conditions = models.TextField(blank=True, verbose_name="Terms & Conditions")

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_purchase_orders'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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


class PurchaseOrderItem(models.Model):
    """Individual line item in a Purchase Order."""

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='items'
    )
    serial_number = models.PositiveIntegerField(default=1, verbose_name="S.No.")
    make_model = models.CharField(max_length=255, blank=True, verbose_name="Make/Model")
    description = models.TextField(verbose_name="Item Description / Specification")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1'), verbose_name="Quantity")
    uom = models.CharField(max_length=50, default='Nos', verbose_name="UOM")
    rate_per_unit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), verbose_name="Rate/Unit (SAR)")
    remarks = models.TextField(blank=True, verbose_name="Remarks")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'serial_number']

    def __str__(self):
        return f"#{self.serial_number} - {self.description[:50]}"

    @property
    def total_value(self):
        return (self.quantity * self.rate_per_unit).quantize(Decimal('0.01'))


# ─── Procurement Summary ──────────────────────────────────────


class ProcurementSummary(models.Model):
    """Procurement Summary Report - tracks items across packages/projects."""

    project_name = models.CharField(max_length=500, verbose_name="Project Name")
    package_name = models.CharField(max_length=255, blank=True, verbose_name="Package Name")
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='procurement_summaries',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_procurement_summaries'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = "Procurement Summary"
        verbose_name_plural = "Procurement Summaries"

    def __str__(self):
        if self.package_name:
            return f"{self.project_name} - {self.package_name}"
        return self.project_name

    @property
    def total_po_value_sar(self):
        return sum(
            (i.po_value_sar or Decimal('0')) for i in self.items.all()
        )

    @property
    def total_po_value_usd(self):
        return sum(
            (i.po_value_usd or Decimal('0')) for i in self.items.all()
        )

    @property
    def total_advance_payment(self):
        return sum(
            (i.advance_payment or Decimal('0')) for i in self.items.all()
        )


class ProcurementSummaryItem(models.Model):
    """Individual line item in a Procurement Summary."""

    PO_STATUS_CHOICES = [
        ('', '-'),
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('acknowledged', 'Acknowledged'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    summary = models.ForeignKey(
        ProcurementSummary, on_delete=models.CASCADE, related_name='items'
    )
    serial_number = models.PositiveIntegerField(default=1, verbose_name="Sr.")
    system_item = models.CharField(max_length=500, verbose_name="System / Item")
    po_number = models.CharField(max_length=100, blank=True, verbose_name="PO Number")
    po_status = models.CharField(max_length=20, choices=PO_STATUS_CHOICES, blank=True, verbose_name="PO Status")
    po_qty = models.CharField(max_length=100, blank=True, verbose_name="PO QTY")
    supplier = models.CharField(max_length=255, blank=True, verbose_name="Supplier")
    lead_time = models.CharField(max_length=255, blank=True, verbose_name="Lead Time")
    incoterm = models.CharField(max_length=100, blank=True, verbose_name="Incoterm")
    payment_terms = models.TextField(blank=True, verbose_name="Payment Terms")
    warranty = models.CharField(max_length=255, blank=True, verbose_name="Warranty")
    po_value_sar = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        verbose_name="PO Value (SAR)"
    )
    po_value_usd = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        verbose_name="PO Value (USD/EUR)"
    )
    advance_payment = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        verbose_name="Advance Payment"
    )
    delivery_status = models.TextField(blank=True, verbose_name="Delivery Status")
    scm = models.CharField(max_length=50, blank=True, verbose_name="SCM")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'serial_number']

    def __str__(self):
        return f"#{self.serial_number} - {self.system_item[:50]}"


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
