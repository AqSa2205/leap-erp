import json
from django.test import TestCase, RequestFactory
from django.db import IntegrityError
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.template import Template, Context
from django.urls import reverse
from accounts.permissions import CAPABILITIES, Capability, capability_codenames, require_capability, CapabilityRequiredMixin, seed_default_permissions, DEFAULT_MODULE_ACCESS
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
        # The seed migration pre-creates rows for all roles; remove the specific
        # codename used below so we can test raw create/unique behaviour.
        RolePermission.objects.filter(role=self.role, codename='costing.access').delete()

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


class GateTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        self.user = User.objects.create_user('u', password='x')
        self.user.role = self.role
        self.user.save()
        RolePermission.objects.update_or_create(
            role=self.role, codename='costing.access', defaults={'allowed': True})

    def _req(self, user):
        r = self.rf.get('/x/')
        r.user = user
        return r

    def test_decorator_allows_when_granted(self):
        @require_capability('costing.access')
        def view(request):
            return HttpResponse('ok')
        self.assertEqual(view(self._req(self.user)).status_code, 200)

    def test_decorator_blocks_when_missing(self):
        @require_capability('po.access')
        def view(request):
            return HttpResponse('ok')
        with self.assertRaises(PermissionDenied):
            view(self._req(self.user))

    def test_mixin_blocks_when_missing(self):
        from django.views import View

        class V(CapabilityRequiredMixin, View):
            capability = 'po.access'
            def get(self, request):
                return HttpResponse('ok')

        with self.assertRaises(PermissionDenied):
            V.as_view()(self._req(self.user))

    def test_mixin_allows_when_granted(self):
        # Exercises the success path: dispatch must fall through to the view.
        from django.views import View

        class V(CapabilityRequiredMixin, View):
            capability = 'costing.access'
            def get(self, request):
                return HttpResponse('ok')

        resp = V.as_view()(self._req(self.user))
        self.assertEqual(resp.status_code, 200)


class TemplateFilterTests(TestCase):
    def setUp(self):
        self.role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        self.user = User.objects.create_user('u', password='x')
        self.user.role = self.role
        self.user.save()
        RolePermission.objects.update_or_create(
            role=self.role, codename='costing.access', defaults={'allowed': True})

    def _render(self, codename, user):
        t = Template("{% load perms %}{% if user|can:cap %}YES{% else %}NO{% endif %}")
        return t.render(Context({'user': user, 'cap': codename}))

    def test_filter_true(self):
        self.assertEqual(self._render('costing.access', self.user), 'YES')

    def test_filter_false(self):
        self.assertEqual(self._render('po.access', self.user), 'NO')


class SeedTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def test_default_module_access_keys_match_roles(self):
        # Guards against a silent regression: if a Role is renamed or added and
        # DEFAULT_MODULE_ACCESS isn't updated, the seed would give that role
        # zero access with no error. This fails loudly instead.
        valid_role_names = {name for name, _ in Role.ROLE_CHOICES}
        self.assertEqual(set(DEFAULT_MODULE_ACCESS), valid_role_names)

    def _allowed(self, role_name, codename):
        role = Role.objects.get(name=role_name)
        return RolePermission.objects.get(role=role, codename=codename).allowed

    def test_every_role_capability_pair_has_a_row(self):
        n_roles = Role.objects.count()
        n_caps = len(capability_codenames())
        self.assertEqual(RolePermission.objects.count(), n_roles * n_caps)

    def test_finance_gets_pipeline_access(self):
        self.assertTrue(self._allowed(Role.FINANCE_REP, 'pipeline.access'))
        self.assertTrue(self._allowed(Role.FINANCE_REP, 'pipeline.nav'))

    def test_finance_no_procurement(self):
        self.assertFalse(self._allowed(Role.FINANCE_REP, 'procurement.access'))

    def test_sales_rep_pipeline_and_costing(self):
        self.assertTrue(self._allowed(Role.SALES_REP, 'pipeline.access'))
        self.assertTrue(self._allowed(Role.SALES_REP, 'costing.access'))

    def test_procurement_gets_po_dn(self):
        self.assertTrue(self._allowed(Role.PROCUREMENT_OFF, 'po.access'))
        self.assertTrue(self._allowed(Role.PROCUREMENT_OFF, 'dn.access'))

    def test_only_super_admin_gets_settings(self):
        self.assertTrue(self._allowed(Role.SUPER_ADMIN, 'settings.access'))
        self.assertFalse(self._allowed(Role.SALES_REP, 'settings.access'))

    def test_seed_is_idempotent(self):
        before = RolePermission.objects.count()
        seed_default_permissions()
        self.assertEqual(RolePermission.objects.count(), before)


class PermissionMatrixViewTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.sa = User.objects.create_user('sa', password='x')
        self.sa.role = Role.objects.get(name=Role.SUPER_ADMIN)
        self.sa.save()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()

    def test_super_admin_can_open(self):
        self.client.force_login(self.sa)
        resp = self.client.get(reverse('accounts:permission_matrix'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Commercial Pipeline')

    def test_non_super_admin_forbidden(self):
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('accounts:permission_matrix'))
        self.assertEqual(resp.status_code, 403)


class TogglePermissionTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.sa = User.objects.create_user('sa', password='x')
        self.sa.role = Role.objects.get(name=Role.SUPER_ADMIN)
        self.sa.save()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()
        self.fin_role = Role.objects.get(name=Role.FINANCE_REP)

    def _toggle(self, role_id, codename, allowed):
        return self.client.post(
            reverse('accounts:toggle_permission'),
            data=json.dumps({'role': role_id, 'codename': codename, 'allowed': allowed}),
            content_type='application/json',
        )

    def test_toggle_flips_grant_and_logs(self):
        self.client.force_login(self.sa)
        resp = self._toggle(self.fin_role.id, 'po.access', True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(RolePermission.objects.get(role=self.fin_role, codename='po.access').allowed)
        self.assertEqual(PermissionChangeLog.objects.filter(codename='po.access', allowed=True).count(), 1)

    def test_non_super_admin_forbidden(self):
        self.client.force_login(self.fin)
        resp = self._toggle(self.fin_role.id, 'po.access', True)
        self.assertEqual(resp.status_code, 403)

    def test_cannot_toggle_super_admin_row(self):
        self.client.force_login(self.sa)
        sa_role = Role.objects.get(name=Role.SUPER_ADMIN)
        resp = self._toggle(sa_role.id, 'po.access', False)
        self.assertEqual(resp.status_code, 400)

    def test_toggle_updates_existing_row_not_duplicate(self):
        # update_or_create must flip the existing seeded row in place, never
        # create a second one for the same (role, codename).
        self.client.force_login(self.sa)
        self._toggle(self.fin_role.id, 'po.access', True)
        self._toggle(self.fin_role.id, 'po.access', False)
        rows = RolePermission.objects.filter(role=self.fin_role, codename='po.access')
        self.assertEqual(rows.count(), 1)
        self.assertFalse(rows.get().allowed)
        # Each toggle is independently audited.
        self.assertEqual(PermissionChangeLog.objects.filter(codename='po.access').count(), 2)


class DashboardWiringTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()

    def test_finance_with_access_gets_200(self):
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)

    def test_finance_without_access_gets_403(self):
        role = Role.objects.get(name=Role.FINANCE_REP)
        RolePermission.objects.filter(role=role, codename='dashboard.access').update(allowed=False)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 403)
