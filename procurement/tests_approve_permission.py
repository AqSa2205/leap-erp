"""Who may sign a purchase-order stage, now that the grid decides it.

Two questions have to pass, and the tests keep them apart because collapsing
them is the failure mode: `po.approve` says whether a role signs anything,
the stage mapping says which stage is theirs. A capability that quietly
granted all four stages would hand one person the whole approval chain, which
is the one thing four stages exist to prevent.
"""

import base64
import io
from datetime import date

from PIL import Image
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, RolePermission, User
from procurement.models import PurchaseOrder

STAGES = [key for key, _label, _signer in PurchaseOrder.APPROVAL_STAGES]


def _png_data_url():
    buf = io.BytesIO()
    Image.new('RGBA', (8, 8), (0, 0, 0, 0)).save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def a_region(code='EAST', name='Eastern'):
    from projects.models import Region
    region, _ = Region.objects.get_or_create(
        code=code, defaults={'name': name})
    return region


def a_project(name, region):
    from projects.models import Project, ProjectStatus
    status, _ = ProjectStatus.objects.get_or_create(
        name='Won', defaults={'category': 'won'})
    return Project.objects.create(
        project_name=name, status=status, region=region)


def a_po(number='PO-PERM-1', **kw):
    values = dict(po_date=date(2026, 1, 1), po_number=number,
                  vendor_name='ACME', po_issued_by='Tester')
    values.update(kw)
    return PurchaseOrder.objects.create(**values)


def a_user(username, role_name, **kw):
    role, _ = Role.objects.get_or_create(name=role_name)
    return User.objects.create_user(username, password='pw', role=role, **kw)


def grant(user, codename, allowed=True):
    user.role.permissions.update_or_create(
        codename=codename, defaults={'allowed': allowed})
    return user


class ProjectManagerApprovalTests(TestCase):

    def setUp(self):
        self.po = a_po()
        self.pm = grant(a_user('pm1', Role.PROJECT_MANAGER), 'po.approve')

    def test_a_project_manager_can_sign_the_pm_stage(self):
        self.assertTrue(self.po.can_user_approve_stage(self.pm, 'pm'))

    def test_a_project_manager_cannot_sign_the_other_stages(self):
        """The capability is not a skeleton key. Four stages exist so that one
        person does not hold the whole chain."""
        for stage in ('scm', 'coo', 'ceo'):
            self.assertFalse(self.po.can_user_approve_stage(self.pm, stage),
                             f'project manager should not sign {stage}')

    def test_the_pm_stage_lands_in_their_inbox(self):
        """Being allowed to sign is useless if the PO never appears in the
        list of things waiting on them."""
        self.assertTrue(self.po.is_designated_approver(self.pm, 'pm'))

    def test_they_are_emailed_when_the_stage_is_reached(self):
        from procurement.notifications import stage_recipients
        self.assertIn(self.pm, stage_recipients('pm'))

    def test_they_can_see_purchase_orders_in_their_region(self):
        """A PO they cannot open is a PO they cannot sign - the approve
        endpoint scopes by the same queryset."""
        from procurement.views import _visible_pos_for
        east = a_region()
        self.pm.region = east
        self.pm.save()
        mine = a_po('PO-PERM-REGION', project=a_project('Jubail', east))
        self.assertIn(mine, _visible_pos_for(self.pm))

    def test_they_cannot_see_another_regions_orders(self):
        from procurement.views import _visible_pos_for
        self.pm.region = a_region()
        self.pm.save()
        theirs = a_po('PO-PERM-OTHER',
                      project=a_project('Riyadh Job',
                                        a_region('CENT', 'Central')))
        self.assertNotIn(theirs, _visible_pos_for(self.pm))


