from django.db import models
from django.conf import settings
from decimal import Decimal
from datetime import timedelta


def working_days_between(start, end):
    """Whole working days from `start` to `end`, excluding the KSA Fri/Sat
    weekend. Returns None if either bound is missing, 0 if same day / end<=start.

    Both bounds are aware datetimes; only the local calendar date matters.
    """
    if not start or not end:
        return None
    from django.utils import timezone
    s = timezone.localtime(start).date()
    e = timezone.localtime(end).date()
    if e <= s:
        return 0
    days, d = 0, s
    while d < e:
        d += timedelta(days=1)
        if d.weekday() not in (4, 5):   # Mon=0 … Fri=4, Sat=5 → KSA weekend
            days += 1
    return days


# ─── Commercial-pipeline workflow badge ──────────────────────────────────────
# Order of CostingSheet.workflow_stage along the BOM → Sales → Finance pipeline,
# plus the label + badge style shown on the Commercial Pipeline (projects) list.
# Index in the sequence = how far along; `most_advanced_stage` uses it to pick a
# single badge for a project that has several costing sheets.
WORKFLOW_STAGE_SEQUENCE = [
    'bom_not_started', 'bom_in_progress', 'ready_for_costing', 'costing_in_progress',
    'finalized', 'finance_review', 'finance_approved',
]
PIPELINE_STAGE_LABELS = {
    'bom_not_started':     ('BOM not started',   'bg-light text-dark'),
    'bom_in_progress':     ('BOM in progress',   'bg-secondary'),
    'ready_for_costing':   ('Handed to Sales',   'bg-info text-dark'),
    'costing_in_progress': ('Sales costing',     'bg-primary'),
    'finalized':           ('Sales finalised',   'bg-warning text-dark'),
    'finance_review':      ('Handed to Finance', 'bg-dark'),
    'finance_approved':    ('Finance approved',  'bg-success'),
}

# Short badge (label + Bootstrap style) shown per-sheet on the Costing list and
# used for the stage-count tabs. Same stages as PIPELINE_STAGE_LABELS but with
# the wording the teams use for their own step.
STAGE_BADGES = {
    'bom_not_started':     ('BOM not started',   'bg-light text-dark border'),
    'bom_in_progress':     ('BOM in progress',   'bg-warning text-dark'),
    'ready_for_costing':   ('Ready for costing',  'bg-info text-dark'),
    'costing_in_progress': ('Costing started',    'bg-primary'),
    'finalized':           ('Sales finalised',    'bg-dark'),
    'finance_review':      ('Finance budgeting',  'bg-secondary'),
    'finance_approved':    ('Finance approved',   'bg-success'),
}


def most_advanced_stage(sheets):
    """Return the furthest-along workflow_stage code among `sheets`, or None.

    `sheets` is any iterable of CostingSheet (already-prefetched lists are fine).
    A project with no costing sheet returns None (renders as "Not started").
    """
    best, best_idx = None, -1
    for s in sheets:
        try:
            idx = WORKFLOW_STAGE_SEQUENCE.index(s.workflow_stage)
        except ValueError:
            continue
        if idx > best_idx:
            best, best_idx = s.workflow_stage, idx
    return best


def pipeline_stage_badge(sheets):
    """Return {'label', 'css'} for the project's furthest stage, or None."""
    code = most_advanced_stage(sheets)
    if code is None:
        return None
    label, css = PIPELINE_STAGE_LABELS[code]
    return {'label': label, 'css': css}


class TermsTemplate(models.Model):
    CATEGORY_CHOICES = [
        ('terms_and_conditions', 'Terms & Conditions'),
        ('exclusions', 'Exclusions'),
        ('payment_terms', 'Payment Terms'),
        ('conclusion', 'Conclusion'),
    ]
    # Which side of the business a term belongs to. Sales terms (costing sheets /
    # quotations) and procurement terms (purchase orders) are kept in separate
    # pickers; 'both' appears in either.
    USAGE_CHOICES = [
        ('sales', 'Sales (Costing / Quotation)'),
        ('procurement', 'Procurement (Purchase Order)'),
        ('both', 'Both'),
    ]
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    usage = models.CharField(max_length=12, choices=USAGE_CHOICES, default='both')
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


