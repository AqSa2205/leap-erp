"""Procurement's ability to add sub items (e.g. printer cartridges under a
printer) to a finance-approved budget, without ever moving the approved
figure, and with quantities restricted to whole numbers."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from costing.models import CostingLineItem, CostingSection, CostingSheet
from procurement.models import PurchaseOrder

User = get_user_model()


class SubLineItemCreationTests(TestCase):
    def setUp(self):
        sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.super_admin = User.objects.create_user('sub_sa', password='x', role=sa_role)

        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        self.finance = User.objects.create_user('sub_fin', password='x', role=finance_role)

        self.sheet = CostingSheet.objects.create(
            title='Sub-item test sheet', workflow_stage='finance_approved')
        self.section = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='A.1', title='Scope of Supply')
        self.parent = CostingLineItem.objects.create(
            section=self.section, item_number='1', description='Printer',
            quantity=Decimal('2'), unit='EA')

    def _post(self, user, **overrides):
        self.client.force_login(user)
        data = {
            'description': 'Cartridge (Black)', 'quantity': '3',
            'unit': 'EA', 'rate_per_unit': '50.00', 'vendor_name': 'Ink Co',
        }
        data.update(overrides)
        return self.client.post(
            reverse('procurement:add_sub_line_item', args=[self.parent.pk]), data)

    def test_super_admin_can_add_a_sub_item(self):
        self._post(self.super_admin)
        sub = self.parent.sub_items.get()
        self.assertEqual(sub.description, 'Cartridge (Black)')
        self.assertEqual(sub.quantity, Decimal('3'))
        self.assertEqual(sub.parent_item_id, self.parent.pk)
        self.assertEqual(sub.section_id, self.section.pk)
        self.assertTrue(sub.added_by_procurement)
        self.assertEqual(sub.added_by, self.super_admin)
        self.assertIsNotNone(sub.added_at)
        # Rate x quantity, since budget_price is a line total, not a unit price.
        self.assertEqual(sub.budget_price, Decimal('150.00'))

    def test_finance_user_cannot_add_a_sub_item(self):
        self._post(self.finance)
        self.assertEqual(self.parent.sub_items.count(), 0)

    def test_cannot_add_before_the_budget_is_finance_approved(self):
        self.sheet.workflow_stage = 'finance_review'
        self.sheet.save()
        self._post(self.super_admin)
        self.assertEqual(self.parent.sub_items.count(), 0)

    def test_quantity_must_be_a_whole_number(self):
        self._post(self.super_admin, quantity='not-a-number')
        self.assertEqual(self.parent.sub_items.count(), 0)

    def test_fractional_quantity_that_still_rounds_down_to_a_valid_whole_number(self):
        """3.2 has no fractional-quantity error to raise post-rounding - it
        rounds down to a valid 3, so the sub item should still be created."""
        self._post(self.super_admin, quantity='3.2')
        self.assertEqual(self.parent.sub_items.get().quantity, Decimal('3'))

    def test_quantity_must_be_positive(self):
        self._post(self.super_admin, quantity='0')
        self.assertEqual(self.parent.sub_items.count(), 0)

    def test_description_is_required(self):
        self._post(self.super_admin, description='')
        self.assertEqual(self.parent.sub_items.count(), 0)

    def test_negative_rate_is_rejected(self):
        self._post(self.super_admin, rate_per_unit='-5')
        self.assertEqual(self.parent.sub_items.count(), 0)

    def test_no_quantity_cap_is_enforced_for_now(self):
        self.assertIsNone(CostingLineItem.max_sub_item_quantity())
        self._post(self.super_admin, quantity='100000')
        self.assertEqual(self.parent.sub_items.get().quantity, Decimal('100000'))

    def test_sub_item_number_is_derived_from_the_parent(self):
        self._post(self.super_admin)
        self._post(self.super_admin, description='Cartridge (Color)')
        numbers = sorted(self.parent.sub_items.values_list('item_number', flat=True))
        self.assertEqual(numbers, ['1-1', '1-2'])


class SubLineItemBudgetIsolationTests(TestCase):
    """The whole point of the feature: adding a sub item must never move
    the approved budget figure, anywhere it's shown."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.super_admin = User.objects.create_user('iso_sa', password='x', role=sa_role)
        region = Region.objects.create(name='Isolation Region')
        won = ProjectStatus.objects.create(name='Won-Iso', category='won')
        project = Project.objects.create(project_name='Iso P', status=won, region=region)
        self.sheet = CostingSheet.objects.create(
            title='Isolation test sheet', project=project, workflow_stage='finance_approved')
        self.section = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='A.1', title='Scope of Supply')
        self.parent = CostingLineItem.objects.create(
            section=self.section, item_number='1', description='Printer',
            quantity=Decimal('2'), unit='EA', base_unit_cost=Decimal('500'),
            budget_price=Decimal('1200'))

    def _tracker_budget_total(self):
        self.client.force_login(self.super_admin)
        resp = self.client.get(
            reverse('procurement:bom_procurement_tracker', args=[self.sheet.pk]))
        return resp.context['budget_total']

    def _finance_totals(self):
        self.client.force_login(self.super_admin)
        resp = self.client.get(reverse('finance:sheet_budget', args=[self.sheet.pk]))
        return resp.context['cost_total'], resp.context['price_total'], resp.context['sales_total']

    def test_procurement_tracker_budget_total_is_unaffected(self):
        before = self._tracker_budget_total()
        self.client.force_login(self.super_admin)
        self.client.post(
            reverse('procurement:add_sub_line_item', args=[self.parent.pk]),
            {'description': 'Cartridge', 'quantity': '5', 'unit': 'EA', 'rate_per_unit': '99.00'})
        after = self._tracker_budget_total()
        self.assertEqual(before, after)

    def test_finance_budget_totals_are_unaffected(self):
        before = self._finance_totals()
        self.client.force_login(self.super_admin)
        self.client.post(
            reverse('procurement:add_sub_line_item', args=[self.parent.pk]),
            {'description': 'Cartridge', 'quantity': '5', 'unit': 'EA', 'rate_per_unit': '99.00'})
        after = self._finance_totals()
        self.assertEqual(before, after)

    def test_sub_item_still_appears_in_finance_budget_lines(self):
        """Excluded from the totals, but not hidden - finance can still see
        what procurement added."""
        self.client.force_login(self.super_admin)
        self.client.post(
            reverse('procurement:add_sub_line_item', args=[self.parent.pk]),
            {'description': 'Cartridge', 'quantity': '5', 'unit': 'EA', 'rate_per_unit': '99.00'})
        resp = self.client.get(reverse('finance:sheet_budget', args=[self.sheet.pk]))
        descriptions = [row['item'].description for sec in resp.context['sections'] for row in sec['lines']]
        self.assertIn('Cartridge', descriptions)
        self.assertEqual(resp.context['sub_item_count'], 1)

    def test_finance_resubmitting_the_form_does_not_wipe_the_sub_items_price(self):
        """Regression guard: a super admin (the only role who can still post
        to this form after approval) resubmitting the budget form must not
        touch a sub item's price - there was never a form field rendered
        for it, so a naive save-everything loop would null it out."""
        self.client.force_login(self.super_admin)
        self.client.post(
            reverse('procurement:add_sub_line_item', args=[self.parent.pk]),
            {'description': 'Cartridge', 'quantity': '5', 'unit': 'EA', 'rate_per_unit': '99.00'})
        sub = self.parent.sub_items.get()
        self.assertEqual(sub.budget_price, Decimal('495.00'))

        self.client.post(reverse('finance:sheet_budget', args=[self.sheet.pk]), {
            f'line-{self.parent.pk}-budget': '1000',
            f'line-{self.parent.pk}-disc': '', f'line-{self.parent.pk}-margin': '',
            f'line-{self.parent.pk}-price_manual': '0', f'line-{self.parent.pk}-remarks': '',
        })
        sub.refresh_from_db()
        self.assertEqual(sub.budget_price, Decimal('495.00'))


