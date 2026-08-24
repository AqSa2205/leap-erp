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
    def natural_side(self):
        """Which side this account normally carries a balance on.

        Assets, cost of sale, expenses and Zakat build up on the debit side;
        liabilities, equity and revenue on the credit side. The ledger uses
        this so a balance reads as a positive number the way finance expects,
        instead of every revenue account showing as negative.
        """
        return 'debit' if self.code[:1] in ('1', '4', '5', '6') else 'credit'

    def signed_balance(self, debit_total, credit_total):
        """Balance in this account's natural direction."""
        if self.natural_side == 'debit':
            return debit_total - credit_total
        return credit_total - debit_total


def descendant_ids(root, accounts=None):
    """Every account id at or beneath `root`, including `root` itself.

    A heading carries no postings of its own, so its ledger is the combined
    ledger of everything filed under it. Walks an in-memory children map so a
    deep branch costs one query rather than one per level, and the visited set
    keeps a malformed import from looping forever.
    """
    if accounts is None:
        accounts = Account.objects.all()
    children = {}
    for account in accounts:
        children.setdefault(account.parent_id, []).append(account.pk)

    collected, queue, guard = {root.pk}, [root.pk], 0
    while queue and guard < 10000:
        current = queue.pop()
        for child_pk in children.get(current, ()):
            if child_pk not in collected:
                collected.add(child_pk)
                queue.append(child_pk)
        guard += 1
    return collected

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