class CapabilityGateTests(TestCase):
    """The grid has to work in both directions, or it is decoration."""

    def setUp(self):
        self.po = a_po()

    def test_without_the_capability_a_project_manager_is_refused(self):
        pm = a_user('pm2', Role.PROJECT_MANAGER)
        grant(pm, 'po.approve', allowed=False)
        self.assertFalse(self.po.can_user_approve_stage(pm, 'pm'))

    def test_revoking_it_takes_the_stage_off_an_admin(self):
        """Admin could sign before this was wired. Revoking the capability has
        to actually remove that, otherwise the toggle is a lie."""
        admin = a_user('adm1', Role.ADMIN)
        grant(admin, 'po.approve')
        self.assertTrue(self.po.can_user_approve_stage(admin, 'pm'))
        grant(admin, 'po.approve', allowed=False)
        # has_capability caches the allowed set on the instance for the life
        # of the request, so the revocation is read on a fresh one - which is
        # what the next request would do.
        admin = User.objects.get(pk=admin.pk)
        self.assertFalse(self.po.can_user_approve_stage(admin, 'pm'))

    def test_revoking_it_also_empties_their_inbox(self):
        admin = a_user('adm2', Role.ADMIN)
        grant(admin, 'po.approve', allowed=False)
        self.assertFalse(self.po.is_designated_approver(admin, 'pm'))

    def test_the_capability_alone_grants_no_stage(self):
        """Granting it to a role with no stage of its own must do nothing -
        it answers whether, not which."""
        rep = grant(a_user('rep1', Role.SALES_REP), 'po.approve')
        for stage in STAGES:
            self.assertFalse(self.po.can_user_approve_stage(rep, stage))

    def test_super_admin_still_overrides(self):
        """The standing override exists so work does not stall when somebody
        is away; it deliberately sits above the grid."""
        boss = a_user('boss1', Role.SUPER_ADMIN)
        grant(boss, 'po.approve', allowed=False)
        for stage in STAGES:
            self.assertTrue(self.po.can_user_approve_stage(boss, stage))

    def test_anonymous_is_refused(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(self.po.can_user_approve_stage(AnonymousUser(), 'pm'))


class ExistingHoldersKeepTheirStagesTests(TestCase):
    """Nobody loses approval when the wiring lands.

    The capability was declared and never read, so every row for it sits at
    OFF on a live database. Migration 0037 turns it on for the roles that
    could already sign, which is what makes this a wiring change rather than
    a change of who approves.
    """

    def setUp(self):
        self.po = a_po()

    def _seeded(self, username, role_name):
        """A user whose role carries only what the migration seeded."""
        user = a_user(username, role_name)
        allowed = (RolePermission.objects
                   .filter(role=user.role, codename='po.approve')
                   .values_list('allowed', flat=True).first())
        self.assertTrue(allowed, f'{role_name} lost po.approve')
        return user

    def test_admin_still_signs_pm_and_coo(self):
        admin = self._seeded('adm3', Role.ADMIN)
        self.assertTrue(self.po.can_user_approve_stage(admin, 'pm'))
        self.assertTrue(self.po.can_user_approve_stage(admin, 'coo'))

    def test_the_procurement_manager_still_signs_scm(self):
        scm = self._seeded('scm1', Role.PROCUREMENT_MGR)
        self.assertTrue(self.po.can_user_approve_stage(scm, 'scm'))

    def test_the_project_manager_is_seeded_on(self):
        pm = self._seeded('pm3', Role.PROJECT_MANAGER)
        self.assertTrue(self.po.can_user_approve_stage(pm, 'pm'))

    def test_a_role_with_no_stage_is_not_seeded(self):
        rep = a_user('rep2', Role.SALES_REP)
        allowed = (RolePermission.objects
                   .filter(role=rep.role, codename='po.approve')
                   .values_list('allowed', flat=True).first())
        self.assertFalse(allowed)


class FreshDatabaseSeedTests(TestCase):
    """The baseline that a brand-new database gets.

    Separate from the migration below on purpose: the two paths cover
    different databases - defaults seed rows that are MISSING, the migration
    corrects rows that already exist - and a test that cannot tell them apart
    passes while either one is broken.
    """

    def test_a_newly_seeded_project_manager_may_approve(self):
        from accounts.permissions import seed_default_permissions
        role = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)[0]
        role.permissions.all().delete()      # as if the role were brand new
        seed_default_permissions()
        allowed = dict(role.permissions.values_list('codename', 'allowed'))
        self.assertTrue(allowed['po.approve'])
        # And can reach the section they approve in.
        self.assertTrue(allowed['po.access'])
        self.assertTrue(allowed['po.nav'])

    def test_a_newly_seeded_sales_rep_may_not(self):
        from accounts.permissions import seed_default_permissions
        role = Role.objects.get_or_create(name=Role.SALES_REP)[0]
        role.permissions.all().delete()
        seed_default_permissions()
        self.assertFalse(
            role.permissions.get(codename='po.approve').allowed)


