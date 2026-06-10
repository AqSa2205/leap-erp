from django.test import TestCase
from django.db import IntegrityError
from accounts.permissions import CAPABILITIES, Capability, capability_codenames
from accounts.models import Role, RolePermission, PermissionChangeLog, User


class RegistryTests(TestCase):
    def test_codenames_are_unique(self):
        codes = [c.codename for c in CAPABILITIES]
        self.assertEqual(len(codes), len(set(codes)), "duplicate capability codenames")

    def test_every_capability_has_module_and_action(self):
        for c in CAPABILITIES:
            # assertTrue checks truthiness; an empty module/action ('') is
            # falsy and fails. codename is the failure message (msg=) so a
            # failure tells you which capability is malformed.
            self.assertTrue(c.module, msg=c.codename)
            self.assertTrue(c.action, msg=c.codename)

    def test_access_and_nav_exist_for_each_main_module(self):
        for module_key in ['dashboard', 'pipeline', 'costing', 'procurement', 'po', 'dn', 'settings']:
            self.assertIn(f'{module_key}.access', capability_codenames())
            self.assertIn(f'{module_key}.nav', capability_codenames())


class RolePermissionModelTests(TestCase):
    def setUp(self):
        self.role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)

    def test_create_grant(self):
        g = RolePermission.objects.create(role=self.role, codename='costing.access', allowed=True)
        self.assertTrue(g.allowed)

    def test_role_codename_is_unique(self):
        RolePermission.objects.create(role=self.role, codename='costing.access', allowed=True)
        with self.assertRaises(IntegrityError):
            RolePermission.objects.create(role=self.role, codename='costing.access', allowed=False)

    def test_change_log_records(self):
        u = User.objects.create_user('toggler', password='x')
        PermissionChangeLog.objects.create(actor=u, role=self.role, codename='po.access', allowed=True)
        self.assertEqual(PermissionChangeLog.objects.count(), 1)
        log = PermissionChangeLog.objects.get()
        self.assertEqual(log.actor, u)
        self.assertEqual(log.role, self.role)
        self.assertEqual(log.codename, 'po.access')
        self.assertTrue(log.allowed)


class HasCapabilityTests(TestCase):
    def setUp(self):
        self.fin, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        self.sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.fin_user = User.objects.create_user('fin', password='x')
        self.fin_user.role = self.fin
        self.fin_user.save()
        self.sa_user = User.objects.create_user('sa', password='x')
        self.sa_user.role = self.sa
        self.sa_user.save()
        RolePermission.objects.update_or_create(
            role=self.fin, codename='costing.access', defaults={'allowed': True})
        RolePermission.objects.update_or_create(
            role=self.fin, codename='po.access', defaults={'allowed': False})

    def test_granted_capability_true(self):
        self.assertTrue(self.fin_user.has_capability('costing.access'))

    def test_denied_capability_false(self):
        self.assertFalse(self.fin_user.has_capability('po.access'))

    def test_missing_grant_defaults_false(self):
        self.assertFalse(self.fin_user.has_capability('dn.access'))

    def test_super_admin_always_true(self):
        self.assertTrue(self.sa_user.has_capability('anything.at.all'))

    def test_user_without_role_false(self):
        roleless = User.objects.create_user('none', password='x')
        self.assertFalse(roleless.has_capability('costing.access'))

    def test_cache_is_consistent_within_instance(self):
        first = self.fin_user.has_capability('costing.access')
        RolePermission.objects.filter(role=self.fin, codename='costing.access').update(allowed=False)
        # Same in-memory user keeps its cached snapshot until reloaded.
        self.assertEqual(self.fin_user.has_capability('costing.access'), first)