class AccountingSettings(models.Model):
    """Singleton holding the control accounts a document posts against.

    An invoice knows its revenue accounts line by line, but the receivable and
    the output tax it also hits are company-wide choices, not per-document
    ones. Keeping them configurable means the posting rules never hardcode a
    code from one particular chart.
    """

    default_receivable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        help_text='Debited by a customer invoice when the partner has no own AR account.')
    default_payable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        help_text='Credited by a vendor bill when the partner has no own AP account.')
    output_tax_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        help_text='VAT charged on sales.')
    input_tax_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        help_text='VAT paid on purchases.')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Accounting settings'
        verbose_name_plural = 'Accounting settings'

    def __str__(self):
        return 'Accounting settings'

    def save(self, *args, **kwargs):
        self.pk = 1                      # singleton — always the same row
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ZohoCredentials(models.Model):
    """Singleton holding the Zoho Books OAuth credentials.

    Stored in the database rather than settings because the refresh token is
    obtained at runtime (by exchanging a one-time Self Client code) and the
    access token is rewritten every hour — neither can live in an env var.
    The client id/secret can be seeded from the environment on first use so a
    deploy never needs the secret typed into a form.

    Read-only by design: the scopes requested are all `.READ`, and nothing in
    accounting.zoho issues a write to Zoho.
    """

    client_id = models.CharField(max_length=255, blank=True)
    client_secret = models.CharField(max_length=255, blank=True)
    organization_id = models.CharField(
        max_length=64, blank=True,
        help_text='Zoho Books → Settings → Organization Profile.')

    # Zoho runs regional data centres. accounts_url is where tokens are minted;
    # api_domain is whatever Zoho returns as `api_domain` at token time, so it
    # is recorded rather than assumed.
    accounts_url = models.CharField(
        max_length=255, default='https://accounts.zoho.com',
        help_text='Region-specific Zoho accounts host, e.g. https://accounts.zoho.sa')
    api_domain = models.CharField(max_length=255, default='https://www.zohoapis.com')

    refresh_token = models.CharField(
        max_length=512, blank=True,
        help_text='Obtained once by exchanging a Self Client code; lasts until revoked.')
    access_token = models.CharField(max_length=2048, blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Zoho credentials'
        verbose_name_plural = 'Zoho credentials'

    def __str__(self):
        return 'Zoho Books credentials'

    def save(self, *args, **kwargs):
        self.pk = 1                       # singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        # Environment wins for the two long-lived secrets, so production can
        # set them via the Render dashboard exactly like the R2 and Anthropic
        # keys, and they never pass through a form or a git diff.
        import os
        changed = []
        for field, env in (('client_id', 'ZOHO_CLIENT_ID'),
                           ('client_secret', 'ZOHO_CLIENT_SECRET'),
                           ('organization_id', 'ZOHO_ORGANIZATION_ID')):
            value = os.environ.get(env)
            if value and getattr(obj, field) != value:
                setattr(obj, field, value)
                changed.append(field)
        if changed:
            obj.save(update_fields=changed + ['updated_at'])
        return obj

    @property
    def is_configured(self):
        return bool(self.client_id and self.client_secret
                    and self.organization_id and self.refresh_token)

    @property
    def status(self):
        """Human-readable state for the admin, without exposing any secret."""
        if not (self.client_id and self.client_secret):
            return 'No client credentials'
        if not self.organization_id:
            return 'No organization ID'
        if not self.refresh_token:
            return 'Not connected — needs a Self Client code'
        return 'Connected'


class ZohoAccountMapQuerySet(models.QuerySet):
    def unmapped(self):
        return self.filter(account__isnull=True, is_ignored=False)

    def mapped(self):
        return self.filter(account__isnull=False)


class ZohoAccountMap(models.Model):
    """Translates one Zoho Books account into the ERP's own chart.

    The ERP chart is not a copy of Zoho's — it is the structure the finance
    team designed, and Zoho's data is coded *into* it. The two ends therefore
    use different vocabularies (Zoho: income/expense/cash/bank; ours:
    View/Regular/Payable/Receivable/Liquidity) and different codes, which is
    expected rather than a discrepancy to reconcile.

    The relationship is many-to-one on purpose: collapsing several Zoho
    accounts into one ERP account is very often the reason the reshaping is
    wanted in the first place.

    An unmapped row is a worklist item, not an error. Sync records every Zoho
    account it sees so finance can point it somewhere; nothing is silently
    dropped, because a transaction quietly vanishing is far worse than one
    that shows up needing attention.
    """

    zoho_account_id = models.CharField(max_length=64, unique=True, db_index=True)
    zoho_account_code = models.CharField(max_length=50, blank=True, db_index=True)
    zoho_account_name = models.CharField(max_length=255)
    zoho_account_type = models.CharField(
        max_length=50, blank=True,
        help_text="Zoho's own type, e.g. income, expense, cash, bank.")
    zoho_parent_name = models.CharField(max_length=255, blank=True)
    zoho_is_active = models.BooleanField(default=True)

    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True,
        related_name='zoho_mappings',
        help_text='The ERP account this lands in. Blank means it still needs mapping.')
    is_ignored = models.BooleanField(
        default=False,
        help_text='Deliberately not mapped — keeps it off the worklist without '
                  'pretending it has a home.')
    note = models.TextField(blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    objects = ZohoAccountMapQuerySet.as_manager()

    class Meta:
        ordering = ['zoho_account_code', 'zoho_account_name']
        verbose_name = 'Zoho account mapping'

    def __str__(self):
        target = self.account.code if self.account_id else 'unmapped'
        return f'{self.zoho_account_name} → {target}'

    @property
    def is_mapped(self):
        return self.account_id is not None

    @property
    def state(self):
        if self.account_id:
            return 'mapped'
        return 'ignored' if self.is_ignored else 'needs mapping'


def resolve_zoho_account(zoho_account_id=None, zoho_account_code=None):
    """Find the ERP account a Zoho account maps to, or None.

    Matches on Zoho's account id first — codes can be edited in Zoho, ids
    cannot — then falls back to the code so a mapping still resolves when an
    export carries no id.
    """
    lookup = None
    if zoho_account_id:
        lookup = ZohoAccountMap.objects.filter(
            zoho_account_id=str(zoho_account_id)).first()
    if lookup is None and zoho_account_code:
        lookup = ZohoAccountMap.objects.filter(
            zoho_account_code=str(zoho_account_code)).first()
    return lookup.account if lookup and lookup.account_id else None


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

    # The double-entry this document produced, if it produced one. Documents
    # mirrored from Zoho deliberately leave this empty — Zoho already recorded
    # the entry and it arrives through the journal import, so generating one
    # here as well would count the same invoice twice.
    voucher = models.OneToOneField(
        'Voucher', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='document')

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

    @property
    def is_invoice(self):
        return self.kind == self.KIND_INVOICE

    def control_account(self):
        """Receivable for an invoice, payable for a bill.

        The partner's own account wins so a chart that gives each customer its
        own GL code keeps working; otherwise the company-wide control account
        from AccountingSettings is used.
        """
        cfg = AccountingSettings.load()
        if self.is_invoice:
            return self.partner.receivable_account or cfg.default_receivable_account
        return self.partner.payable_account or cfg.default_payable_account

    def tax_account(self):
        cfg = AccountingSettings.load()
        return cfg.output_tax_account if self.is_invoice else cfg.input_tax_account

    def build_voucher(self, created_by=None):
        """Create the double-entry voucher this document represents.

        Invoice:  Dr receivable (total)      Cr revenue (per line) + Cr output tax
        Bill:     Dr expense (per line) + Dr input tax    Cr payable (total)

        Refuses rather than guesses. A document whose lines and tax don't reach
        its stored total is an import that didn't add up, and forcing it to
        balance would bury the discrepancy in the ledger — which is precisely
        where it would do the most damage.
        """
        if self.source == 'zoho':
            raise ValidationError(
                'This document came from Zoho, which already recorded the entry. '
                'Import the Zoho journal export instead of posting it again here.')
        if self.voucher_id:
            raise ValidationError(f'{self} has already been posted as {self.voucher}.')

        lines = [line for line in self.lines.all() if line.account_id]
        if not lines:
            raise ValidationError('Add at least one line with an account before posting.')

        control = self.control_account()
        if control is None:
            side = 'receivable' if self.is_invoice else 'payable'
            raise ValidationError(
                f'No {side} account set for {self.partner}, and no company default '
                f'configured in Accounting settings.')

        tax = self.tax_total or Decimal('0')
        tax_account = self.tax_account()
        if tax and tax_account is None:
            raise ValidationError(
                'This document carries tax but no tax account is configured in '
                'Accounting settings.')

        expected = sum((line.amount for line in lines), Decimal('0')) + tax
        if expected != (self.total or Decimal('0')):
            raise ValidationError(
                f'{self} does not add up: lines + tax = {expected} but the '
                f'document total is {self.total}. Fix the figures before posting.')

        voucher = Voucher.objects.create(
            voucher_type=Voucher.TYPE_JV,
            number=f'{"INV" if self.is_invoice else "BILL"}/{self.number}',
            date=self.date,
            narration=f'{self.get_kind_display()} {self.number} — {self.partner}',
            partner=self.partner,
            project=self.project,
            currency=self.currency,
            source=self.source,
            created_by=created_by,
        )

        order = 0
        # Control account carries the gross figure; the detail sits opposite.
        VoucherLine.objects.create(
            voucher=voucher, account=control, partner=self.partner,
            description=f'{self.partner}',
            debit=self.total if self.is_invoice else Decimal('0'),
            credit=Decimal('0') if self.is_invoice else self.total,
            order=order)
        for line in lines:
            order += 1
            VoucherLine.objects.create(
                voucher=voucher, account=line.account, partner=self.partner,
                project=line.project or self.project,
                description=line.description[:255],
                debit=Decimal('0') if self.is_invoice else line.amount,
                credit=line.amount if self.is_invoice else Decimal('0'),
                order=order)
        if tax:
            order += 1
            VoucherLine.objects.create(
                voucher=voucher, account=tax_account, partner=self.partner,
                description='VAT',
                debit=Decimal('0') if self.is_invoice else tax,
                credit=tax if self.is_invoice else Decimal('0'),
                order=order)

        voucher.post()
        self.voucher = voucher
        if self.status == self.STATUS_DRAFT:
            self.status = self.STATUS_OPEN
        self.save(update_fields=['voucher', 'status', 'updated_at'])
        return voucher


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
