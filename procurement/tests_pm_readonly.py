"""Project Manager gets to see the whole Procurement section, not change it.

Two failure modes matter here, and they are opposite of each other:

1. A page that opens for the PM but a write path underneath it does not
   actually refuse them (the ownership fallback on Update/Delete views reads
   `created_by == user`, and once the PM is a legitimate viewer, nothing
   stops a record from ending up owned by them).
2. A page the PM should see stays empty because its queryset was never
   taught the PM's region counts, the way _visible_pos_for() already knows
   for Purchase Orders (DN/Inventory/FRC did not, until this PR).

Every test below is one specific instance of one of these two shapes, plus a
check that nothing here changed what procurement/admin/super_admin could
already do - a role-scoped fix that quietly widens or narrows someone else's
access is its own bug.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, RolePermission, User
from procurement.models import (
    PurchaseOrder, PurchaseOrderItem, DeliveryNote, InventoryReport,
    FRCReport, FRCInventory,
)


def a_region(code='EAST', name='Eastern'):
    from projects.models import Region
    region, _ = Region.objects.get_or_create(code=code, defaults={'name': name})
    return region


def a_project(name, region, **kw):
    from projects.models import Project, ProjectStatus
    status, _ = ProjectStatus.objects.get_or_create(
        name='Won', defaults={'category': 'won'})
    kw.setdefault('proposal_reference', f'TEST-{name}')
    return Project.objects.create(
        project_name=name, status=status, region=region, **kw)


def a_user(username, role_name, **kw):
    role, _ = Role.objects.get_or_create(name=role_name)
    return User.objects.create_user(username, password='pw', role=role, **kw)


def grant(user, codename, allowed=True):
    user.role.permissions.update_or_create(
        codename=codename, defaults={'allowed': allowed})
    return user


def pm_with_procurement(username='pm', region=None):
    """A project manager the way migration 0038 leaves them: access to the
    section, region set, nothing else changed."""
    pm = a_user(username, Role.PROJECT_MANAGER, region=region or a_region())
    for code in ('procurement.access', 'procurement.nav', 'dn.access', 'dn.nav',
                 'po.access', 'po.nav'):
        grant(pm, code)
    return pm


class ReadEverythingTests(TestCase):
    """Every page this PR opens to the PM has to actually render, not just
    pass the capability gate and then 404/500 inside."""

    def setUp(self):
        self.region = a_region()
        self.pm = pm_with_procurement(region=self.region)
        # A creator distinct from the PM viewer for every fixture: some of
        # these templates print "Created by {{ x.created_by.get_full_name }}"
        # with no null guard, and a null created_by (a real state - the FK is
        # SET_NULL if that account is ever deleted) makes Django's
        # test-render instrumentation raise where production would just
        # print a blank line. Giving every fixture a real creator keeps the
        # tests about this PR's permission logic, not that unrelated,
        # pre-existing template gap.
        creator = a_user('creator0', Role.SUPER_ADMIN)
        self.project = a_project('Jubail Upgrade', self.region)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-RO-1', vendor_name='ACME',
            po_issued_by='Tester', project=self.project, created_by=creator)
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=1, description='Widget',
            quantity=1, rate_per_unit=100, system='CCTV')
        # Fully approved, so the priced Excel export (locked until release)
        # is actually exercised rather than skipped.
        now = timezone.now()
        for stage in ('scm', 'pm', 'coo', 'ceo'):
            setattr(self.po, f'{stage}_approved_at', now)
        self.po.save()
        self.dn = DeliveryNote.objects.create(
            dn_number='DN-RO-1', date=date(2026, 1, 1),
            sold_to_company='A Client', delivery_address='123 Somewhere Street',
            project=self.project, created_by=creator)
        self.inv = InventoryReport.objects.create(
            title='RO Store', project=self.project, created_by=creator)
        self.frc = FRCReport.objects.create(
            title='RO PPE', project=self.project, created_by=creator)
        self.client.force_login(self.pm)

    def _get(self, name, *args):
        return self.client.get(reverse(name, args=args))

    def test_read_pages(self):
        cases = [
            ('procurement:dashboard', ()),
            ('procurement:po_list', ()),
            ('procurement:po_detail', (self.po.pk,)),
            ('procurement:po_by_project', ()),
            ('procurement:summary_internal', ()),
            ('procurement:summary_external', ()),
            ('procurement:dn_list', ()),
            ('procurement:dn_detail', (self.dn.pk,)),
            ('procurement:inventory_list', ()),
            ('procurement:inventory_detail', (self.inv.pk,)),
            ('procurement:frc_list', ()),
            ('procurement:frc_detail', (self.frc.pk,)),
            ('procurement:frc_inventory', ()),
            ('procurement:approved_budgets', ()),
        ]
        for name, args in cases:
            with self.subTest(name):
                self.assertEqual(self._get(name, *args).status_code, 200)

    def test_export_endpoints(self):
        cases = [
            ('procurement:po_export', (self.po.pk,)),
            ('procurement:po_export_pdf', (self.po.pk,)),
            ('procurement:dn_export', (self.dn.pk,)),
            ('procurement:dn_export_pdf', (self.dn.pk,)),
            ('procurement:inventory_export', (self.inv.pk,)),
            ('procurement:inventory_export_pdf', (self.inv.pk,)),
            ('procurement:frc_export', (self.frc.pk,)),
            ('procurement:frc_export_pdf', (self.frc.pk,)),
            ('procurement:summary_internal_export', ()),
            ('procurement:summary_external_export', ()),
        ]
        for name, args in cases:
            with self.subTest(name):
                self.assertEqual(self._get(name, *args).status_code, 200)

    def test_a_record_they_did_not_create_but_is_in_their_region_is_visible(self):
        """The bug this PR shipped with: DN/Inventory/FRC were scoped to
        created_by only, so a real PM (who does not create these) would have
        opened an empty list. Region visibility has to match _visible_pos_for.
        """
        other = a_user('creator', Role.SUPER_ADMIN)
        dn = DeliveryNote.objects.create(
            dn_number='DN-RO-2', date=date(2026, 1, 1),
            sold_to_company='Another Client', delivery_address='456 Elsewhere Ave',
            project=self.project, created_by=other)
        inv = InventoryReport.objects.create(
            title='Not Mine Store', project=self.project, created_by=other)
        frc = FRCReport.objects.create(
            title='Not Mine PPE', project=self.project, created_by=other)

        self.assertEqual(self._get('procurement:dn_detail', dn.pk).status_code, 200)
        self.assertEqual(self._get('procurement:inventory_detail', inv.pk).status_code, 200)
        self.assertEqual(self._get('procurement:frc_detail', frc.pk).status_code, 200)
        self.assertIn(dn.dn_number, self._get('procurement:dn_list').content.decode())

    def test_a_record_outside_their_region_stays_invisible(self):
        """Widening to region-scoped must not become company-wide."""
        other_region = a_region('WEST', 'Western')
        other = a_user('outsider', Role.SUPER_ADMIN)
        other_project = a_project('Faraway Job', other_region)
        dn = DeliveryNote.objects.create(
            dn_number='DN-RO-3', date=date(2026, 1, 1),
            sold_to_company='Far Client', delivery_address='789 Nowhere Rd',
            project=other_project, created_by=other)
        # Not in the scoped queryset -> DetailView's get_object() 404s;
        # test_func() itself is unconditionally True on these detail views,
        # so a wrong-region PK never reaches a permission check at all.
        self.assertEqual(self._get('procurement:dn_detail', dn.pk).status_code, 404)
        self.assertNotIn(dn.dn_number, self._get('procurement:dn_list').content.decode())


class WriteEverythingBlockedTests(TestCase):
    """Read access must not come with write access, anywhere in the section.

    Each assertion checks two things: the response is not a 200 (nothing was
    rendered as if it worked) and, where relevant, the database did not
    change - a redirect that still silently saved would pass a status-only
    check while being exactly the bug this PR exists to prevent.
    """

    def setUp(self):
        self.region = a_region()
        self.pm = pm_with_procurement(region=self.region)
        self.project = a_project('Jubail Upgrade', self.region)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-RO-2', vendor_name='ACME',
            po_issued_by='Tester', project=self.project)
        self.item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=1, description='Widget',
            quantity=1, rate_per_unit=100, system='CCTV', scm='')
        self.dn = DeliveryNote.objects.create(
            dn_number='DN-RO-4', date=date(2026, 1, 1),
            sold_to_company='A Client', delivery_address='123 Somewhere Street',
            project=self.project)
        self.inv = InventoryReport.objects.create(title='RO Store 2', project=self.project)
        self.frc = FRCReport.objects.create(title='RO PPE 2', project=self.project)
        self.stock = FRCInventory.objects.create(item_type='shirt', size='M', color='Navy')
        self.client.force_login(self.pm)

    def _blocked(self, method, name, args=(), data=None):
        url = reverse(name, args=args)
        resp = getattr(self.client, method)(url, data or {})
        self.assertNotEqual(resp.status_code, 200,
                             f'{name} let a project manager through')
        return resp

    def test_create_pages_are_blocked(self):
        for name in ('procurement:po_create', 'procurement:dn_create',
                     'procurement:inventory_create', 'procurement:frc_create',
                     'procurement:frc_inventory_create'):
            with self.subTest(name):
                self._blocked('get', name)

    def test_edit_and_delete_pages_are_blocked(self):
        cases = [
            ('procurement:po_update', (self.po.pk,)),
            ('procurement:po_delete', (self.po.pk,)),
            ('procurement:dn_update', (self.dn.pk,)),
            ('procurement:dn_delete', (self.dn.pk,)),
            ('procurement:inventory_update', (self.inv.pk,)),
            ('procurement:inventory_delete', (self.inv.pk,)),
            ('procurement:frc_update', (self.frc.pk,)),
            ('procurement:frc_delete', (self.frc.pk,)),
            ('procurement:frc_inventory_update', (self.stock.pk,)),
        ]
        for name, args in cases:
            with self.subTest(name):
                self._blocked('get', name, args)

    def test_a_po_they_happen_to_own_is_still_not_editable(self):
        """The exact bug found in review: ownership must not override the
        role block, because a PM being `created_by` on a record (however
        that happened) is not the same as being allowed to change it."""
        self.po.created_by = self.pm
        self.po.save(update_fields=['created_by'])
        self._blocked('get', 'procurement:po_update', (self.po.pk,))
        self._blocked('get', 'procurement:po_delete', (self.po.pk,))

        self.dn.created_by = self.pm
        self.dn.save(update_fields=['created_by'])
        self._blocked('get', 'procurement:dn_update', (self.dn.pk,))

        self.inv.created_by = self.pm
        self.inv.save(update_fields=['created_by'])
        self._blocked('get', 'procurement:inventory_update', (self.inv.pk,))

        self.frc.created_by = self.pm
        self.frc.save(update_fields=['created_by'])
        self._blocked('get', 'procurement:frc_update', (self.frc.pk,))

    def test_import_endpoints_are_blocked_and_change_nothing(self):
        po_count = PurchaseOrder.objects.count()
        dn_count = DeliveryNote.objects.count()
        inv_count = InventoryReport.objects.count()
        frc_count = FRCReport.objects.count()

        self._blocked('post', 'procurement:po_import')
        self._blocked('post', 'procurement:dn_import')
        self._blocked('post', 'procurement:inventory_import')
        self._blocked('post', 'procurement:frc_import')
        self._blocked('post', 'procurement:po_import_items', (self.po.pk,))

        self.assertEqual(PurchaseOrder.objects.count(), po_count)
        self.assertEqual(DeliveryNote.objects.count(), dn_count)
        self.assertEqual(InventoryReport.objects.count(), inv_count)
        self.assertEqual(FRCReport.objects.count(), frc_count)

    def test_dn_from_po_is_blocked(self):
        dn_count = DeliveryNote.objects.count()
        self._blocked('get', 'procurement:dn_create_from_po', (self.po.pk,))
        self.assertEqual(DeliveryNote.objects.count(), dn_count)

    def test_ajax_edit_endpoints_refuse_with_json_not_a_crash(self):
        resp = self.client.post(
            reverse('procurement:po_toggle_term', args=(self.po.pk,)),
            {'term_id': '1'})
        self.assertEqual(resp.status_code, 403)

        resp = self.client.post(
            reverse('procurement:po_item_update_field', args=(self.item.pk,)),
            {'field': 'scm', 'value': 'ZZ'})
        self.assertEqual(resp.status_code, 403)
        self.item.refresh_from_db()
        self.assertEqual(self.item.scm, '')

    def test_quotation_and_budget_procurement_flows_stay_off_limits(self):
        """Pre-existing gates (_can_procure / the approved-budgets role
        check), not something this PR added - a regression here would mean
        the read-only widening leaked into flows it was never meant to touch.
        """
        self._blocked('get', 'procurement:quotation_import')

    def test_admin_and_procurement_manager_are_unaffected(self):
        """The point of a role-scoped check: everyone who could already
        create/edit/delete/import still can, unchanged."""
        admin = a_user('admin-ro', Role.ADMIN, region=self.region)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(
            reverse('procurement:po_create')).status_code, 200)
        self.assertEqual(self.client.get(
            reverse('procurement:po_update', args=(self.po.pk,))).status_code, 200)

        scm = grant(a_user('scm-ro', Role.PROCUREMENT_MGR, region=self.region),
                    'po.approve')
        self.client.force_login(scm)
        self.assertEqual(self.client.get(
            reverse('procurement:dn_create')).status_code, 200)
        self.assertEqual(self.client.get(
            reverse('procurement:inventory_create')).status_code, 200)


class NoWriteShortcutsInListOrDashboardHtmlTests(TestCase):
    """The server already refuses every write path for a PM (see
    WriteEverythingBlockedTests) - this checks the other half: that a page
    doesn't show a live-looking Edit/Delete/Create control that only bounces
    the PM away when clicked. Found by review: the header/empty-state
    buttons were hidden, but per-row action buttons in the list tables and
    the procurement dashboard's Quick Actions were missed.
    """

    def setUp(self):
        self.region = a_region()
        self.pm = pm_with_procurement(region=self.region)
        creator = a_user('creator1', Role.SUPER_ADMIN)
        self.project = a_project('Jubail Upgrade', self.region)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-RO-3', vendor_name='ACME',
            po_issued_by='Tester', project=self.project, created_by=creator)
        self.dn = DeliveryNote.objects.create(
            dn_number='DN-RO-5', date=date(2026, 1, 1),
            sold_to_company='A Client', delivery_address='123 Somewhere Street',
            project=self.project, created_by=creator)
        self.inv = InventoryReport.objects.create(
            title='RO Store 3', project=self.project, created_by=creator)
        self.frc = FRCReport.objects.create(
            title='RO PPE 3', project=self.project, created_by=creator)
        self.client.force_login(self.pm)

    def test_po_list_row_has_no_edit_link(self):
        body = self.client.get(reverse('procurement:po_list')).content.decode()
        self.assertNotIn(reverse('procurement:po_update', args=(self.po.pk,)), body)

    def test_dn_list_row_has_no_edit_link(self):
        body = self.client.get(reverse('procurement:dn_list')).content.decode()
        self.assertNotIn(reverse('procurement:dn_update', args=(self.dn.pk,)), body)

    def test_inventory_list_row_has_no_edit_link(self):
        body = self.client.get(reverse('procurement:inventory_list')).content.decode()
        self.assertNotIn(reverse('procurement:inventory_update', args=(self.inv.pk,)), body)

    def test_frc_list_card_has_no_edit_or_delete_link(self):
        body = self.client.get(reverse('procurement:frc_list')).content.decode()
        self.assertNotIn(reverse('procurement:frc_update', args=(self.frc.pk,)), body)
        self.assertNotIn(reverse('procurement:frc_delete', args=(self.frc.pk,)), body)

    def test_dashboard_quick_actions_has_no_create_links(self):
        body = self.client.get(reverse('procurement:dashboard')).content.decode()
        self.assertNotIn(reverse('procurement:po_create'), body)
        self.assertNotIn(reverse('procurement:dn_create'), body)
        self.assertNotIn(reverse('procurement:inventory_create'), body)

    def test_po_by_project_empty_state_has_no_create_link(self):
        body = self.client.get(reverse('procurement:po_by_project')).content.decode()
        self.assertNotIn(reverse('procurement:po_create'), body)


class NavAndHiddenPagesTests(TestCase):

    def setUp(self):
        self.pm = pm_with_procurement()

    def test_terms_and_routing_are_not_in_the_procurement_nav(self):
        self.client.force_login(self.pm)
        body = self.client.get(reverse('procurement:po_list')).content.decode()
        self.assertNotIn('T&amp;C Templates', body)

    def test_approval_routing_still_refuses_a_project_manager(self):
        """Pre-existing (super-admin-only) gate; confirming it survived this
        PR untouched, since the section is now open around it."""
        self.client.force_login(self.pm)
        resp = self.client.get(reverse('procurement:po_stage_approvers'))
        self.assertNotEqual(resp.status_code, 200)


class ProcurementReadOnlyPropertyTests(TestCase):
    """Every check in procurement/views.py and its templates reads
    user.is_procurement_read_only_user, not is_project_manager_user directly
    - that indirection is what lets a future Assistant Project Manager role
    inherit this read-only behaviour by editing one property instead of the
    ~20 sites this PR touched. This test exists so a future edit that
    quietly reverts to the direct check gets caught here rather than by
    someone noticing Assistant PM has full write access in production.
    """

    def test_a_project_manager_is_procurement_read_only(self):
        pm = a_user('pm-prop', Role.PROJECT_MANAGER)
        self.assertTrue(pm.is_procurement_read_only_user)

    def test_other_roles_are_not(self):
        for role_name in (Role.ADMIN, Role.SUPER_ADMIN, Role.PROCUREMENT_MGR,
                           Role.SALES_REP, Role.SITE_MANAGER):
            with self.subTest(role_name):
                user = a_user(f'notpm-{role_name}', role_name)
                self.assertFalse(user.is_procurement_read_only_user)


class Migration0038Tests(TestCase):
    """Same shape as ExistingDatabaseMigrationTests in
    tests_approve_permission.py: the rows this migration corrects already
    exist, seeded OFF, on any database that has been running - a data
    migration is the only path that reaches them.
    """

    def _migration(self):
        import importlib
        return importlib.import_module(
            'accounts.migrations.0038_grant_pm_procurement_read')

    def _apply_to_rows_that_are_off(self):
        from django.apps import apps as global_apps
        codes = ('procurement.access', 'procurement.nav', 'dn.access', 'dn.nav')
        RolePermission.objects.filter(codename__in=codes).update(allowed=False)
        self._migration().grant(global_apps, None)
        return {
            (rp.role.name, rp.codename): rp.allowed
            for rp in RolePermission.objects.select_related('role')
            .filter(codename__in=codes)
        }

    def test_project_manager_is_granted_all_four(self):
        Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        rows = self._apply_to_rows_that_are_off()
        for code in ('procurement.access', 'procurement.nav', 'dn.access', 'dn.nav'):
            self.assertTrue(rows[('project_manager', code)], code)

    def test_other_roles_are_left_alone(self):
        Role.objects.get_or_create(name=Role.SALES_REP)
        Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        rows = self._apply_to_rows_that_are_off()
        self.assertFalse(rows[('sales_rep', 'procurement.access')])

    def test_reversing_takes_exactly_these_rows_back_off(self):
        from django.apps import apps as global_apps
        Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        module = self._migration()
        module.grant(global_apps, None)
        module.ungrant(global_apps, None)
        pm_role = Role.objects.get(name=Role.PROJECT_MANAGER)
        self.assertFalse(
            pm_role.permissions.get(codename='procurement.access').allowed)
