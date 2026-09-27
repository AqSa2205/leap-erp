"""The exports must show exactly what the detail page shows.

Delivery notes, inventory reports and FRC reports each had a scoped list and
detail view and an UNSCOPED export: `@login_required` plus a bare
`get_object_or_404(Model, pk=pk)`. So the detail page refused an out-of-region
record with a 404 while the export beside it handed the whole thing to any
authenticated account - a sales rep could read another region's delivery note
by walking the pk.

The tests are written as an agreement between the two responses rather than as
a list of expected status codes, because the defect was never "the export
returns the wrong number" - it was "the export answers a question the detail
page had already refused".
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from procurement.models import DeliveryNote, FRCReport, InventoryReport

# (label, detail route, export routes, factory attribute)
RECORD_TYPES = ('dn', 'inventory', 'frc')

ROUTES = {
    'dn': ('procurement:dn_detail',
           ('procurement:dn_export', 'procurement:dn_export_pdf')),
    'inventory': ('procurement:inventory_detail',
                  ('procurement:inventory_export',
                   'procurement:inventory_export_pdf')),
    'frc': ('procurement:frc_detail',
            ('procurement:frc_export', 'procurement:frc_export_pdf')),
}


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


class ExportsAgreeWithTheDetailPageTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.east = a_region('EAST', 'Eastern')
        cls.west = a_region('WEST', 'Western')
        # The record lives in the WEST; everybody below is in the EAST, so
        # only the roles that see every region should reach it.
        west_project = a_project('Riyadh Job', cls.west, 'REF-W-1')
        cls.owner = a_user('owner', Role.PROCUREMENT_MGR, cls.east)
        cls.records = {
            'dn': DeliveryNote.objects.create(
                dn_number='DN-SCOPE-1', date=date(2026, 1, 1),
                sold_to_company='A Client', delivery_address='1 Somewhere',
                project=west_project, created_by=cls.owner),
            'inventory': InventoryReport.objects.create(
                title='West Store', project=west_project, created_by=cls.owner),
            'frc': FRCReport.objects.create(
                title='West PPE', project=west_project, created_by=cls.owner),
        }

    def _responses(self, kind, user):
        """(detail status, [export statuses]) for one viewer and one record."""
        pk = self.records[kind].pk
        detail_route, export_routes = ROUTES[kind]
        self.client.force_login(user)
        detail = self.client.get(reverse(detail_route, args=[pk]))
        exports = [self.client.get(reverse(r, args=[pk])).status_code
                   for r in export_routes]
        return detail.status_code, exports

    def _assert_agree(self, kind, user, label):
        detail, exports = self._responses(kind, user)
        opened = detail == 200
        for route, status in zip(ROUTES[kind][1], exports):
            self.assertEqual(
                status == 200, opened,
                f'{label}: {kind} detail={detail} but {route}={status} - the '
                f'export disagrees with the page it belongs to')

    # ── the leak ─────────────────────────────────────────────────────────
    def test_a_sales_rep_cannot_export_another_regions_record(self):
        rep = a_user('rep', Role.SALES_REP, self.east)
        for kind in RECORD_TYPES:
            with self.subTest(kind=kind):
                detail, exports = self._responses(kind, rep)
                self.assertEqual(detail, 404)
                self.assertEqual(exports, [404, 404])

    def test_a_manager_cannot_export_outside_their_region(self):
        manager = a_user('mgr', Role.MANAGER, self.east)
        for kind in RECORD_TYPES:
            with self.subTest(kind=kind):
                detail, exports = self._responses(kind, manager)
                self.assertEqual(detail, 404)
                self.assertEqual(exports, [404, 404])

    def test_a_project_manager_cannot_export_outside_their_region(self):
        """They hold PO approval and a regional PO view, which is exactly the
        kind of partial access that made the unscoped export dangerous."""
        pm = a_user('pm', Role.PROJECT_MANAGER, self.east)
        for kind in RECORD_TYPES:
            with self.subTest(kind=kind):
                _detail, exports = self._responses(kind, pm)
                self.assertEqual(exports, [404, 404])

    # ── nothing legitimate is lost ───────────────────────────────────────
    def test_procurement_still_exports_any_region(self):
        for kind in RECORD_TYPES:
            with self.subTest(kind=kind):
                detail, exports = self._responses(kind, self.owner)
                self.assertEqual(detail, 200)
                self.assertEqual(exports, [200, 200])

    def test_a_super_admin_still_exports_any_region(self):
        boss = a_user('boss', Role.SUPER_ADMIN, self.east)
        for kind in RECORD_TYPES:
            with self.subTest(kind=kind):
                detail, exports = self._responses(kind, boss)
                self.assertEqual(detail, 200)
                self.assertEqual(exports, [200, 200])

    def test_a_manager_in_the_records_own_region_still_exports_it(self):
        """The rule is regional, not "procurement only" - narrowing it that
        far would have been a quieter way to break the same thing."""
        manager = a_user('mgr_west', Role.MANAGER, self.west)
        for kind in RECORD_TYPES:
            with self.subTest(kind=kind):
                detail, exports = self._responses(kind, manager)
                self.assertEqual(detail, 200)
                self.assertEqual(exports, [200, 200])

    def test_the_creator_still_exports_their_own_record(self):
        """created_by is the fallback branch of the rule: a record you made is
        yours to export wherever it sits."""
        author = a_user('author', Role.SALES_REP, self.east)
        for kind, record in self.records.items():
            type(record).objects.filter(pk=record.pk).update(created_by=author)
            with self.subTest(kind=kind):
                detail, exports = self._responses(kind, author)
                self.assertEqual(detail, 200)
                self.assertEqual(exports, [200, 200])
        # Leave the fixture as the class set it up.
        for record in self.records.values():
            type(record).objects.filter(pk=record.pk).update(
                created_by=self.owner)

    # ── the property, stated once ────────────────────────────────────────
    def test_every_viewer_gets_the_same_answer_from_both(self):
        viewers = [
            (a_user('v_rep', Role.SALES_REP, self.east), 'sales rep, other region'),
            (a_user('v_mgr', Role.MANAGER, self.east), 'manager, other region'),
            (a_user('v_pm', Role.PROJECT_MANAGER, self.east), 'PM, other region'),
            (a_user('v_mgr_w', Role.MANAGER, self.west), 'manager, same region'),
            (a_user('v_boss', Role.SUPER_ADMIN, self.east), 'super admin'),
            (self.owner, 'procurement'),
        ]
        for user, label in viewers:
            for kind in RECORD_TYPES:
                with self.subTest(viewer=label, kind=kind):
                    self._assert_agree(kind, user, label)

    def test_an_anonymous_visitor_is_sent_to_login_not_handed_a_file(self):
        for kind in RECORD_TYPES:
            pk = self.records[kind].pk
            for route in ROUTES[kind][1]:
                with self.subTest(kind=kind, route=route):
                    resp = self.client.get(reverse(route, args=[pk]))
                    self.assertEqual(resp.status_code, 302)
                    self.assertIn('login', resp.url)

    def test_a_missing_record_is_a_404_for_somebody_allowed_everything(self):
        """Guards against the fix turning "not found" into "not permitted" in
        a way that hides genuine 404s."""
        boss = a_user('boss404', Role.SUPER_ADMIN, self.east)
        self.client.force_login(boss)
        for kind in RECORD_TYPES:
            for route in ROUTES[kind][1]:
                with self.subTest(kind=kind, route=route):
                    self.assertEqual(
                        self.client.get(reverse(route, args=[999999])).status_code,
                        404)

class TheRuleItselfTests(TestCase):
    """The helpers are unit-tested as well as exercised through the views.

    The anonymous branch cannot be reached through any URL today - every view
    that uses these is behind login_required or LoginRequiredMixin - so a
    view-level test cannot pin it. It is pinned here because _visible_pos_for,
    the helper these mirror, IS called with an anonymous user: the sidebar
    badge runs from a context processor on every page, login screen included,
    and AnonymousUser has no is_super_admin_user to read.
    """

    @classmethod
    def setUpTestData(cls):
        west = a_region('WEST', 'Western')
        owner = a_user('owner_rule', Role.PROCUREMENT_MGR, west)
        DeliveryNote.objects.create(
            dn_number='DN-RULE-1', date=date(2026, 1, 1),
            sold_to_company='A Client', delivery_address='1 Somewhere',
            project=a_project('Riyadh', west, 'REF-RULE-1'), created_by=owner)

    def test_an_anonymous_user_sees_nothing_rather_than_raising(self):
        from django.contrib.auth.models import AnonymousUser
        from procurement.views import (
            _visible_dns_for, _visible_frc_for, _visible_inventory_for)
        for helper in (_visible_dns_for, _visible_inventory_for, _visible_frc_for):
            with self.subTest(helper=helper.__name__):
                self.assertEqual(helper(AnonymousUser()).count(), 0)

    def test_procurement_sees_everything(self):
        from procurement.views import _visible_dns_for
        owner = User.objects.get(username='owner_rule')
        self.assertEqual(_visible_dns_for(owner).count(), 1)