def default_remark_columns():
    """Starting columns for a fresh Client Remarks bundle. Column headers are
    editable per-bundle; each column carries a stable `key` that ties every
    row's cell back to its column even after headers are renamed/reordered."""
    return [
        {'key': 'c1', 'name': 'Client Remark'},
        {'key': 'c2', 'name': "Leap's Answer"},
    ]


class ClientRemarkTemplate(models.Model):
    """Named bundle backing the 'Client Remarks & Leap's Answers' PDF section.

    The bundle is a small spreadsheet: `columns` holds an ordered list of
    editable headers (add/remove/rename freely), and `rows` holds the grid
    data. Sheets attach one or more bundles via
    CostingSheet.selected_client_remarks (M2M).

    Shapes:
        columns = [{"key": "c1", "name": "Client Remark"}, ...]
        rows    = [{"cells": {"c1": {"text": "...", "color": "#000000"}, ...}}, ...]

    A cell keyed by a column that no longer exists is simply ignored on
    render; a column with no matching cell renders blank."""
    name = models.CharField(max_length=255)
    columns = models.JSONField(default=default_remark_columns)
    rows = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='client_remark_templates',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def row_count(self):
        return len(self.rows or [])

    @property
    def column_count(self):
        return len(self.columns or [])

    def cell(self, row, key):
        """Return (text, color) for a row's cell under column `key`."""
        cells = (row or {}).get('cells') or {}
        data = cells.get(key) or {}
        return data.get('text', '') or '', data.get('color') or '#000000'


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

    # Handover workflow — tracks where the sheet is in the
    # proposal → sales pipeline. Still editable by both teams at every
    # stage (per existing policy); the stage just gives visibility and
    # drives the filters/notifications.
    WORKFLOW_STAGE_CHOICES = [
        ('bom_not_started',     'BOM — not started'),
        ('bom_in_progress',     'BOM — in progress by Proposal'),
        ('ready_for_costing',   'Ready for costing (handed over to Sales)'),
        ('costing_in_progress', 'Costing — in progress by Sales'),
        ('finalized',           'Finalized by Sales'),
        ('finance_review',      'Finance — budgeting in progress'),
        ('finance_approved',    'Finance approved — released for Procurement'),
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
    # margin = M1, the current structured margin the sheet is built on.
    margin = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('40'))
    # M2/M3/M4 — flat margin scenarios for the internal finance margin analysis
    # (high / medium / low). Blank = scenario not configured.
    margin_high = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='M2 — high margin % scenario (finance approval comparison).')
    margin_medium = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='M3 — medium margin % scenario.')
    margin_low = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='M4 — low margin % scenario.')
    margin_final = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='M5 — final/approved margin % (the one approved for the PO).')
    # Finance budgeting defaults — used ONLY on the finance budget screen; they
    # never affect the costing sheet's own pricing. Blank falls back to the
    # sheet's margin / discount_rate above so the budget starts from the same
    # preset the sales team used.
    budget_margin = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    budget_discount_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    shipping_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    customs_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    finances_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    installation_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    output_currency = models.CharField(max_length=10, default='SAR')
    default_supplier_currency = models.CharField(
        max_length=10, default='SAR',
        help_text='The currency the team is entering costs in. New line items default to this currency; use the "Apply to all" action to bulk-update existing items.',
    )
    # PDF header fields
    customer_name = models.CharField(max_length=255, blank=True)
    end_user = models.CharField(max_length=255, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    telephone = models.CharField(max_length=100, blank=True)
    fax = models.CharField(max_length=100, blank=True)
    # Additional Leap contributors credited on the PDF (above and beyond the
    # auto "Contact Person / Email" row that comes from sheet.created_by).
    additional_contacts = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='additional_contact_costing_sheets',
    )
    # PDF content sections
    terms_and_conditions = models.TextField(blank=True)
    exclusions = models.TextField(blank=True)
    payment_terms = models.TextField(blank=True)
    conclusion = models.TextField(blank=True)
    selected_terms = models.ManyToManyField('TermsTemplate', blank=True, related_name='costing_sheets')
    selected_client_remarks = models.ManyToManyField(
        'ClientRemarkTemplate', blank=True, related_name='costing_sheets',
    )

    # Scope of Work (A.2 section in the commercial offer)
    scope_of_work_total = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0'),
        verbose_name="Scope of Work Total",
        help_text="Total value for A.2 Scope of Work"
    )

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')

    # Workflow stage + handover bookkeeping
    workflow_stage = models.CharField(
        max_length=24, choices=WORKFLOW_STAGE_CHOICES, default='bom_not_started',
    )
    bom_started_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the proposal team started building the BOM.')
    bom_started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costing_sheets_bom_started',
    )
    enforce_stage_barriers = models.BooleanField(
        default=True,
        help_text='When set, editing is locked to the team that owns the current '
                  'workflow stage (KPI mode). Pre-existing sheets are False so they '
                  'keep the older lenient rules.')
    handed_over_at = models.DateTimeField(null=True, blank=True,
        help_text='When the proposal team marked the BOM ready for sales')
    handed_over_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costing_sheets_handed_over',
    )
    handover_note = models.TextField(blank=True)
    costing_started_at = models.DateTimeField(null=True, blank=True,
        help_text='When the sales team picked it up and started pricing')
    costing_started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costing_sheets_costing_started',
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costing_sheets_finalized',
    )
    finance_review_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When sales handed the sheet over to finance for budgeting.',
    )
    finance_review_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costing_sheets_handed_to_finance',
    )
    finance_approved_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When finance approved the sheet, releasing it for procurement.',
    )
    finance_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='costing_sheets_finance_approved',
    )
    finance_note = models.TextField(blank=True, help_text='Notes from the finance team.')

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

    @property
    def stage_badge(self):
        """{'label', 'css'} for this sheet's own workflow stage (list display)."""
        label, css = STAGE_BADGES.get(
            self.workflow_stage,
            (self.get_workflow_stage_display(), 'bg-secondary'))
        return {'label': label, 'css': css}

    # ─── Cycle time (working days per stage) for the management views ───
    def cycle_rows(self):
        """Per-stage turnaround in working days + who did it. Each row:
        {'label', 'team', 'days' (int|None), 'person' (User|None)}.
        A stage with a missing start/end shows days=None (—)."""
        c = self
        stages = [
            ('BOM',            'Proposal', c.bom_started_at or c.created_at, c.handed_over_at,      c.bom_started_by or c.created_by),
            ('Sales pickup',   'Sales',    c.handed_over_at,                 c.costing_started_at,   c.costing_started_by),
            ('Sales finalise', 'Sales',    c.costing_started_at or c.handed_over_at, c.finalized_at, c.finalized_by),
        ]
        rows = []
        for lbl, team, s, e, p in stages:
            wd = working_days_between(s, e)
            rows.append({'label': lbl, 'team': team, 'days': wd,
                         'display': (f'{wd}d' if wd is not None else '—'),
                         'person': p})
        return rows

    def _current_stage_start(self):
        if self.workflow_stage == 'finance_approved':
            return None   # workflow complete — "in current stage" not meaningful
        return {
            'bom_not_started':     self.created_at,
            'bom_in_progress':     self.bom_started_at or self.created_at,
            'ready_for_costing':   self.handed_over_at,
            'costing_in_progress': self.costing_started_at,
            'finalized':           self.finalized_at,
            'finance_review':      self.finance_review_at,
        }.get(self.workflow_stage)

    @property
    def days_in_current_stage(self):
        """Working days the sheet has sat in its current stage (None if done)."""
        from django.utils import timezone
        return working_days_between(self._current_stage_start(), timezone.now())

    @property
    def current_stage_display(self):
        d = self.days_in_current_stage
        return f'{d}d' if d is not None else '—'

    @property
    def current_stage_level(self):
        """Bootstrap severity for the 'in current stage' badge (colour flag)."""
        d = self.days_in_current_stage
        if d is None:
            return ''
        if d > 3:
            return 'danger'
        if d > 1:
            return 'warning text-dark'
        return 'success'

    def milestone_rows(self):
        """Date each milestone happened + who did it, for the management views.
        [{'label', 'date' (str), 'person' (User|None)}] — '—' when not reached."""
        from django.utils import timezone

        def _dt(x):
            return timezone.localtime(x).strftime('%d %b %y') if x else '—'
        c = self
        ms = [
            ('BOM started',     c.bom_started_at,     c.bom_started_by),
            ('Handed to sales', c.handed_over_at,     c.handed_over_by),
            ('Costing started', c.costing_started_at, c.costing_started_by),
            ('Finalised',       c.finalized_at,       c.finalized_by),
        ]
        return [{'label': lbl, 'date': _dt(x), 'person': p} for lbl, x, p in ms]

    @property
    def total_cycle_days(self):
        """Working days from pipeline creation to Sales finalisation (or to now
        if not yet finalised)."""
        from django.utils import timezone
        end = self.finalized_at or timezone.now()
        return working_days_between(self.created_at, end)

    @property
    def total_cycle_display(self):
        d = self.total_cycle_days
        return f'{d}d' if d is not None else '—'

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

    # Exchange-rate cache hook so list views can pre-load rates once and
    # avoid an N+1 query when iterating contract_total over many sheets.
    def set_rates_cache(self, rates_dict):
        self._rates_cache = rates_dict

    def _conv_to_output_currency(self):
        """Return the SAR → output_currency multiplier for this sheet."""
        rates = getattr(self, '_rates_cache', None)
        if rates is None:
            rates = {r.currency_code: r.rate_to_usd
                     for r in ExchangeRate.objects.all()}
            self._rates_cache = rates
        target = (self.output_currency or 'SAR').upper()
        if target == 'SAR':
            return Decimal('1')
        sar_rate = rates.get('SAR')
        tgt_rate = rates.get(target)
        if not sar_rate or not tgt_rate:
            return Decimal('1')
        return tgt_rate / sar_rate

    @property
    def sow_total(self):
        """Sum of Scope-of-Work / Services items, in this sheet's output
        currency. Falls back to the legacy flat scope_of_work_total field
        when no per-row items exist.
        """
        items = list(self.scope_of_work_items.all())
        if items:
            return sum((i.total_price for i in items), Decimal('0'))
        return self.scope_of_work_total or Decimal('0')

    @property
    def contract_total(self):
        """Contract total in the sheet's output currency.

        Mirrors the "MAIN — TOTAL CONTRACT PRICE" line on the costing
        PDF: A.1 (grand_total) converted to the output currency, plus
        A.2 (sow_total) which is already in the output currency.

        Display-only — the underlying grand_total / SOW values are not
        rewritten. Pre-load exchange rates with set_rates_cache() when
        accessed across many sheets.
        """
        conv = self._conv_to_output_currency()
        grand_in_oc = (self.grand_total * conv).quantize(Decimal('0.01'))
        return (grand_in_oc + Decimal(str(self.sow_total))).quantize(Decimal('0.01'))

    @property
    def contract_total_currency(self):
        return (self.output_currency or 'SAR').upper()

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

    # ── Finance margin analysis (internal) ──────────────────────
    # Bounds mirror CostingLineItem so the selling-price formula stays sane.
    _SCENARIO_MIN = Decimal('0')
    _SCENARIO_MAX = Decimal('0.99')

    def _consolidated_cost_groups(self):
        """Group non-optional sections by consolidated title (same key the
        commercial offer uses) and accumulate, in SAR, the margin-0 cost and the
        current structured (M1) selling price for each."""
        from collections import OrderedDict
        groups = OrderedDict()
        for section in self.sections.all():
            if section.is_optional or not section.section_number:
                continue
            key = (section.title or '').strip().upper() or '—'
            g = groups.setdefault(key, {
                'title': (section.title or '—').strip(),
                'cost': Decimal('0'), 'price_m1': Decimal('0')})
            for item in section.line_items.all():
                g['cost'] += item.unit_cost_sar * item.quantity
                g['price_m1'] += item.base_total_price
        return groups

    def margin_scenarios(self):
        """Cost / Price / Profit per consolidated system for four margin
        scenarios — M1 (current structure), and the flat M2/M3/M4 — for the
        internal finance approval comparison. All figures in SAR; profit is the
        pure margin (add-ons excluded). Price = Cost / (1 - margin).
        """
        groups = self._consolidated_cost_groups()

        # A.2 Services / Scope of Work — billed at cost (no margin applied), so
        # it adds equally to grand cost and grand price (zero profit). sow_total
        # is in the sheet's output currency; convert back to SAR for this view.
        conv = self._conv_to_output_currency()
        services = ((Decimal(str(self.sow_total)) / conv).quantize(Decimal('0.01'))
                    if conv else Decimal('0'))

        def _clamp(margin_pct):
            m = Decimal(str(margin_pct)) / Decimal('100')
            return max(self._SCENARIO_MIN, min(self._SCENARIO_MAX, m))

        def _scenario(key, label, margin_pct, structured):
            configured = structured or margin_pct is not None
            rows, tot_cost, tot_price = [], Decimal('0'), Decimal('0')
            if configured:
                m = None if structured else _clamp(margin_pct)
                for g in groups.values():
                    cost = g['cost'].quantize(Decimal('0.01'))
                    if structured:
                        price = g['price_m1'].quantize(Decimal('0.01'))
                    else:
                        price = (g['cost'] / (Decimal('1') - m)).quantize(Decimal('0.01'))
                    rows.append({'system': g['title'], 'cost': cost,
                                 'price': price, 'profit': (price - cost).quantize(Decimal('0.01'))})
                    tot_cost += cost
                    tot_price += price
            total_profit = tot_price - tot_cost
            # Effective blended margin = profit / price (since price = cost/(1-m)).
            # For flat scenarios this equals the flat margin; for M1 it's the
            # real blended margin across the (possibly mixed) per-item margins.
            eff = ((total_profit / tot_price * Decimal('100')).quantize(Decimal('0.01'))
                   if tot_price else Decimal('0'))
            # Grand total = supply (A.1, with margin) + services (A.2, no margin).
            grand_cost = (tot_cost + services).quantize(Decimal('0.01'))
            grand_price = (tot_price + services).quantize(Decimal('0.01'))
            return {
                'key': key, 'label': label,
                'margin_pct': (None if structured else margin_pct),
                'structured': structured, 'configured': configured,
                'effective_margin_pct': eff,
                'rows': rows,
                'total_cost': tot_cost.quantize(Decimal('0.01')),
                'total_price': tot_price.quantize(Decimal('0.01')),
                'total_profit': total_profit.quantize(Decimal('0.01')),
                'services': services if configured else Decimal('0.00'),
                'grand_cost': grand_cost if configured else Decimal('0.00'),
                'grand_price': grand_price if configured else Decimal('0.00'),
                'grand_profit': total_profit.quantize(Decimal('0.01')),  # services add 0 profit
            }

        return [
            _scenario('M1', 'M1 — Current', self.margin, True),
            _scenario('M2', 'M2 — High', self.margin_high, False),
            _scenario('M3', 'M3 — Medium', self.margin_medium, False),
            _scenario('M4', 'M4 — Low', self.margin_low, False),
            _scenario('M5', 'M5 — Final (Approved)', self.margin_final, False),
        ]


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

    # Finance budgeting — the cost finance approves to spend on this service,
    # set on the finance budget screen. Blank = not yet budgeted (defaults to
    # the sales cost above). budget_margin / budget_discount are budget-only
    # overrides (blank = use the sheet's budget default).
    budgeted_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    budget_margin = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    budget_discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Manual budgeted-price override. Blank = derive from cost + margin/discount.
    budget_price = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    budget_remarks = models.TextField(blank=True)

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

    # Finance budgeting — the SAR cost finance approves to spend on this line,
    # set on the finance budget screen. Blank = not yet budgeted (defaults to
    # the line's base cost, base_total_cost_sar). budget_margin / budget_discount
    # are budget-only overrides (blank = use the sheet's budget default). None of
    # these affect the costing sheet's own pricing.
    budgeted_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    budget_margin = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    budget_discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Manual budgeted-price override. Blank = derive from cost + margin/discount.
    budget_price = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    budget_remarks = models.TextField(blank=True)

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
    def total_cost_sar(self):
        """Total line cost in SAR = unit cost (SAR) × quantity — the sales-side
        cost finance budgets against."""
        return (self.unit_cost_sar * self.quantity).quantize(Decimal('0.01'))

    @property
    def base_total_cost_sar(self):
        """Base (pre-discount) line cost in SAR × quantity — the budget's
        default 'Cost', so applying the preset discount + margin reproduces the
        costing price before add-ons."""
        return (self.base_unit_cost * self.exchange_rate_to_sar
                * self.quantity).quantize(Decimal('0.01'))

    def budget_line_price(self):
        """The finance-approved budgeted price for this line (total for the
        quantity), matching the finance budget screen. This is what procurement
        receives — the costing sheet itself is not exposed to them.

        Mirrors finance.views.sheet_budget: a manual price override wins; else
        the price is anchored to the costing sales price and scaled by the
        budgeted cost + the (per-line → sheet → costing) margin/discount.
        """
        if self.budget_price is not None:
            return self.budget_price
        sheet = self.sheet
        base_cost = self.base_total_cost_sar
        budgeted_cost = self.budgeted_cost if self.budgeted_cost is not None else base_cost
        cost_disc = self.effective_discount_pct * Decimal('100')
        cost_margin = self.effective_margin * Decimal('100')
        disc = (self.budget_discount if self.budget_discount is not None
                else (sheet.budget_discount_rate if sheet.budget_discount_rate is not None else cost_disc))
        margin = (self.budget_margin if self.budget_margin is not None
                  else (sheet.budget_margin if sheet.budget_margin is not None else cost_margin))

        def factor(d, m):
            return (Decimal('1') - d / Decimal('100')) / (Decimal('1') - min(m, Decimal('99')) / Decimal('100'))

        f_cost = factor(cost_disc, cost_margin)
        if base_cost and f_cost:
            return (self.base_total_price * (budgeted_cost / base_cost)
                    * (factor(disc, margin) / f_cost)).quantize(Decimal('0.01'))
        return (budgeted_cost * factor(disc, margin)).quantize(Decimal('0.01'))

    def budget_unit_price(self):
        """Budgeted price per unit = budgeted line price ÷ quantity (so a PO's
        rate × qty reproduces the budgeted line total)."""
        qty = self.quantity or Decimal('1')
        if not qty:
            return Decimal('0')
        return (self.budget_line_price() / qty).quantize(Decimal('0.01'))

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