class ExistingDatabaseMigrationTests(TestCase):
    """The correction applied to a database that has been running.

    Exercised directly against rows set to OFF, because that is the state
    production is in and the one the defaults above cannot reach.
    """

    def _migration(self):
        import importlib
        return importlib.import_module(
            'accounts.migrations.0037_grant_po_approve')

    def _apply_to_rows_that_are_off(self):
        from django.apps import apps as global_apps
        RolePermission.objects.filter(
            codename__in=('po.approve', 'po.access', 'po.nav')
        ).update(allowed=False)
        self._migration().grant(global_apps, None)
        return {
            (rp.role.name, rp.codename): rp.allowed
            for rp in RolePermission.objects.select_related('role').filter(
                codename__in=('po.approve', 'po.access', 'po.nav'))
        }

    def test_the_roles_that_could_already_sign_get_it_back(self):
        rows = self._apply_to_rows_that_are_off()
        for name in ('super_admin', 'admin', 'procurement_mgr'):
            self.assertTrue(rows[(name, 'po.approve')],
                            f'{name} would lose PO approval on deploy')

    def test_the_project_manager_is_granted_it(self):
        rows = self._apply_to_rows_that_are_off()
        self.assertTrue(rows[('project_manager', 'po.approve')])

    def test_the_project_manager_can_also_reach_purchase_orders(self):
        """Granting approval without the module leaves a permission that
        cannot be exercised - the section stays invisible."""
        rows = self._apply_to_rows_that_are_off()
        self.assertTrue(rows[('project_manager', 'po.access')])
        self.assertTrue(rows[('project_manager', 'po.nav')])

    def test_a_role_with_no_stage_is_left_alone(self):
        rows = self._apply_to_rows_that_are_off()
        self.assertFalse(rows[('sales_rep', 'po.approve')])

    def test_reversing_does_not_take_purchase_orders_from_everyone_else(self):
        """po.access is held by most roles; the reverse must undo this
        migration, not empty the column."""
        from django.apps import apps as global_apps
        module = self._migration()
        module.grant(global_apps, None)
        module.ungrant(global_apps, None)
        rep = Role.objects.get(name=Role.SALES_REP)
        self.assertTrue(
            rep.permissions.get(codename='po.access').allowed)
        pm = Role.objects.get(name=Role.PROJECT_MANAGER)
        self.assertFalse(
            pm.permissions.get(codename='po.approve').allowed)


class ApproveEndpointTests(TestCase):
    """The gate has to hold at the URL, not only in the model.

    Hiding the Approve button is not a permission; the endpoint is still
    there for anyone who knows it.
    """

    def setUp(self):
        self.po = a_po('PO-PERM-ENDPOINT')

    def _url(self, stage='pm'):
        return reverse('procurement:po_approve_stage',
                       kwargs={'pk': self.po.pk, 'stage': stage})

    def _sign(self, user, stage='pm'):
        """A complete, valid request - signature included.

        Posting an empty body would be refused at 400 for the missing
        signature and never reach the permission check, which would make
        these pass whatever the gate did.
        """
        self.client.force_login(user)
        return self.client.post(self._url(stage),
                                {'signature_data': _png_data_url()})

    def test_a_project_manager_without_the_capability_is_refused(self):
        pm = a_user('pm4', Role.PROJECT_MANAGER, region=a_region())
        grant(pm, 'po.approve', allowed=False)
        resp = self._sign(pm)
        self.assertIn(resp.status_code, (400, 403, 404))
        self.po.refresh_from_db()
        self.assertIsNone(self.po.pm_approved_at)

    def test_a_sales_rep_cannot_sign_even_with_the_capability(self):
        rep = grant(a_user('rep3', Role.SALES_REP), 'po.approve')
        resp = self._sign(rep)
        self.assertIn(resp.status_code, (400, 403, 404))
        self.po.refresh_from_db()
        self.assertIsNone(self.po.pm_approved_at)

    def test_a_project_manager_with_it_signs_the_pm_stage(self):
        """The positive case at the URL: everything above proves a refusal,
        and a gate that refuses everybody would pass all of them."""
        from django.utils import timezone
        east = a_region()
        # The approve endpoint scopes by _visible_pos_for, so the order has
        # to be one they can actually see - a PO with no project would 404
        # here and the test would pass for the wrong reason.
        PurchaseOrder.objects.filter(pk=self.po.pk).update(
            scm_approved_at=timezone.now(),
            project=a_project('Jubail', east))
        pm = grant(a_user('pm6', Role.PROJECT_MANAGER, region=east),
                   'po.approve')
        resp = self._sign(pm)
        self.assertEqual(resp.status_code, 200)
        self.po.refresh_from_db()
        self.assertIsNotNone(self.po.pm_approved_at)
        self.assertEqual(self.po.pm_approved_by, pm)


class PurchaseOrderModuleTests(TestCase):

    def test_a_project_manager_can_open_the_po_list(self):
        """Seeded with the module, because approving inside a section you
        cannot open is a dead end."""
        pm = a_user('pm5', Role.PROJECT_MANAGER)
        self.client.force_login(pm)
        resp = self.client.get(reverse('procurement:po_list'))
        self.assertEqual(resp.status_code, 200)

    def test_the_approve_capability_is_declared_as_enforced(self):
        """The grid marks unenforced caps as wiring-pending; leaving this one
        marked that way would tell an administrator the toggle does nothing."""
        from accounts.permissions import CAPABILITIES
        cap = [c for c in CAPABILITIES if c.codename == 'po.approve'][0]
        self.assertTrue(cap.enforced)
