"""Remaining Budget tile on the procurement tracker.

Remaining = the approved Budget Total minus POs raised against this budget:
ex-VAT, in SAR, cancelled POs excluded, drafts reported separately and not
deducted. An overrun shows as a negative figure with a minus sign in red;
zero or positive shows in green.
"""
import re
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from costing.models import CostingLineItem, CostingSection, CostingSheet
from procurement.models import PurchaseOrder, PurchaseOrderItem
from projects.models import Project, ProjectStatus, Region


class RemainingBudgetTileTests(TestCase):

    def setUp(self):
        self.proc_role, _ = Role.objects.get_or_create(name=Role.PROCUREMENT_MGR)
        self.region = Region.objects.create(name='RB Arabia', code='RBLNA')
        self.won = ProjectStatus.objects.create(name='RB Won', category='won')
        self.project = Project.objects.create(
            project_name='RB Project', status=self.won, region=self.region)
        self.sheet, self.item = self.budget(self.project, 'RB Sheet')
        self.proc = User.objects.create_user(
            'rb_proc', password='pw', role=self.proc_role, region=self.region)
        self.url = reverse('procurement:bom_procurement_tracker',
                           kwargs={'sheet_pk': self.sheet.pk})
        self.counter = 0

    # --- helpers -----------------------------------------------------------

    def budget(self, project, title):
        sheet = CostingSheet.objects.create(
            title=title, project=project, margin=Decimal('30'),
            discount_rate=Decimal('0'), workflow_stage='finance_approved')
        sec = CostingSection.objects.create(
            costing_sheet=sheet, section_number='A.1', title='CCTV', order=0)
        item = CostingLineItem.objects.create(
            section=sec, item_number='1', description='Cam', quantity=Decimal('2'),
            unit='EA', make='Bosch', model_number='X1', vendor_name='Acme',
            base_unit_cost=Decimal('100'), supplier_currency='SAR')
        return sheet, item

    def po(self, status, rate, line='default', qty='1', project=None, **extra):
        """One PO with one line (or none when line=None), linked to `line`."""
        self.counter += 1
        po = PurchaseOrder.objects.create(
            po_number=f'RB-{self.counter}', po_date=timezone.localdate(),
            status=status, vendor_name='V', po_issued_by='T', currency='SAR',
            discount_rate=Decimal('0'), project=project or self.project, **extra)
        PurchaseOrderItem.objects.create(
            purchase_order=po, serial_number=1, description='x',
            quantity=Decimal(qty), rate_per_unit=Decimal(rate),
            source_bom_item=self.item if line == 'default' else line)
        return po

    def page(self):
        self.client.force_login(self.proc)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        return r

    def tile_html(self, r):
        html = r.content.decode()
        m = re.search(r'Remaining Budget \(SAR\)</div><div class="stat-value ([^"]*)"[^>]*>([^<]*)<', html)
        self.assertIsNotNone(m, 'Remaining Budget tile not found on the tracker')
        return m.group(1), m.group(2).strip()

    # --- the figure --------------------------------------------------------

    def test_no_pos_leaves_the_whole_budget_in_green(self):
        r = self.page()
        self.assertEqual(r.context['remaining_budget'], r.context['budget_total'])
        css, _ = self.tile_html(r)
        self.assertIn('text-success', css)
        self.assertNotContains(r, 'Drafts not counted')

    def test_an_issued_po_is_deducted(self):
        self.po('issued', '100')
        r = self.page()
        self.assertEqual(r.context['remaining_budget'],
                         r.context['budget_total'] - Decimal('100'))
        self.assertIn('text-success', self.tile_html(r)[0])

    def test_every_committed_status_is_deducted(self):
        for status in ('issued', 'supplier_acknowledged', 'client_acknowledged', 'completed'):
            self.po(status, '10')
        r = self.page()
        self.assertEqual(r.context['remaining_budget'],
                         r.context['budget_total'] - Decimal('40'))

    def test_overrun_is_negative_with_a_minus_sign_in_red(self):
        total = self.page().context['budget_total']
        self.po('issued', str(total + Decimal('1000')))
        r = self.page()
        self.assertEqual(r.context['remaining_budget'], Decimal('-1000'))
        css, text = self.tile_html(r)
        self.assertIn('text-danger', css)
        self.assertNotIn('text-success', css)
        self.assertEqual(text, '-1,000.00')

    def test_exactly_zero_is_green_not_red(self):
        total = self.page().context['budget_total']
        self.po('issued', str(total))
        r = self.page()
        self.assertEqual(r.context['remaining_budget'], Decimal('0'))
        css, text = self.tile_html(r)
        self.assertIn('text-success', css)
        self.assertEqual(text, '0.00')

    # --- what does not count -------------------------------------------------

    def test_a_draft_is_shown_but_not_deducted(self):
        self.po('draft', '250')
        r = self.page()
        self.assertEqual(r.context['remaining_budget'], r.context['budget_total'])
        self.assertEqual(r.context['budget_drafts'], Decimal('250'))
        self.assertContains(r, 'Drafts not counted: 250.00')

    def test_a_cancelled_po_is_neither_deducted_nor_shown_as_draft(self):
        self.po('cancelled', '300')
        r = self.page()
        self.assertEqual(r.context['remaining_budget'], r.context['budget_total'])
        self.assertNotContains(r, 'Drafts not counted')

    def test_vat_is_not_deducted(self):
        """The budget carries no VAT, so only the ex-VAT value comes off."""
        self.po('issued', '1000', vat_rate=Decimal('15'))
        r = self.page()
        self.assertEqual(r.context['remaining_budget'],
                         r.context['budget_total'] - Decimal('1000'))

    def test_a_po_with_two_lines_on_this_budget_is_counted_once(self):
        po = self.po('issued', '100')
        PurchaseOrderItem.objects.create(
            purchase_order=po, serial_number=2, description='y',
            quantity=Decimal('1'), rate_per_unit=Decimal('50'), source_bom_item=self.item)
        r = self.page()
        self.assertEqual(r.context['remaining_budget'],
                         r.context['budget_total'] - Decimal('150'))

    def test_a_po_against_another_budget_is_not_deducted(self):
        other_project = Project.objects.create(
            project_name='RB Other', proposal_reference='RB-OTHER-001',
            status=self.won, region=self.region)
        _, other_item = self.budget(other_project, 'RB Other Sheet')
        self.po('issued', '400', line=other_item, project=other_project)
        r = self.page()
        self.assertEqual(r.context['remaining_budget'], r.context['budget_total'])

    def test_a_po_on_the_project_but_not_linked_to_the_budget_is_not_deducted(self):
        """Only POs drawing on this budget's lines count - a hand-typed PO
        nobody linked stays out until it is linked (Link to budget)."""
        self.po('issued', '400', line=None)
        r = self.page()
        self.assertEqual(r.context['remaining_budget'], r.context['budget_total'])

    def test_a_procurement_sub_item_po_is_deducted_though_not_in_the_total(self):
        """Sub items procurement adds never move Budget Total, but money spent
        on them is still spent against this budget."""
        sub = CostingLineItem.all_objects.create(
            section=self.item.section, item_number='1.1', description='Cartridge',
            quantity=Decimal('1'), unit='EA', base_unit_cost=Decimal('20'),
            supplier_currency='SAR', added_by_procurement=True, parent_item=self.item)
        before = self.page().context['budget_total']
        self.po('issued', '75', line=sub)
        r = self.page()
        self.assertEqual(r.context['budget_total'], before)
        self.assertEqual(r.context['remaining_budget'], before - Decimal('75'))

    # --- who sees it ---------------------------------------------------------

    def test_non_procurement_user_does_not_get_the_tile(self):
        pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        pm = User.objects.create_user('rb_pm', password='pw', role=pm_role, region=self.region)
        self.po('issued', '100')
        self.client.force_login(pm)
        r = self.client.get(self.url)
        self.assertNotEqual(r.status_code, 200)
        self.assertNotIn(b'Remaining Budget', r.content)

    def test_procurement_user_from_another_region_does_not_get_the_tile(self):
        other = Region.objects.create(name='RB Elsewhere', code='RBELSE')
        outsider = User.objects.create_user(
            'rb_out', password='pw', role=self.proc_role, region=other)
        self.client.force_login(outsider)
        r = self.client.get(self.url)
        self.assertNotEqual(r.status_code, 200)
        self.assertNotIn(b'Remaining Budget', r.content)
