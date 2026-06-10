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