class SubLineItemProcurementFlowTests(TestCase):
    """A sub item is not just visible - it must be genuinely orderable,
    the same as any other budget line item."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('flow_sa', password='x', role=sa_role)
        region = Region.objects.create(name='Flow Region')
        won = ProjectStatus.objects.create(name='Won-Flow', category='won')
        project = Project.objects.create(project_name='Flow P', status=won, region=region)
        self.sheet = CostingSheet.objects.create(
            title='Flow test sheet', project=project, workflow_stage='finance_approved')
        self.section = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='A.1', title='Scope of Supply')
        self.parent = CostingLineItem.objects.create(
            section=self.section, item_number='1', description='Printer',
            quantity=Decimal('2'), unit='EA')
        self.client.force_login(self.user)
        self.client.post(
            reverse('procurement:add_sub_line_item', args=[self.parent.pk]),
            {'description': 'Cartridge', 'quantity': '5', 'unit': 'EA', 'rate_per_unit': '20.00'})
        self.sub = self.parent.sub_items.get()

    def test_sub_item_can_be_picked_for_a_po(self):
        resp = self.client.post(
            reverse('procurement:bom_procurement_tracker', args=[self.sheet.pk]),
            {'item_ids': [str(self.sub.pk)], f'qty_{self.sub.pk}': '5'})
        self.assertEqual(resp.status_code, 302)
        po = PurchaseOrder.objects.get()
        pi = po.items.get(source_bom_item=self.sub)
        self.assertEqual(pi.quantity, Decimal('5'))
