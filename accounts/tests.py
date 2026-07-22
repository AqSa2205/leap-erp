import json
import secrets
from datetime import timedelta
from django.test import TestCase, RequestFactory
from django.db import IntegrityError
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.template import Template, Context
from django.urls import reverse
from django.utils import timezone
from accounts.permissions import CAPABILITIES, Capability, capability_codenames, require_capability, CapabilityRequiredMixin, seed_default_permissions, DEFAULT_MODULE_ACCESS
from accounts.models import Role, RolePermission, PermissionChangeLog, User, PasswordResetRequest
from accounts.forms import CustomUserCreationForm
from projects.models import Project, Region, ProjectStatus


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
        # settings.access is seeded False for finance (super_admin-only page).
        self.assertFalse(self.fin_user.has_capability('settings.access'))

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
        # settings.access is the one capability a finance_rep never holds
        # (super_admin only), so it's a stable "denied" case across baselines.
        @require_capability('settings.access')
        def view(request):
            return HttpResponse('ok')
        with self.assertRaises(PermissionDenied):
            view(self._req(self.user))

    def test_mixin_blocks_when_missing(self):
        from django.views import View

        class V(CapabilityRequiredMixin, View):
            capability = 'settings.access'
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
        self.assertEqual(self._render('settings.access', self.user), 'NO')


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

    # The AI team is deliberately siloed to Dev Tracking (+ ungated My Work /
    # notifications), so it is exempt from the match-today open-module baseline.
    SILOED_ROLES = {Role.DEVELOPER, Role.AI_HEAD, Role.AI_INTERN,
                    Role.AI_ENGINEER, Role.AI_JUNIOR_ENGINEER}

    def test_match_today_all_open_modules_on_for_every_role(self):
        # Zero-regression baseline: every currently-open module is ON for every
        # operational role (data is scoped inside the views, but the page opens).
        open_modules = ['dashboard', 'pipeline', 'costing', 'procurement', 'po', 'dn']
        for role_name, _ in Role.ROLE_CHOICES:
            if role_name in self.SILOED_ROLES:
                continue
            for module in open_modules:
                self.assertTrue(self._allowed(role_name, f'{module}.access'),
                                msg=f'{role_name} should have {module}.access')
                self.assertTrue(self._allowed(role_name, f'{module}.nav'),
                                msg=f'{role_name} should have {module}.nav')

    def test_ai_team_is_siloed_to_devtracking(self):
        # Doers: only their own task view — none of the operational modules.
        for r in (Role.AI_INTERN, Role.AI_ENGINEER, Role.AI_JUNIOR_ENGINEER):
            self.assertTrue(self._allowed(r, 'devtracking.mywork'), msg=r)
            self.assertFalse(self._allowed(r, 'devtracking.admin'), msg=r)
            self.assertFalse(self._allowed(r, 'costing.access'), msg=r)
            self.assertFalse(self._allowed(r, 'pipeline.access'), msg=r)
            self.assertFalse(self._allowed(r, 'dashboard.nav'), msg=r)
        # AI Head manages Dev Tracking but stays out of the operational modules.
        self.assertTrue(self._allowed(Role.AI_HEAD, 'devtracking.admin'))
        self.assertFalse(self._allowed(Role.AI_HEAD, 'costing.access'))

    def test_finance_has_procurement_today(self):
        # Match-today: finance can open procurement pages (scoped) just like now.
        self.assertTrue(self._allowed(Role.FINANCE_REP, 'procurement.access'))

    def test_procurement_gets_po_dn(self):
        self.assertTrue(self._allowed(Role.PROCUREMENT_OFF, 'po.access'))
        self.assertTrue(self._allowed(Role.PROCUREMENT_OFF, 'dn.access'))

    def test_settings_is_super_admin_only(self):
        # Users/Settings is the one genuinely restricted page today
        # (AdminRequiredMixin = super_admin only).
        self.assertTrue(self._allowed(Role.SUPER_ADMIN, 'settings.access'))
        self.assertFalse(self._allowed(Role.SALES_REP, 'settings.access'))
        self.assertFalse(self._allowed(Role.ADMIN, 'settings.access'))

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

    def test_finance_without_access_redirected_not_403(self):
        # The dashboard root ('/') must not 403 for a logged-in user who lacks
        # dashboard access — it routes them to a page they can reach instead.
        # (Dashboard data is still only rendered for those with access.)
        role = Role.objects.get(name=Role.FINANCE_REP)
        RolePermission.objects.filter(role=role, codename='dashboard.access').update(allowed=False)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp['Location'], reverse('dashboard:index'))  # not a self-loop


class PipelineWiringTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.region, _ = Region.objects.get_or_create(
            code='LNA', defaults={'name': 'LNA', 'description': ''})
        self.status, _ = ProjectStatus.objects.get_or_create(
            name='Open', defaults={'category': 'open', 'description': ''})
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.region = self.region
        self.fin.save()

    def test_finance_with_access_sees_region_projects(self):
        owner = User.objects.create_user('owner', password='x')
        Project.objects.create(
            project_name='ZZPIPE1', region=self.region, status=self.status, owner=owner)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('projects:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ZZPIPE1')

    def test_finance_without_access_403(self):
        role = Role.objects.get(name=Role.FINANCE_REP)
        RolePermission.objects.filter(role=role, codename='pipeline.access').update(allowed=False)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('projects:list'))
        self.assertEqual(resp.status_code, 403)

    def test_finance_is_view_only_cannot_edit_region_project(self):
        # Finance can SEE the region project (above) but must not be able to
        # edit it — the widened queryset grants view, not write.
        owner = User.objects.create_user('owner2', password='x')
        proj = Project.objects.create(
            project_name='ZZPIPE2', region=self.region, status=self.status, owner=owner)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('projects:edit', kwargs={'pk': proj.pk}))
        self.assertEqual(resp.status_code, 403)


class ModuleAccessWiringTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()
        self.fin_role = Role.objects.get(name=Role.FINANCE_REP)

    def _off(self, codename):
        RolePermission.objects.filter(role=self.fin_role, codename=codename).update(allowed=False)

    def test_costing_open_by_default(self):
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse('costing:list')).status_code, 200)

    def test_costing_gate_blocks_when_off(self):
        self.client.force_login(self.fin)
        self._off('costing.access')
        self.assertEqual(self.client.get(reverse('costing:list')).status_code, 403)

    def test_procurement_gate(self):
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse('procurement:dashboard')).status_code, 200)
        self._off('procurement.access')
        self.assertEqual(self.client.get(reverse('procurement:dashboard')).status_code, 403)

    def test_po_gate(self):
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse('procurement:po_list')).status_code, 200)
        self._off('po.access')
        self.assertEqual(self.client.get(reverse('procurement:po_list')).status_code, 403)

    def test_dn_gate(self):
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse('procurement:dn_list')).status_code, 200)
        self._off('dn.access')
        self.assertEqual(self.client.get(reverse('procurement:dn_list')).status_code, 403)


class NavVisibilityTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.user = User.objects.create_user('navu', password='x')
        self.user.role = Role.objects.get(name=Role.SALES_REP)
        self.user.save()
        self.role = Role.objects.get(name=Role.SALES_REP)

    def test_procurement_section_visible_by_default(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertContains(resp, 'data-section="procurement"')

    def test_procurement_section_hidden_when_nav_off(self):
        RolePermission.objects.filter(role=self.role, codename='procurement.nav').update(allowed=False)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertNotContains(resp, 'data-section="procurement"')

    def test_pipeline_link_hidden_when_nav_off(self):
        RolePermission.objects.filter(role=self.role, codename='pipeline.nav').update(allowed=False)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertNotContains(resp, 'data-title="Commercial Pipeline"')


class AITeamRoleTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _allowed(self, role_name, codename):
        role = Role.objects.get(name=role_name)
        return RolePermission.objects.get(role=role, codename=codename).allowed

    def test_ai_head_manages(self):
        self.assertTrue(self._allowed(Role.AI_HEAD, 'devtracking.admin'))
        self.assertTrue(self._allowed(Role.AI_HEAD, 'devtracking.mywork'))

    def test_doers_have_mywork_not_admin(self):
        for r in (Role.AI_INTERN, Role.AI_ENGINEER, Role.AI_JUNIOR_ENGINEER):
            self.assertTrue(self._allowed(r, 'devtracking.mywork'), r)
            self.assertFalse(self._allowed(r, 'devtracking.admin'), r)


class RoleAccessReferenceTests(TestCase):
    """default_modules_by_role() maps roles to readable department labels, and
    the user form surfaces it for admins."""

    def test_sales_rep_includes_pipeline(self):
        from accounts.permissions import default_modules_by_role
        mapping = default_modules_by_role()
        self.assertIn('Commercial Pipeline', mapping['sales_rep'])
        # AI doer roles have no module access
        self.assertEqual(mapping['developer'], [])

    def test_user_form_shows_role_access_panel(self):
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        admin = User.objects.create_user('boss', password='x')
        admin.role = sa
        admin.save()
        self.client.force_login(admin)
        r = self.client.get(reverse('accounts:user_create'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Department access by role')
        self.assertContains(r, 'Commercial Pipeline')


class LoginPageTests(TestCase):
    """Login page renders a show/hide toggle for the password field, defaulting
    to masked (type="password") on first load."""

    def test_password_field_defaults_to_masked(self):
        resp = self.client.get(reverse('accounts:login'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<input type="password" name="password" id="id_login_password"')

    def test_show_password_toggle_button_present(self):
        resp = self.client.get(reverse('accounts:login'))
        self.assertContains(resp, 'id="toggle_login_password"')
        self.assertContains(resp, 'bi-eye')

    def test_forgot_password_link_present(self):
        resp = self.client.get(reverse('accounts:login'))
        self.assertContains(resp, reverse('accounts:forgot_password'))


class EmailOrUsernameLoginTests(TestCase):
    """accounts.backends.EmailOrUsernameBackend: the login form accepts
    either the username or the email address."""

    def setUp(self):
        self.user = User.objects.create_user(
            'loginuser', password='CorrectPass1!', email='loginuser@example.com')

    def _login(self, identifier, password):
        return self.client.post(reverse('accounts:login'), {
            'username': identifier, 'password': password,
        })

    def test_login_with_username_succeeds(self):
        resp = self._login('loginuser', 'CorrectPass1!')
        self.assertTrue(resp.wsgi_request.user.is_authenticated)

    def test_login_with_email_succeeds(self):
        resp = self._login('loginuser@example.com', 'CorrectPass1!')
        self.assertTrue(resp.wsgi_request.user.is_authenticated)

    def test_login_with_email_is_case_insensitive(self):
        resp = self._login('LoginUser@Example.com', 'CorrectPass1!')
        self.assertTrue(resp.wsgi_request.user.is_authenticated)

    def test_login_with_wrong_password_fails(self):
        resp = self._login('loginuser@example.com', 'WrongPass1!')
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_login_with_unknown_identifier_fails(self):
        resp = self._login('nobody@example.com', 'CorrectPass1!')
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_inactive_user_cannot_login_via_email(self):
        self.user.is_active = False
        self.user.save()
        resp = self._login('loginuser@example.com', 'CorrectPass1!')
        self.assertFalse(resp.wsgi_request.user.is_authenticated)


class ForgotPasswordViewTests(TestCase):
    """Self-service password reset entry point. The response must never
    reveal whether a given email matches a real account — same neutral
    message either way — since that's the whole point of not reusing
    send_reset_link's admin-facing per-user success/error messaging."""

    def setUp(self):
        self.user = User.objects.create_user('forgot_target', password='x', email='target@example.com')

    def test_get_renders_form(self):
        resp = self.client.get(reverse('accounts:forgot_password'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Forgot Password?')

    def test_existing_email_creates_pending_reset_request(self):
        resp = self.client.post(reverse('accounts:forgot_password'), {'email': 'target@example.com'})
        self.assertEqual(resp.status_code, 200)
        req = PasswordResetRequest.objects.get(user=self.user)
        self.assertEqual(req.status, 'pending_user')
        self.assertIsNone(req.created_by)  # nobody sent it on the user's behalf

    def test_existing_email_shows_neutral_message(self):
        resp = self.client.post(reverse('accounts:forgot_password'), {'email': 'target@example.com'})
        self.assertContains(resp, 'If an account with that email exists')

    def test_nonexistent_email_shows_same_neutral_message_no_request_created(self):
        resp = self.client.post(reverse('accounts:forgot_password'), {'email': 'nobody@example.com'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'If an account with that email exists')
        self.assertEqual(PasswordResetRequest.objects.count(), 0)

    def test_inactive_user_email_creates_no_request(self):
        self.user.is_active = False
        self.user.save()
        resp = self.client.post(reverse('accounts:forgot_password'), {'email': 'target@example.com'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'If an account with that email exists')
        self.assertEqual(PasswordResetRequest.objects.count(), 0)

    def test_email_lookup_is_case_insensitive(self):
        resp = self.client.post(reverse('accounts:forgot_password'), {'email': 'TARGET@EXAMPLE.COM'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(PasswordResetRequest.objects.filter(user=self.user).exists())

    def test_invalid_email_format_shows_form_error_not_neutral_message(self):
        resp = self.client.post(reverse('accounts:forgot_password'), {'email': 'not-an-email'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'If an account with that email exists')
        self.assertEqual(PasswordResetRequest.objects.count(), 0)

    def test_generated_token_can_be_used_on_reset_password_page(self):
        self.client.post(reverse('accounts:forgot_password'), {'email': 'target@example.com'})
        req = PasswordResetRequest.objects.get(user=self.user)
        resp = self.client.get(reverse('accounts:reset_password', args=[req.token]))
        self.assertEqual(resp.status_code, 200)


class LandingRedirectTests(TestCase):
    """Siloed roles (AI team) land on a page they can access instead of 403'ing
    on the capability-gated dashboard root."""

    def setUp(self):
        for rn in (Role.AI_HEAD, Role.AI_ENGINEER, Role.SUPER_ADMIN):
            Role.objects.get_or_create(name=rn)
        seed_default_permissions()

    def _user(self, role_name, uname):
        u = User.objects.create_user(uname, password='x')
        u.role = Role.objects.get(name=role_name)
        u.save()
        return u

    def test_ai_head_root_redirects_not_403(self):
        self.client.force_login(self._user(Role.AI_HEAD, 'ah'))
        r = self.client.get('/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/devtracking/', r['Location'])

    def test_super_admin_sees_dashboard(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_landing_url_for_roles(self):
        from accounts.permissions import landing_url_for
        self.assertEqual(landing_url_for(self._user(Role.AI_HEAD, 'ah2')),
                         reverse('devtracking:dashboard'))
        self.assertEqual(landing_url_for(self._user(Role.AI_ENGINEER, 'ae2')),
                         reverse('devtracking:my_tasks'))
        self.assertEqual(landing_url_for(self._user(Role.SUPER_ADMIN, 'sa2')),
                         reverse('dashboard:index'))


class ResetPasswordFormViewTests(TestCase):
    """The token-based reset-password apply flow (accounts.views.reset_password_form),
    shared by both the admin-triggered send_reset_link and self-service
    forgot_password entry points."""

    def setUp(self):
        self.user = User.objects.create_user(
            'resetter', password='OldPass123!', email='resetter@example.com')
        self.token = secrets.token_urlsafe(48)
        self.req = PasswordResetRequest.objects.create(
            user=self.user, token=self.token, status='pending_user')

    def _post(self, token, password1, password2):
        return self.client.post(reverse('accounts:reset_password', args=[token]), {
            'password1': password1, 'password2': password2,
        })

    def test_valid_strong_password_actually_applies(self):
        resp = self._post(self.token, 'NewSecure1!', 'NewSecure1!')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Password Changed')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewSecure1!'))
        self.assertFalse(self.user.check_password('OldPass123!'))
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')

    def test_token_is_single_use(self):
        self._post(self.token, 'NewSecure1!', 'NewSecure1!')
        resp = self._post(self.token, 'AnotherOne2@', 'AnotherOne2@')
        self.assertContains(resp, 'already been used or expired')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewSecure1!'))
        self.assertFalse(self.user.check_password('AnotherOne2@'))

    def test_mismatched_passwords_rejected(self):
        resp = self._post(self.token, 'NewSecure1!', 'Different2@')
        self.assertContains(resp, 'do not match')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_password_too_short_rejected(self):
        resp = self._post(self.token, 'Ab1!', 'Ab1!')
        self.assertContains(resp, 'too short')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_password_missing_uppercase_rejected(self):
        resp = self._post(self.token, 'lowercase1!', 'lowercase1!')
        self.assertContains(resp, 'uppercase letter')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_password_missing_lowercase_rejected(self):
        resp = self._post(self.token, 'UPPERCASE1!', 'UPPERCASE1!')
        self.assertContains(resp, 'lowercase letter')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_password_missing_number_rejected(self):
        resp = self._post(self.token, 'NoNumbers!', 'NoNumbers!')
        self.assertContains(resp, 'one number')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_password_missing_special_char_rejected(self):
        resp = self._post(self.token, 'NoSpecial123', 'NoSpecial123')
        self.assertContains(resp, 'special character')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_expired_token_rejected(self):
        PasswordResetRequest.objects.filter(pk=self.req.pk).update(
            created_at=timezone.now() - timedelta(days=8))
        resp = self._post(self.token, 'NewSecure1!', 'NewSecure1!')
        self.assertContains(resp, 'expired')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'expired')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_rejected_token_cannot_be_used(self):
        self.req.status = 'rejected'
        self.req.save()
        resp = self._post(self.token, 'NewSecure1!', 'NewSecure1!')
        self.assertContains(resp, 'already been used or expired')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123!'))

    def test_using_one_token_invalidates_other_pending_tokens_for_same_user(self):
        other_token = secrets.token_urlsafe(48)
        other_req = PasswordResetRequest.objects.create(
            user=self.user, token=other_token, status='pending_user')

        self._post(self.token, 'NewSecure1!', 'NewSecure1!')

        other_req.refresh_from_db()
        self.assertEqual(other_req.status, 'expired')
        resp = self._post(other_token, 'AnotherOne2@', 'AnotherOne2@')
        self.assertContains(resp, 'already been used or expired')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewSecure1!'))

    def test_invalid_token_404s(self):
        resp = self._post('not-a-real-token', 'NewSecure1!', 'NewSecure1!')
        self.assertEqual(resp.status_code, 404)

    def test_reset_form_has_csrf_token(self):
        resp = self.client.get(reverse('accounts:reset_password', args=[self.token]))
        self.assertContains(resp, 'csrfmiddlewaretoken')

    def test_password_strength_checklist_rendered(self):
        resp = self.client.get(reverse('accounts:reset_password', args=[self.token]))
        self.assertContains(resp, 'pwd-strength-checklist')


class UserCreationFormPasswordValidatorTests(TestCase):
    """CustomUserCreationForm (used for admin-created new users) must reject
    passwords that don't meet the composition rules, via the same
    AUTH_PASSWORD_VALIDATORS pipeline as the reset-password flow."""

    def _form(self, password):
        return CustomUserCreationForm(data={
            'username': 'newhire', 'email': 'newhire@example.com',
            'first_name': 'New', 'last_name': 'Hire',
            'password1': password, 'password2': password,
        })

    def test_missing_special_char_rejected(self):
        form = self._form('NoSpecial123')
        self.assertFalse(form.is_valid())
        self.assertIn('special character', str(form.errors))

    def test_missing_uppercase_rejected(self):
        form = self._form('nouppercase1!')
        self.assertFalse(form.is_valid())
        self.assertIn('uppercase letter', str(form.errors))

    def test_too_short_rejected(self):
        form = self._form('Ab1!')
        self.assertFalse(form.is_valid())
        self.assertIn('too short', str(form.errors))

    def test_strong_password_passes_validators(self):
        form = self._form('StrongPass1!')
        form.is_valid()
        self.assertNotIn('password1', form.errors)
        self.assertNotIn('password2', form.errors)
