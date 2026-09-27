"""A read-only Project Manager must actually SEE their region's procurement.

The read-only sweep (tests_pm_readonly.py) proves nothing can be changed. This
proves the other half, which a `status_code == 200` assertion cannot: that the
pages are not empty. Four helpers each kept their own copy of the region rule,
three of them never updated for this role, so the PM's own landing dashboard
read zero and both Summary pages showed nothing while the PO list beside them
showed the whole region.

Every test here uses records created by SOMEBODY ELSE, because `created_by` is
the fallback branch of every one of those rules - a fixture the PM created
passes whether the rule is right or wrong.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from procurement.models import (
    DeliveryNote, InventoryReport, POSummaryEntry, PurchaseOrder,
    PurchaseOrderItem,
)


def a_region(code, name):
    from projects.models import Region
    region, _ = Region.objects.get_or_create(code=code, defaults={'name': name})
    return region


def a_project(name, region, reference):
    from projects.models import Project, ProjectStatus
    status, _ = ProjectStatus.objects.get_or_create(
        name='Won', defaults={'category': 'won'})
    return Project.objects.create(
        project_name=name, status=status, region=region,
        proposal_reference=reference)


def a_user(username, role_name, region=None):
    role, _ = Role.objects.get_or_create(name=role_name)
    return User.objects.create_user(
        username, password='pw', role=role, region=region)


class ProjectManagerSeesTheirRegionTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.east = a_region('EAST', 'Eastern')
        cls.west = a_region('WEST', 'Western')
        cls.mine = a_project('Jubail', cls.east, 'REF-E-1')
        cls.theirs = a_project('Riyadh', cls.west, 'REF-W-1')
        # Created by procurement, not by the PM: created_by is the fallback
        # branch of every rule under test, so it would mask all of them.
        cls.author = a_user('proc_author', Role.PROCUREMENT_MGR, cls.east)

        cls.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-RS-EAST',
            vendor_name='ACME', po_issued_by='Someone',
            project=cls.mine, created_by=cls.author)
        PurchaseOrderItem.objects.create(
            purchase_order=cls.po, serial_number=1, description='Cable',
            quantity=Decimal('2'), rate_per_unit=Decimal('100'), system='CCTV')
        PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-RS-WEST',
            vendor_name='ACME', po_issued_by='Someone',
            project=cls.theirs, created_by=cls.author)
        DeliveryNote.objects.create(
            dn_number='DN-RS-EAST', date=date(2026, 1, 1),
            sold_to_company='A Client', delivery_address='1 Somewhere',
            project=cls.mine, created_by=cls.author)
        InventoryReport.objects.create(
            title='East Store', project=cls.mine, created_by=cls.author)

        cls.pm = a_user('pm_scope', Role.PROJECT_MANAGER, cls.east)

    def setUp(self):
        self.client.force_login(self.pm)

    # ── the dashboard ────────────────────────────────────────────────────
    def test_the_dashboard_counts_their_regions_records(self):
        """This is the PM's landing page - the capability this PR grants opens
        it - and it read zero for every tile."""
        ctx = self.client.get(reverse('procurement:dashboard')).context
        self.assertEqual(ctx['po_total'], 1)
        self.assertEqual(ctx['dn_total'], 1)
        self.assertEqual(ctx['inv_total'], 1)
        self.assertEqual(len(ctx['recent_pos']), 1)
        self.assertEqual(len(ctx['recent_dns']), 1)

    def test_the_dashboard_agrees_with_the_list_beside_it(self):
        """The property, rather than the number: a dashboard summarising a
        different set from the page it links to is worse than no dashboard."""
        dash = self.client.get(reverse('procurement:dashboard'))
        listing = self.client.get(reverse('procurement:po_list'))
        self.assertEqual(dash.context['po_total'],
                         listing.context['total_count'])

    def test_the_dashboard_excludes_another_region(self):
        ctx = self.client.get(reverse('procurement:dashboard')).context
        self.assertEqual(ctx['po_total'], 1)   # not 2
        numbers = [po.po_number for po in ctx['recent_pos']]
        self.assertNotIn('PO-RS-WEST', numbers)

    # ── the summaries ────────────────────────────────────────────────────
    def test_the_internal_summary_shows_their_line_items(self):
        ctx = self.client.get(reverse('procurement:summary_internal')).context
        self.assertEqual(ctx['po_count'], 1)
        self.assertTrue(ctx['rows'])

    def test_the_external_summary_shows_their_line_items(self):
        ctx = self.client.get(reverse('procurement:summary_external')).context
        self.assertEqual(ctx['po_count'], 1)
        self.assertTrue(ctx['rows'])

    def test_the_summary_excludes_another_regions_items(self):
        PurchaseOrderItem.objects.create(
            purchase_order=PurchaseOrder.objects.get(po_number='PO-RS-WEST'),
            serial_number=1, description='Not theirs',
            quantity=Decimal('1'), rate_per_unit=Decimal('50'), system='CCTV')
        ctx = self.client.get(reverse('procurement:summary_internal')).context
        self.assertEqual(ctx['po_count'], 1)

    def test_the_summary_export_is_not_empty(self):
        resp = self.client.get(reverse('procurement:summary_internal_export'))
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.content), 0)

    # ── looking must not write ───────────────────────────────────────────
    def test_opening_the_summary_writes_nothing(self):
        """The page bulk_created a POSummaryEntry per line item on GET. For a
        role that may not change anything, browsing must not either - and a
        missing entry already reads as `pending`."""
        before = POSummaryEntry.objects.count()
        self.client.get(reverse('procurement:summary_internal'))
        self.client.get(reverse('procurement:summary_internal_export'))
        self.assertEqual(POSummaryEntry.objects.count(), before)

    def test_procurement_still_gets_its_entries_created(self):
        """The write is how the editable columns come into existence for the
        people who fill them in, so it must stay for them."""
        POSummaryEntry.objects.all().delete()
        self.client.force_login(self.author)
        self.client.get(reverse('procurement:summary_internal'))
        self.assertEqual(POSummaryEntry.objects.count(), 1)

    def test_a_row_with_no_entry_still_renders_as_pending(self):
        POSummaryEntry.objects.all().delete()
        ctx = self.client.get(reverse('procurement:summary_internal')).context
        self.assertTrue(ctx['rows'])
        self.assertEqual(ctx['totals']['pending'], 1)

    # ── POs by project ───────────────────────────────────────────────────
    def test_pos_by_project_reaches_as_far_as_the_po_list(self):
        resp = self.client.get(reverse('procurement:po_by_project'))
        names = [g['project_name'] for g in resp.context['groups']]
        self.assertIn('Jubail', names)
        self.assertNotIn('Riyadh', names)

    def test_a_region_project_with_no_orders_yet_is_on_the_board(self):
        """The case _can_see_all_projects actually decides. A project that
        already has a PO appears through the PO queryset whatever that helper
        returns, so asserting on Jubail alone tests nothing about it - and the
        whole point of the board is the work that has no orders yet.
        """
        from procurement.models import ProcurementProject
        waiting = a_project('Khobar Fit-out', self.east, 'REF-E-EMPTY')
        ProcurementProject.objects.create(project=waiting, added_by=self.author)
        resp = self.client.get(reverse('procurement:po_by_project'))
        names = [g['project_name'] for g in resp.context['groups']]
        self.assertIn('Khobar Fit-out', names)

    def test_another_regions_project_with_no_orders_stays_off_the_board(self):
        from procurement.models import ProcurementProject
        elsewhere = a_project('Riyadh Fit-out', self.west, 'REF-W-EMPTY')
        ProcurementProject.objects.create(project=elsewhere, added_by=self.author)
        resp = self.client.get(reverse('procurement:po_by_project'))
        names = [g['project_name'] for g in resp.context['groups']]
        self.assertNotIn('Riyadh Fit-out', names)


class NobodyElseChangedTests(TestCase):
    """The four helpers were rewritten, so the roles that already worked have
    to be checked, not assumed."""

    @classmethod
    def setUpTestData(cls):
        cls.east = a_region('EAST', 'Eastern')
        cls.west = a_region('WEST', 'Western')
        east_project = a_project('Jubail', cls.east, 'REF-E-2')
        west_project = a_project('Riyadh', cls.west, 'REF-W-2')
        cls.author = a_user('author2', Role.PROCUREMENT_MGR, cls.east)
        for ref, project in (('PO-NC-EAST', east_project),
                             ('PO-NC-WEST', west_project)):
            po = PurchaseOrder.objects.create(
                po_date=date(2026, 1, 1), po_number=ref, vendor_name='ACME',
                po_issued_by='Someone', project=project, created_by=cls.author)
            PurchaseOrderItem.objects.create(
                purchase_order=po, serial_number=1, description='Cable',
                quantity=Decimal('1'), rate_per_unit=Decimal('10'))

    def _totals(self, user):
        self.client.force_login(user)
        dash = self.client.get(reverse('procurement:dashboard')).context
        summary = self.client.get(
            reverse('procurement:summary_internal')).context
        return dash['po_total'], summary['po_count']

    def test_procurement_still_sees_every_region(self):
        self.assertEqual(self._totals(self.author), (2, 2))

    def test_a_super_admin_still_sees_every_region(self):
        boss = a_user('boss_nc', Role.SUPER_ADMIN, self.east)
        self.assertEqual(self._totals(boss), (2, 2))

    def test_a_manager_still_sees_only_their_region(self):
        manager = a_user('mgr_nc', Role.MANAGER, self.east)
        self.assertEqual(self._totals(manager), (1, 1))

    def test_an_admin_still_sees_only_their_region(self):
        admin = a_user('adm_nc', Role.ADMIN, self.east)
        self.assertEqual(self._totals(admin), (1, 1))

    def test_somebody_with_neither_sees_only_what_they_created(self):
        rep = a_user('rep_nc', Role.SALES_REP, self.east)
        self.assertEqual(self._totals(rep), (0, 0))
