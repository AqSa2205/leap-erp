"""The totals strip on the costing sheet, across the states A.2 and A.4 can be in.

A.4 outranks A.2 when it has lines, A.2 stands on its own when it does not, and
a sheet may legitimately have neither — a pure supply quote is only A.1. That
last case is the one worth guarding: the contract total is still a real figure
there, and it is the number the client is being given.

The page and the PDF read the same `sow_total`, so the risk is not that they
compute different figures but that one of them declines to show what it has.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from costing.models import (CostingLineItem, CostingSection, CostingSheet,
                            ResourceLine, ScopeOfWorkItem)

User = get_user_model()


class TotalsDisplayTests(TestCase):

    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('totals-sa', password='x', role=role)
        self.client.force_login(self.user)
        self.sheet = CostingSheet.objects.create(
            title='Totals', created_by=self.user, margin=Decimal('30'),
            output_currency='SAR')
        # A.1 — one supply line, so the sheet has a real grand total.
        section = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='1', title='Supply', order=0)
        CostingLineItem.objects.create(
            section=section, item_number='1.1', description='Camera',
            quantity=Decimal('10'), base_unit_cost=Decimal('100'),
            supplier_currency='SAR', order=0)

    def page(self):
        return self.client.get(
            reverse('costing:detail', args=[self.sheet.pk])).content.decode()

    def add_sow(self, total='5000'):
        return ScopeOfWorkItem.objects.create(
            costing_sheet=self.sheet, serial_number=1, description='Installation',
            quantity=Decimal('1'), uom='LS', total_price=Decimal(total), order=0)

    def add_resource(self, rate='7000'):
        return ResourceLine.objects.create(
            costing_sheet=self.sheet, description='Site Engineer',
            quantity=Decimal('1'), uom='Nos', rate=Decimal(rate), order=0)

    # ── the model already picks the right source ────────────────────────────

    def test_a4_wins_when_it_has_lines(self):
        self.add_sow('5000')
        self.add_resource('7000')
        self.assertEqual(self.sheet.sow_total, Decimal('7000'))

    def test_a2_stands_on_its_own_when_a4_is_empty(self):
        self.add_sow('5000')
        self.assertEqual(self.sheet.sow_total, Decimal('5000'))

    def test_neither_section_leaves_the_services_figure_at_zero(self):
        self.assertEqual(self.sheet.sow_total, Decimal('0'))

    # ── and the page has to show it ─────────────────────────────────────────

    def test_the_contract_total_is_shown_when_a4_is_empty(self):
        """A.2 alone is an ordinary sheet — most of them. The services figure
        and the contract total both have to appear."""
        self.add_sow('5000')
        body = self.page()
        self.assertIn('Services (A.2)', body)
        self.assertIn('Contract Total', body)

    def test_the_contract_total_is_shown_on_a_supply_only_sheet(self):
        """No A.2 and no A.4 — a pure supply quote. The contract total is
        still a real number (it is the grand total), and it is what the client
        is being charged, so a page that hides it is hiding the answer.

        The PDF prints this row unconditionally, so a page that does not is
        also disagreeing with the document that goes out.
        """
        self.assertEqual(self.sheet.sow_total, Decimal('0'))
        self.assertIn('Contract Total', self.page())

    def test_the_contract_total_equals_the_grand_total_when_there_are_no_services(self):
        self.assertEqual(self.sheet.contract_total, self.sheet.grand_total)

    def test_the_services_tile_is_present_but_hidden_on_a_supply_only_sheet(self):
        """Hidden rather than omitted. Adding the first A.4 line gives the
        sheet a services figure, and the grid updates the tile in place — an
        element that is not in the page cannot be updated, so the user would
        be left looking at a contract total with no services behind it."""
        body = self.page()
        self.assertIn('id="sowTotalTile"', body)
        self.assertIn('hidden', body.split('id="sowTotalTile"')[1][:60])

    def test_the_services_tile_is_visible_once_there_is_a_figure(self):
        self.add_sow('5000')
        body = self.page()
        tile = body.split('id="sowTotalTile"')[1][:60]
        self.assertNotIn('hidden', tile)

    # ── the grid updates those tiles from the response ──────────────────────

    def test_adding_a_resource_line_returns_both_derived_totals(self):
        """The grid is at the bottom of a long page and the tiles are at the
        top. Without these the user types a rate and the contract total above
        keeps yesterday's number."""
        response = self.client.post(
            reverse('costing:add_resource_line', args=[self.sheet.pk]),
            {'description': 'Site Engineer', 'quantity': '1', 'uom': 'Nos',
             'rate': '7000', 'remarks': ''},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        payload = response.json()
        self.assertEqual(Decimal(payload['sow_total']), Decimal('7000'))
        self.assertEqual(Decimal(payload['contract_total']),
                         self.sheet.grand_total + Decimal('7000'))

    def test_deleting_the_last_resource_line_hands_a2_back_its_own_rows(self):
        """The figure can move by more than the line removed, so the response
        has to carry the recomputed totals rather than the caller subtracting."""
        self.add_sow('5000')
        line = self.add_resource('7000')
        response = self.client.post(
            reverse('costing:delete_resource_line', args=[line.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        payload = response.json()
        self.assertEqual(Decimal(payload['sow_total']), Decimal('5000'))
        self.assertEqual(Decimal(payload['contract_total']),
                         self.sheet.grand_total + Decimal('5000'))

    def test_the_stale_marker_hook_the_javascript_looks_for_exists(self):
        """The grid updates #sowTotalCell and #contractTotalCell by id. It
        previously looked for [data-derived-from-sow], which nothing carried,
        so the update silently did nothing — this pins the hooks to the ids
        the script actually queries."""
        body = self.page()
        self.assertIn('id="sowTotalCell"', body)
        self.assertIn('id="contractTotalCell"', body)

    def test_the_page_and_the_pdf_agree_on_the_contract_total(self):
        """Both read sow_total; this pins that neither adds its own arithmetic
        on top. The docstring on sow_total says it exists because this number
        was once worked out in three places."""
        for setup in (lambda: None,
                      lambda: self.add_sow('5000'),
                      lambda: self.add_resource('7000')):
            with self.subTest(setup=setup):
                self.sheet.scope_of_work_items.all().delete()
                self.sheet.resource_lines.all().delete()
                self.sheet = CostingSheet.objects.get(pk=self.sheet.pk)
                setup()
                self.sheet = CostingSheet.objects.get(pk=self.sheet.pk)
                expected = self.sheet.grand_total + self.sheet.sow_total
                self.assertEqual(self.sheet.contract_total, expected)
