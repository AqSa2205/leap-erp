"""A project's ordered value broken down by system, with delivery progress.

The page reproduces a workbook procurement keeps by hand. Its four percentage
columns use three different denominators, and the arrangement only means
anything while

    % Delivery + Pending % == % of All

holds for every row. Break that and the page still renders perfectly — it just
stops being true — so it is asserted directly rather than left to inspection.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from costing.models import ExchangeRate
from procurement.models import (
    DeliveryNote, DeliveryNoteItem, PurchaseOrder, PurchaseOrderItem,
)
from procurement.system_breakdown import UNASSIGNED, breakdown
from projects.models import Project, ProjectStatus, Region

User = get_user_model()


class SystemBreakdownTests(TestCase):

    def setUp(self):
        from accounts.permissions import seed_default_permissions
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        ExchangeRate.objects.update_or_create(
            currency_code='USD', defaults={'currency_name': 'US Dollar',
                                           'rate_to_usd': Decimal('1')})
        ExchangeRate.objects.update_or_create(
            currency_code='SAR', defaults={'currency_name': 'Saudi Riyal',
                                           'rate_to_usd': Decimal('3.75')})
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.user = User.objects.create_user(
            'sb-super', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.outsider = User.objects.create_user(
            'sb-out', password='x',
            role=Role.objects.get(name=Role.DOCUMENT_CONTROLLER))
        self.project = self._project('Ghazlan', 'LNA-2026-1001')

    def _project(self, name, ref):
        return Project.objects.create(
            project_name=name, proposal_reference=ref, status=self.status,
            region=self.region, estimated_value=Decimal('1'),
            actual_sales=Decimal('0'), year='2026', po_award_quarter='Q2')

    def _po(self, number, *, status='issued', currency='SAR', project=None):
        return PurchaseOrder.objects.create(
            po_date='2026-01-01', po_number=number, vendor_name='ACME',
            po_issued_by='T', project=project or self.project,
            created_by=self.user, status=status, currency=currency)

    def _item(self, po, system, description, qty, rate, serial=1):
        return PurchaseOrderItem.objects.create(
            purchase_order=po, serial_number=serial, system=system,
            description=description, quantity=Decimal(qty),
            rate_per_unit=Decimal(rate), order=serial)

    def _deliver(self, item, qty):
        note = DeliveryNote.objects.create(
            sold_to_company='Client', delivery_address='Somewhere on site, KSA',
            project=self.project, purchase_order=item.purchase_order,
            created_by=self.user)
        return DeliveryNoteItem.objects.create(
            delivery_note=note, description=item.description,
            quantity=Decimal(qty), source_po_item=item)

    def _breakdown(self, pos=None):
        return breakdown(pos if pos is not None else PurchaseOrder.objects.all())

    # ── grouping ────────────────────────────────────────────────────────────

    def test_lines_are_grouped_under_their_system(self):
        po = self._po('PO-1')
        self._item(po, 'CCTV', 'Camera', '2', '100', serial=1)
        self._item(po, 'CCTV', 'Lens', '1', '50', serial=2)
        self._item(po, 'PAGA', 'Speaker', '1', '300', serial=3)
        data = self._breakdown()
        systems = {g['system']: g['total'] for g in data['groups']}
        self.assertEqual(systems, {'CCTV': Decimal('250'), 'PAGA': Decimal('300')})

    def test_systems_are_ordered_largest_first(self):
        """The question is what the project is mostly made of; alphabetical
        order buries the answer."""
        po = self._po('PO-2')
        self._item(po, 'Small', 'A', '1', '10', serial=1)
        self._item(po, 'Large', 'B', '1', '900', serial=2)
        self.assertEqual([g['system'] for g in self._breakdown()['groups']],
                         ['Large', 'Small'])

    def test_a_line_with_no_system_is_grouped_not_dropped(self):
        """Dropping it would lose ordered value from a page whose whole point
        is that the shares add to 100%."""
        po = self._po('PO-3')
        self._item(po, '', 'Uncategorised thing', '1', '500')
        data = self._breakdown()
        self.assertEqual([g['system'] for g in data['groups']], [UNASSIGNED])
        self.assertEqual(data['total'], Decimal('500'))

    def test_uncategorised_sorts_last_however_large(self):
        """It is a gap to close, not a finding about the project."""
        po = self._po('PO-4')
        self._item(po, '', 'Big uncategorised', '1', '9000', serial=1)
        self._item(po, 'CCTV', 'Camera', '1', '10', serial=2)
        self.assertEqual([g['system'] for g in self._breakdown()['groups']][-1],
                         UNASSIGNED)

    # ── which orders count ──────────────────────────────────────────────────

    def test_a_draft_order_is_not_part_of_the_composition(self):
        """Nothing is ordered until it is issued, and a draft's delivery
        progress is not a fact about anything."""
        self._item(self._po('PO-5', status='draft'), 'CCTV', 'Camera', '1', '100')
        self.assertEqual(self._breakdown()['total'], Decimal('0'))

    def test_a_cancelled_order_is_excluded(self):
        self._item(self._po('PO-6', status='cancelled'), 'CCTV', 'Camera', '1', '100')
        self.assertEqual(self._breakdown()['total'], Decimal('0'))

    def test_acknowledged_and_completed_orders_still_count(self):
        for i, status in enumerate(('client_acknowledged', 'completed')):
            self._item(self._po(f'PO-7{i}', status=status), 'CCTV', 'Cam', '1', '100')
        self.assertEqual(self._breakdown()['total'], Decimal('200'))

    # ── the percentages ─────────────────────────────────────────────────────

    def test_percent_of_all_is_the_share_of_the_project(self):
        po = self._po('PO-8')
        self._item(po, 'CCTV', 'Camera', '1', '250', serial=1)
        self._item(po, 'PAGA', 'Speaker', '1', '750', serial=2)
        row = next(r for r in self._breakdown()['rows'] if r['description'] == 'Camera')
        self.assertEqual(row['pct_of_all'], Decimal('25.00'))

    def test_percent_of_system_is_the_share_of_its_own_system(self):
        """A different denominator from % of All — that is the point of having
        both columns."""
        po = self._po('PO-9')
        self._item(po, 'CCTV', 'Camera', '1', '250', serial=1)
        self._item(po, 'CCTV', 'Lens', '1', '250', serial=2)
        self._item(po, 'PAGA', 'Speaker', '1', '500', serial=3)
        row = next(r for r in self._breakdown()['rows'] if r['description'] == 'Camera')
        self.assertEqual(row['pct_of_all'], Decimal('25.00'))
        self.assertEqual(row['pct_of_system'], Decimal('50.00'))

    def test_delivery_and_pending_add_up_to_the_share_of_the_project(self):
        """The identity the whole sheet rests on. Break it and the page still
        renders — it just stops meaning anything."""
        po = self._po('PO-10')
        camera = self._item(po, 'CCTV', 'Camera', '4', '100', serial=1)
        self._item(po, 'PAGA', 'Speaker', '1', '600', serial=2)
        self._deliver(camera, '3')
        for row in self._breakdown()['rows']:
            self.assertEqual(row['pct_delivered'] + row['pct_pending'],
                             row['pct_of_all'], row['description'])

    def test_delivery_is_measured_against_the_project_not_the_line(self):
        """So the column reads straight down the page as progress against the
        whole project."""
        po = self._po('PO-11')
        camera = self._item(po, 'CCTV', 'Camera', '4', '100', serial=1)
        self._item(po, 'PAGA', 'Speaker', '1', '600', serial=2)
        self._deliver(camera, '2')            # 200 of 1000 total
        row = next(r for r in self._breakdown()['rows'] if r['description'] == 'Camera')
        self.assertEqual(row['pct_delivered'], Decimal('20.00'))
        self.assertEqual(row['pct_pending'], Decimal('20.00'))

    def test_an_undelivered_line_is_entirely_pending(self):
        self._item(self._po('PO-12'), 'CCTV', 'Camera', '1', '100')
        row = self._breakdown()['rows'][0]
        self.assertEqual(row['pct_delivered'], Decimal('0.00'))
        self.assertEqual(row['pct_pending'], Decimal('100.00'))

    def test_partial_deliveries_accumulate(self):
        po = self._po('PO-13')
        camera = self._item(po, 'CCTV', 'Camera', '4', '100')
        self._deliver(camera, '1')
        self._deliver(camera, '2')
        self.assertEqual(self._breakdown()['delivered'], Decimal('300.00'))

    def test_nothing_ordered_gives_no_percentages_rather_than_zero(self):
        """0% would say 'none of this delivered'. There is nothing here at
        all, which is a different statement."""
        data = self._breakdown()
        self.assertEqual(data['total'], Decimal('0'))
        self.assertIsNone(data['pct_delivered'])

    # ── awkward data ────────────────────────────────────────────────────────

    def test_over_delivery_is_capped_and_flagged_not_absorbed(self):
        """Uncapped it drives Pending negative and pushes % Delivery past % of
        All, quietly breaking the identity the sheet rests on."""
        po = self._po('PO-14')
        camera = self._item(po, 'CCTV', 'Camera', '2', '100')
        self._deliver(camera, '5')
        data = self._breakdown()
        row = data['rows'][0]
        self.assertEqual(row['delivered_value'], Decimal('200.00'))
        self.assertEqual(row['pending_value'], Decimal('0.00'))
        self.assertTrue(row['over_delivered'])
        self.assertEqual(len(data['over_delivered']), 1)

    def test_a_foreign_currency_order_is_converted_before_being_compared(self):
        """Otherwise a 100 USD line and a 100 SAR line look like equal shares
        of the project when one is nearly four times the other."""
        usd = self._po('PO-15', currency='USD')
        self._item(usd, 'CCTV', 'Camera', '1', '100')
        sar = self._po('PO-16')
        self._item(sar, 'PAGA', 'Speaker', '1', '375')
        data = self._breakdown()
        self.assertEqual(data['total'], Decimal('750.00'))
        self.assertTrue(data['converted'])

    def test_a_delivery_not_linked_to_a_po_line_is_not_attributed(self):
        """It cannot be attributed to anything, and guessing would credit
        delivery against a line that never received it."""
        po = self._po('PO-17')
        self._item(po, 'CCTV', 'Camera', '1', '100')
        note = DeliveryNote.objects.create(
            sold_to_company='Client', delivery_address='Somewhere on site, KSA',
            project=self.project, purchase_order=po, created_by=self.user)
        DeliveryNoteItem.objects.create(delivery_note=note, description='Mystery',
                                        quantity=Decimal('1'), source_po_item=None)
        self.assertEqual(self._breakdown()['delivered'], Decimal('0'))

    # ── the page ────────────────────────────────────────────────────────────

    def test_the_page_renders_the_systems_and_the_summary(self):
        po = self._po('PO-18')
        self._item(po, 'CCTV', 'Camera', '1', '250', serial=1)
        self._item(po, 'PAGA', 'Speaker', '1', '750', serial=2)
        self.client.force_login(self.user)
        body = self.client.get(reverse('procurement:project_systems',
                                       args=[self.project.pk])).content.decode()
        self.assertIn('CCTV', body)
        self.assertIn('25.00%', body)
        self.assertIn('75.00%', body)

    def test_the_page_says_so_when_only_drafts_exist(self):
        """Otherwise an empty table reads as a broken page rather than as
        'nothing has been ordered yet'."""
        self._item(self._po('PO-19', status='draft'), 'CCTV', 'Camera', '1', '100')
        self.client.force_login(self.user)
        body = self.client.get(reverse('procurement:project_systems',
                                       args=[self.project.pk])).content.decode()
        self.assertIn('Draft orders are not counted', body)

    def test_a_viewer_with_no_po_on_the_project_cannot_open_it(self):
        """A page rendered for a project they cannot otherwise see would
        confirm it exists and name it."""
        self._item(self._po('PO-20'), 'CCTV', 'Camera', '1', '100')
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('procurement:project_systems',
                                           args=[self.project.pk]))
        self.assertEqual(response.status_code, 403)

    def test_a_viewer_sees_only_their_own_orders(self):
        """Visibility follows _visible_pos_for, so this page can never show a
        PO the flat list would not."""
        mine = PurchaseOrder.objects.create(
            po_date='2026-01-01', po_number='PO-21', vendor_name='ACME',
            po_issued_by='T', project=self.project, created_by=self.outsider,
            status='issued')
        self._item(mine, 'CCTV', 'Mine', '1', '100')
        self._item(self._po('PO-22'), 'PAGA', 'Somebody else', '1', '900')
        self.client.force_login(self.outsider)
        body = self.client.get(reverse('procurement:project_systems',
                                       args=[self.project.pk])).content.decode()
        self.assertIn('Mine', body)
        self.assertNotIn('Somebody else', body)

    def test_the_board_links_to_it(self):
        self._item(self._po('PO-23'), 'CCTV', 'Camera', '1', '100')
        self.client.force_login(self.user)
        body = self.client.get(reverse('procurement:po_by_project')).content.decode()
        self.assertIn(reverse('procurement:project_systems', args=[self.project.pk]),
                      body)

    def test_the_query_count_does_not_grow_with_the_lines(self):
        """Delivered quantities are looked up for the whole page at once; a
        per-line lookup degrades quietly as a project fills up."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            with CaptureQueriesContext(connection) as ctx:
                breakdown(PurchaseOrder.objects.all().prefetch_related('items'))
            return len(ctx.captured_queries)

        po = self._po('PO-24')
        self._item(po, 'CCTV', 'Camera', '1', '100', serial=1)
        small = queries()
        for i in range(10):
            self._item(po, 'CCTV', f'Extra {i}', '1', '10', serial=i + 2)
        self.assertEqual(small, queries())
