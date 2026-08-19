"""Accounting records — chart of accounts, vouchers, invoices and bills.

Zoho Books remains the statutory book of record: it holds the bank feeds, the
ZATCA e-invoicing certification and the VAT filings, and this app deliberately
touches none of that. What lives here is the same accounting data restructured
the way the finance team actually works — Zoho's interface does not present it
usefully, so records are brought across (today via its exports, later via its
API) and given proper shape.

Because the ERP is not the statutory book, nothing here files, clears or pays
anything. It records.

This app is separate from `finance`: that one plans project cash flow (P.O
values, payment milestones, vendor outflows), while this one holds the
accounting structure everything is coded against.

NOTE: the app is called `accounting`, not `accounts` — `accounts` is already
the authentication app (custom User model, roles, permission grid).
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AccountQuerySet(models.QuerySet):
    def postable(self):
        """Accounts that can carry a posting — i.e. not `View` headings."""
        return self.exclude(internal_type=Account.TYPE_VIEW)

    def headings(self):
        return self.filter(internal_type=Account.TYPE_VIEW)

    def active(self):
        return self.filter(is_active=True)


class Account(models.Model):
    """One General Ledger account.

    Codes are 7 digits and encode the hierarchy: the leading digit is the
    account class (1 Assets … 6 Zakat) and trailing zeros mark heading levels,
    e.g. 1000000 Assets > 1100000 Current Assets > 1110000 Cash in hand >
    1110001 Cash in hand. `parent` is resolved by the importer rather than
    recomputed on the fly, so a chart that does not follow the convention
    perfectly still imports (the importer links each account to its nearest
    existing ancestor).
    """

    # Mirrors the source workbook's "Internal Type" column, which follows the
    # Odoo account-type vocabulary. `View` means a heading that groups other
    # accounts and must never be posted to; the rest are postable, with
    # Receivable/Payable/Liquidity carrying extra meaning downstream (AR, AP
    # and bank/cash reconciliation respectively).
    TYPE_VIEW = 'View'
    TYPE_REGULAR = 'Regular'
    TYPE_RECEIVABLE = 'Receivable'
    TYPE_PAYABLE = 'Payable'
    TYPE_LIQUIDITY = 'Liquidity'
    INTERNAL_TYPE_CHOICES = [
        (TYPE_VIEW, 'View — heading, not postable'),
        (TYPE_REGULAR, 'Regular'),
        (TYPE_RECEIVABLE, 'Receivable'),
        (TYPE_PAYABLE, 'Payable'),
        (TYPE_LIQUIDITY, 'Liquidity'),
    ]

    # Leading digit of the code -> reporting class. Used for grouping and for
    # the class filter on the chart page.
    CLASS_LABELS = {
        '1': 'Assets',
        '2': 'Liabilities and Equity',
        '3': 'Revenues',
        '4': 'Cost of Sale',
        '5': 'Administrative Expenses',
        '6': 'Zakat',
    }

    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='children',
        help_text='Nearest ancestor account. Blank for a top-level class.')
    internal_type = models.CharField(
        max_length=20, choices=INTERNAL_TYPE_CHOICES, default=TYPE_REGULAR)

    # The workbook carries a second, coarser "Reporting code / Balance Sheet
    # Description" pair on heading rows. Kept so the imported chart can be
    # diffed against the source without going back to the spreadsheet.
    reporting_code = models.CharField(max_length=20, blank=True)
    reporting_name = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(
        default=True,
        help_text='Cleared instead of deleting, so historic references survive.')
    source_row = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Row number in the imported workbook, for tracing issues back.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AccountQuerySet.as_manager()

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'

    @property
    def is_postable(self):
        """True when a journal line may hit this account (anything but View)."""
        return self.internal_type != self.TYPE_VIEW

    @property
    def account_class(self):
        """Reporting class label derived from the leading digit of the code."""
        return self.CLASS_LABELS.get(self.code[:1], '')

    @property
    def depth(self):
        """Nesting level, 0 for a top-level class. Walks `parent`, so callers
        rendering many rows should use `build_tree()` instead of this."""
        depth, node, guard = 0, self.parent, 0
        while node is not None and guard < 12:
            depth += 1
            node = node.parent
            guard += 1
        return depth


def subtree_counts(accounts):
    """Map account pk -> {'direct', 'total', 'postable'} in a single pass.

    `direct` is immediate children, `total` every descendant, `postable` the
    descendants that can actually carry a posting. Computed in memory from one
    queryset so a heading list can show "what's underneath" without a query per
    row. Cycles (which a bad import could create) are guarded by the visited
    set rather than blowing the stack.
    """
    accounts = list(accounts)
    children = {}
    for account in accounts:
        children.setdefault(account.parent_id, []).append(account)
    postable_flag = {a.pk: a.is_postable for a in accounts}

    memo = {}

    def walk(pk, seen):
        if pk in memo:
            return memo[pk]
        total = postable = 0
        for child in children.get(pk, ()):
            if child.pk in seen:
                continue
            seen.add(child.pk)
            sub = walk(child.pk, seen)
            total += 1 + sub['total']
            postable += (1 if postable_flag[child.pk] else 0) + sub['postable']
        result = {'direct': len(children.get(pk, ())), 'total': total,
                  'postable': postable}
        memo[pk] = result
        return result

    return {a.pk: walk(a.pk, {a.pk}) for a in accounts}


def build_tree(accounts):
    """Annotate `accounts` with a `tree_depth` for indented rendering.

    Takes any iterable of Account (already ordered by code) and computes depth
    from the in-memory parent map, so a whole chart renders from one query
    instead of one lookup per row. Accounts whose parent is not in the given
    set are treated as roots, which keeps a filtered/searched list readable.
    """
    accounts = list(accounts)
    by_id = {a.pk: a for a in accounts}
    for account in accounts:
        depth, parent_id, guard = 0, account.parent_id, 0
        while parent_id in by_id and guard < 12:
            depth += 1
            parent_id = by_id[parent_id].parent_id
            guard += 1
        account.tree_depth = depth
    return accounts


class Partner(models.Model):
    """A customer or vendor.

    Invoices and bills are meaningless without one, and the free-text
    `vendor_name` scattered across procurement can't be relied on ("Al Ghad Al
    Taqni Est" vs "Al-Ghad Al Taqni" would be two different vendors). Records
    imported from Zoho carry its contact id so a re-import updates in place
    rather than duplicating.
    """

    KIND_CUSTOMER = 'customer'
    KIND_VENDOR = 'vendor'
    KIND_BOTH = 'both'
    KIND_CHOICES = [
        (KIND_CUSTOMER, 'Customer'),
        (KIND_VENDOR, 'Vendor'),
        (KIND_BOTH, 'Customer & Vendor'),
    ]

    name = models.CharField(max_length=255, db_index=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_CUSTOMER)
    vat_number = models.CharField(max_length=50, blank=True)
    cr_number = models.CharField(
        max_length=50, blank=True, verbose_name='CR number',
        help_text='Commercial Registration number.')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    # Where this partner's balance sits. Blank falls back to the control
    # account for the document's direction.
    receivable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True,
        related_name='receivable_partners')
    payable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True,
        related_name='payable_partners')

    zoho_contact_id = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text='Zoho Books contact id, so a re-import updates in place.')
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_customer(self):
        return self.kind in (self.KIND_CUSTOMER, self.KIND_BOTH)

    @property
    def is_vendor(self):
        return self.kind in (self.KIND_VENDOR, self.KIND_BOTH)


class VoucherQuerySet(models.QuerySet):
    def posted(self):
        return self.filter(status=Voucher.STATUS_POSTED)

    def of_type(self, voucher_type):
        return self.filter(voucher_type=voucher_type)


class Voucher(models.Model):
    """A journal, payment or receipt voucher — the double-entry record.

    JV  journal voucher — adjustments and corrections, any accounts.
    PV  payment voucher — money out (credit bank/cash, debit payable/expense).
    RV  receipt voucher — money in (debit bank/cash, credit receivable/income).

    Lines must balance before a voucher can be posted. That single rule is what
    makes every account view derived from these records trustworthy rather than
    a pile of loose numbers.
    """

    TYPE_JV = 'JV'
    TYPE_PV = 'PV'
    TYPE_RV = 'RV'
    TYPE_CHOICES = [
        (TYPE_JV, 'Journal Voucher'),
        (TYPE_PV, 'Payment Voucher'),
        (TYPE_RV, 'Receipt Voucher'),
    ]

    STATUS_DRAFT = 'draft'
    STATUS_POSTED = 'posted'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_POSTED, 'Posted'),
    ]

    voucher_type = models.CharField(max_length=2, choices=TYPE_CHOICES, db_index=True)
    number = models.CharField(
        max_length=50, db_index=True,
        help_text="Voucher number. Imported records keep Zoho's own number.")
    date = models.DateField(db_index=True)
    narration = models.TextField(blank=True)

    partner = models.ForeignKey(
        Partner, on_delete=models.PROTECT, null=True, blank=True,
        related_name='vouchers')
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accounting_vouchers',
        help_text='Optional dimension — lets cost be reported per project.')

    status = models.CharField(max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT, db_index=True)
    currency = models.CharField(max_length=10, default='SAR')

    # Provenance. Records mirrored from Zoho carry its id so a re-import
    # updates in place instead of creating a second copy.
    zoho_id = models.CharField(max_length=64, blank=True, db_index=True)
    source = models.CharField(
        max_length=20, default='manual',
        help_text="'manual' or 'zoho' — where this record came from.")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accounting_vouchers')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = VoucherQuerySet.as_manager()

    class Meta:
        ordering = ['-date', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['voucher_type', 'number'],
                name='accounting_voucher_unique_number_per_type'),
        ]

    def __str__(self):
        return f'{self.voucher_type}-{self.number}'

    @property
    def total_debit(self):
        return sum((line.debit for line in self.lines.all()), Decimal('0'))

    @property
    def total_credit(self):
        return sum((line.credit for line in self.lines.all()), Decimal('0'))

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit

    @property
    def amount(self):
        """Headline value — the debit side of a balanced voucher."""
        return self.total_debit

    def post(self):
        """Move a draft to posted, refusing anything that does not balance.

        A voucher with no lines is not 'balanced at zero' — it is empty, and
        posting it would drop a meaningless record into the ledger.
        """
        if self.status == self.STATUS_POSTED:
            raise ValidationError('This voucher is already posted.')
        if not self.lines.exists():
            raise ValidationError('A voucher needs at least one line before it can be posted.')
        if not self.is_balanced:
            raise ValidationError(
                f'Voucher does not balance: debit {self.total_debit} vs credit {self.total_credit}.')
        self.status = self.STATUS_POSTED
        self.save(update_fields=['status', 'updated_at'])


class VoucherLine(models.Model):
    """One side of a voucher entry — exactly one of debit/credit is non-zero."""

    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='voucher_lines')
    description = models.CharField(max_length=255, blank=True)

    debit = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))

    # Dimensions live on the line so cost can be reported per partner or per
    # project without growing a branch of the chart for every job.
    partner = models.ForeignKey(
        Partner, on_delete=models.PROTECT, null=True, blank=True,
        related_name='voucher_lines')
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accounting_voucher_lines')

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        side = f'Dr {self.debit}' if self.debit else f'Cr {self.credit}'
        return f'{self.account.code} {side}'

    def clean(self):
        if self.debit and self.credit:
            raise ValidationError('A line is either a debit or a credit, not both.')
        if not self.debit and not self.credit:
            raise ValidationError('A line needs a debit or a credit amount.')
        if self.debit < 0 or self.credit < 0:
            raise ValidationError('Amounts must be positive — use the other side instead.')
        if self.account_id and not self.account.is_postable:
            raise ValidationError(
                f'{self.account.code} {self.account.name} is a heading and cannot be posted to.')


class DocumentQuerySet(models.QuerySet):
    def invoices(self):
        return self.filter(kind=Document.KIND_INVOICE)

    def bills(self):
        return self.filter(kind=Document.KIND_BILL)

    def outstanding(self):
        return self.exclude(status__in=(Document.STATUS_PAID, Document.STATUS_VOID))


class Document(models.Model):
    """A customer invoice or a vendor bill.

    One model rather than two: an invoice and a bill differ by direction and by
    which control account they sit against, but share every other concern —
    partner, dates, lines, tax, totals, settlement. Splitting them would
    duplicate all of that and leave two code paths to keep in step.

    These are records, not instruments. The ERP does not issue or clear a tax
    invoice — Zoho Books does that, and holds the ZATCA certification for it.
    """

    KIND_INVOICE = 'invoice'
    KIND_BILL = 'bill'
    KIND_CHOICES = [
        (KIND_INVOICE, 'Customer Invoice'),
        (KIND_BILL, 'Vendor Bill'),
    ]

    STATUS_DRAFT = 'draft'
    STATUS_OPEN = 'open'
    STATUS_PARTIAL = 'partial'
    STATUS_PAID = 'paid'
    STATUS_VOID = 'void'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_OPEN, 'Open'),
        (STATUS_PARTIAL, 'Partly paid'),
        (STATUS_PAID, 'Paid'),
        (STATUS_VOID, 'Void'),
    ]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES, db_index=True)
    number = models.CharField(
        max_length=50, db_index=True,
        help_text="Document number. Imported records keep Zoho's own number.")
    reference = models.CharField(
        max_length=100, blank=True,
        help_text='Their reference — a customer PO, or the vendor invoice number.')
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='documents')

    date = models.DateField(db_index=True)
    due_date = models.DateField(null=True, blank=True)

    currency = models.CharField(max_length=10, default='SAR')
    # Totals are stored rather than derived: these mirror figures that already
    # exist in Zoho, and a rounding difference in our arithmetic must never
    # silently restate what the statutory book says.
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    tax_total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    amount_paid = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))

    status = models.CharField(max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT, db_index=True)
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accounting_documents')
    notes = models.TextField(blank=True)

    zoho_id = models.CharField(max_length=64, blank=True, db_index=True)
    source = models.CharField(max_length=20, default='manual')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accounting_documents')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DocumentQuerySet.as_manager()

    class Meta:
        ordering = ['-date', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'number'],
                name='accounting_document_unique_number_per_kind'),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} {self.number}'

    @property
    def balance_due(self):
        return (self.total or Decimal('0')) - (self.amount_paid or Decimal('0'))

    @property
    def is_overdue(self):
        """Past its due date with money still outstanding."""
        from django.utils import timezone
        if not self.due_date or self.status in (self.STATUS_PAID, self.STATUS_VOID):
            return False
        return self.due_date < timezone.localdate() and self.balance_due > 0

    @property
    def lines_total(self):
        """Sum of the line amounts — compared against `total` to surface an
        import that didn't add up, rather than quietly trusting either."""
        return sum((line.amount for line in self.lines.all()), Decimal('0'))


class DocumentLine(models.Model):
    """One line of an invoice or bill, coded to a GL account."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='lines')
    description = models.CharField(max_length=500, blank=True)
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True,
        related_name='document_lines',
        help_text='Income account for an invoice, expense/COGS for a bill.')

    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1'))
    unit_price = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal('0'),
        help_text='Line total before tax.')

    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accounting_document_lines')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.description[:60] or f'Line {self.pk}'

    def clean(self):
        if self.account_id and not self.account.is_postable:
            raise ValidationError(
                f'{self.account.code} {self.account.name} is a heading and cannot be posted to.')
