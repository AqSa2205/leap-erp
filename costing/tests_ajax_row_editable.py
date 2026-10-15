"""The three AJAX row endpoints must return an *editable* row.

Every editable cell in ``_section_item_row.html`` is gated on ``can_edit``.
The lazy-load endpoint (``ajax_section_items``) has passed it since the gate
was introduced in #224; "Add row", "Insert row below" and "Paste from Excel"
did not, so a row created through any of them came back as dead text in Item
No / Description / Vendor / Make / Model / Qty / Unit, and one ``<td>`` short
because the trailing action cell is gated too.  The row existed in the
database, so refreshing the page — which reloads the section through
``ajax_section_items`` — made it editable.  That is the "can't add a line item
until I refresh" report.

All three endpoints 403 before rendering when the user cannot edit, so
``can_edit`` is only ever True at those render sites; the template was being
handed a *missing* variable, not a false one.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from costing.models import CostingSheet, CostingSection, CostingLineItem


class AjaxRowEditableTests(TestCase):

    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('row_sa', password='x', role=role)
        self.client.force_login(self.user)
        self.sheet = CostingSheet.objects.create(
            title='S', created_by=self.user, margin=Decimal('30'))
        self.sec = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='1', title='A', order=0)
        self.anchor = CostingLineItem.objects.create(
            section=self.sec, item_number='1.1', description='x',
            quantity=Decimal('1'), base_unit_cost=Decimal('1'),
            supplier_currency='SAR', order=0)

    def assert_editable(self, html):
        self.assertIn('item-num-edit', html)    # Item No input
        self.assertIn('desc-edit', html)        # Description textarea
        self.assertIn('insert-row-btn', html)   # trailing action cell

    # The path a page refresh takes — the control for the three below.
    def test_lazy_load_renders_editable_cells(self):
        r = self.client.get(
            reverse('costing:section_items', kwargs={'pk': self.sec.pk}))
        self.assertEqual(r.status_code, 200)
        self.assert_editable(r.content.decode())

    def test_add_row_returns_an_editable_row(self):
        r = self.client.post(
            reverse('costing:ajax_add_line_item', kwargs={'pk': self.sec.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sec.line_items.count(), 2)
        self.assert_editable(r.json()['html'])

    def test_insert_row_below_returns_an_editable_row(self):
        r = self.client.post(
            reverse('costing:ajax_insert_line_item', kwargs={'pk': self.anchor.pk}))
        self.assertEqual(r.status_code, 200)
        self.assert_editable(r.json()['html'])

    def test_pasted_rows_are_editable(self):
        r = self.client.post(
            reverse('costing:ajax_paste_line_items', kwargs={'pk': self.sec.pk}),
            {'data': 'Pasted widget\t\t\t2\tEA\t50\tSAR'},
        )
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertTrue(payload.get('ok'), payload)
        self.assertEqual(payload['created'], 1, payload)
        self.assert_editable(payload['html'])

    def test_added_row_has_the_same_cell_count_as_a_refreshed_row(self):
        """A missing trailing action cell also misaligns the row's columns."""
        refreshed = self.client.get(
            reverse('costing:section_items', kwargs={'pk': self.sec.pk})
        ).content.decode().split('</tr>')[0]
        added = self.client.post(
            reverse('costing:ajax_add_line_item', kwargs={'pk': self.sec.pk})
        ).json()['html']
        self.assertEqual(added.count('<td'), refreshed.count('<td'))