class CostingSheetRevision(models.Model):
    """A saved export of a costing sheet at a point in time.

    Auto-created every time the user exports the sheet as PDF or Excel,
    so past versions are preserved. Users can preview or delete specific
    revisions from the detail page. Also captures a state snapshot and
    an auto-computed diff vs the previous revision.
    """

    FORMAT_CHOICES = [
        ('pdf',   'PDF'),
        ('excel', 'Excel'),
    ]

    sheet = models.ForeignKey(
        CostingSheet,
        on_delete=models.CASCADE,
        related_name='pdf_revisions',
    )
    revision_label = models.CharField(
        max_length=16,
        help_text='Auto-assigned label like R00, R01, R02, ...',
    )
    export_format = models.CharField(
        max_length=10, choices=FORMAT_CHOICES, default='pdf',
        help_text='Which export produced this revision',
    )
    file = models.FileField(upload_to='costing/revisions/')
    original_filename = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    snapshot = models.JSONField(
        null=True, blank=True,
        help_text='Key sheet state at save time (for change tracking)',
    )
    change_summary = models.CharField(
        max_length=500, blank=True,
        help_text='Short one-line summary of changes vs previous revision',
    )
    change_details = models.JSONField(
        null=True, blank=True,
        help_text='Full list of field-level changes vs previous revision',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='costing_pdf_revisions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('sheet', 'revision_label')

    def __str__(self):
        return f'{self.sheet.title} — {self.revision_label}'

    @classmethod
    def next_label_for(cls, sheet):
        """Return the next R-label (R00 if none yet). Shared sequence
        across PDF and Excel exports so the order reads naturally."""
        last = cls.objects.filter(sheet=sheet).order_by('-created_at').first()
        if not last:
            return 'R00'
        label = last.revision_label or ''
        if label.startswith('R') and label[1:].isdigit():
            return f'R{int(label[1:]) + 1:02d}'
        return f'R{cls.objects.filter(sheet=sheet).count():02d}'


class VendorQuote(models.Model):
    """A supplier/vendor quote file uploaded against a costing sheet.

    Used by the sales team to keep vendor pricing documents alongside
    the sheet they informed. Standalone from line items — this is just
    a reference archive (PDF / Word / Excel / image).
    """
    sheet = models.ForeignKey(
        CostingSheet,
        on_delete=models.CASCADE,
        related_name='vendor_quotes',
    )
    SOURCE_CHOICES = [
        ('manual', 'Manually uploaded'),
        ('email', 'Auto-detected from email'),
    ]

    vendor_name = models.CharField(max_length=255)
    quote_reference = models.CharField(max_length=100, blank=True)
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default='manual',
        help_text='Whether this was uploaded by hand or auto-attached by the vendor-email matcher.',
    )
    file = models.FileField(upload_to='costing/vendor_quotes/')
    original_filename = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text='Optional quoted amount (for reference)',
    )
    currency = models.CharField(max_length=10, blank=True, default='SAR')
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='vendor_quotes_uploaded',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.vendor_name} — {self.sheet.title}'

    @property
    def extension(self):
        import os
        _, ext = os.path.splitext((self.original_filename or self.file.name).lower())
        return ext.lstrip('.')


class CostingSheetChangeLog(models.Model):
    """Audit trail of edits made to a costing sheet — used to keep the
    proposal team and the sales team in sync when they work on the same
    sheet concurrently. Each entry records who made the change, what
    changed (field name), the before/after values, and which team the
    actor belongs to so the opposite team can be notified automatically.
    """

    TEAM_CHOICES = [
        ('proposal', 'Proposal'),
        ('sales',    'Sales'),
        ('other',    'Other'),
    ]

    SCOPE_CHOICES = [
        ('sheet',   'Sheet'),
        ('section', 'Section'),
        ('item',    'Line item'),
    ]

    sheet = models.ForeignKey(
        CostingSheet,
        on_delete=models.CASCADE,
        related_name='change_log',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='costing_changes',
    )
    actor_team = models.CharField(max_length=16, choices=TEAM_CHOICES, default='other')
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, default='sheet')
    scope_label = models.CharField(
        max_length=255, blank=True,
        help_text='Short human description of the thing changed (e.g. "Section 2.1 — Camera")',
    )
    field = models.CharField(max_length=64, blank=True)
    before = models.TextField(blank=True)
    after = models.TextField(blank=True)
    is_pricing_change = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.actor.username if self.actor else '?'
        return f'{who} · {self.scope_label or self.scope} · {self.field}'
