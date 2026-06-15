from datetime import date, timedelta
from django.test import TestCase
from django.utils import timezone
from accounts.models import Role, User


def mkuser(username, role_name):
    role, _ = Role.objects.get_or_create(name=role_name)
    u = User.objects.create_user(username, password='x'); u.role = role; u.save()
    return u


class DevTaskModelTests(TestCase):
    def setUp(self):
        self.admin = mkuser('adm', Role.ADMIN)
        self.dev = mkuser('dev', Role.DEVELOPER)

    def _task(self, **kw):
        from devtracking.models import DevTask
        kw.setdefault('title', 'T'); kw.setdefault('developer', self.dev)
        kw.setdefault('assigned_by', self.admin)
        return DevTask.objects.create(**kw)

    def test_mark_started_stamps_once(self):
        t = self._task()
        t.mark_started(); first = t.started_at
        self.assertIsNotNone(first); self.assertEqual(t.status, 'in_progress')
        t.mark_started()
        self.assertEqual(t.started_at, first)

    def test_mark_done_stamps_and_on_time(self):
        t = self._task(due_date=date.today() + timedelta(days=2))
        t.mark_started(); t.mark_done()
        self.assertEqual(t.status, 'done')
        self.assertIsNotNone(t.completed_at)
        self.assertTrue(t.on_time)

    def test_overdue(self):
        t = self._task(due_date=date.today() - timedelta(days=1))
        self.assertTrue(t.is_overdue)
        t.mark_started(); t.mark_done()
        self.assertFalse(t.is_overdue)


class CapabilityTests(TestCase):
    # The capability model is PER-CODENAME granular: User.has_capability checks
    # the exact codename against the role's allowed RolePermission rows. Those
    # rows come from seed_default_permissions(). In a fresh test DB the `admin`
    # role is not migration-created (only super_admin / procurement / proposal /
    # finance / developer are), so mkuser() would create an unseeded role. We
    # seed here — exactly as accounts.tests.SeedTests does — so the granted
    # codenames exist before we assert on them.
    def setUp(self):
        from accounts.permissions import seed_default_permissions
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def test_developer_has_mywork_not_admin(self):
        dev = mkuser('d2', Role.DEVELOPER)
        self.assertTrue(dev.has_capability('devtracking.mywork'))
        self.assertFalse(dev.has_capability('devtracking.admin'))

    def test_admin_has_both(self):
        adm = mkuser('a2', Role.ADMIN)
        self.assertTrue(adm.has_capability('devtracking.admin'))
        self.assertTrue(adm.has_capability('devtracking.mywork'))


class AssignFlowTests(TestCase):
    def setUp(self):
        # Seed RolePermission rows exactly as CapabilityTests does so the admin
        # role actually holds devtracking.admin in the fresh test DB.
        from accounts.permissions import seed_default_permissions
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.admin = mkuser('adm', Role.ADMIN); self.dev = mkuser('dev', Role.DEVELOPER)

    def test_assign_creates_task_and_notifies(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        self.client.post(reverse('devtracking:assign'), {
            'developer': self.dev.pk, 'title': 'Build login', 'description': '',
            'priority': 'high', 'estimated_hours': '6', 'due_date': '2026-07-01', 'github_url': ''})
        from devtracking.models import DevTask
        self.assertEqual(DevTask.objects.filter(developer=self.dev, title='Build login').count(), 1)

    def test_developer_cannot_open_assign(self):
        from django.urls import reverse
        self.client.force_login(self.dev)
        resp = self.client.get(reverse('devtracking:assign'))
        self.assertIn(resp.status_code, (302, 403))

    def test_admin_dashboard_ok(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('devtracking:dashboard')).status_code, 200)


class MyTasksActionTests(TestCase):
    def setUp(self):
        # seed capabilities like AssignFlowTests.setUp does
        from accounts.permissions import seed_default_permissions
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.admin = mkuser('adm', Role.ADMIN)
        self.dev = mkuser('dev', Role.DEVELOPER)
        self.other = mkuser('other', Role.DEVELOPER)
        from devtracking.models import DevTask
        self.task = DevTask.objects.create(title='T', developer=self.dev, assigned_by=self.admin)

    def _act(self, user, action, note=''):
        from django.urls import reverse
        self.client.force_login(user)
        return self.client.post(reverse('devtracking:task_action', kwargs={'pk': self.task.pk}),
                                {'action': action, 'note': note})

    def test_start_then_done_stamps_and_logs(self):
        self._act(self.dev, 'start', 'beginning')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'in_progress')
        self.assertIsNotNone(self.task.started_at)
        self._act(self.dev, 'done', 'finished')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'done')
        self.assertIsNotNone(self.task.completed_at)
        from devtracking.models import DevTaskUpdate
        self.assertEqual(DevTaskUpdate.objects.filter(task=self.task).count(), 2)

    def test_other_dev_cannot_act(self):
        self.assertEqual(self._act(self.other, 'start').status_code, 403)

    def test_my_tasks_page_ok(self):
        from django.urls import reverse
        self.client.force_login(self.dev)
        self.assertEqual(self.client.get(reverse('devtracking:my_tasks')).status_code, 200)


class DigestTests(TestCase):
    def setUp(self):
        from accounts.permissions import seed_default_permissions
        for name, _l in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.admin = mkuser('adm', Role.ADMIN); self.dev = mkuser('dev', Role.DEVELOPER)
        from devtracking.models import DevTask
        DevTask.objects.create(title='A', developer=self.dev, assigned_by=self.admin, status='done')

    def test_context_counts(self):
        from devtracking.ai import build_digest_context
        ctx = build_digest_context()
        self.assertTrue(any(d['done'] >= 1 for d in ctx['developers']))

    def test_generate_fallback_without_key(self):
        from django.test import override_settings
        with override_settings(ANTHROPIC_API_KEY=''):
            from devtracking.ai import generate_admin_digest
            dg = generate_admin_digest(generated_by=self.admin)
            self.assertTrue(dg.content)
            self.assertEqual(dg.model_used, '')

    def test_generate_now_view(self):
        from django.urls import reverse
        from django.test import override_settings
        with override_settings(ANTHROPIC_API_KEY=''):
            self.client.force_login(self.admin)
            resp = self.client.post(reverse('devtracking:generate_now'))
            self.assertEqual(resp.status_code, 302)
            from devtracking.models import DevDigest
            self.assertEqual(DevDigest.objects.count(), 1)

    def test_command_runs(self):
        from django.core.management import call_command
        from django.test import override_settings
        with override_settings(ANTHROPIC_API_KEY=''):
            call_command('generate_dev_digest')
        from devtracking.models import DevDigest
        self.assertEqual(DevDigest.objects.count(), 1)


class GithubStatusTests(TestCase):
    def test_parse_pr_url(self):
        from devtracking.github import parse_pr_url
        self.assertEqual(parse_pr_url('https://github.com/acme/repo/pull/42'), ('acme', 'repo', 42))
        self.assertIsNone(parse_pr_url('https://example.com/x'))
        self.assertIsNone(parse_pr_url(''))

    def test_refresh_writes_cache(self):
        from unittest.mock import patch
        from devtracking.models import DevTask
        dev = mkuser('dev', Role.DEVELOPER)
        t = DevTask.objects.create(title='T', developer=dev,
                                   github_url='https://github.com/acme/repo/pull/7')
        with patch('devtracking.github.fetch_pr_status',
                   return_value={'state': 'merged', 'commits': 3, 'title': 'Add auth'}):
            from devtracking.github import refresh_task_github
            self.assertTrue(refresh_task_github(t))
        t.refresh_from_db()
        self.assertEqual(t.gh_state, 'merged'); self.assertEqual(t.gh_commits, 3)
        self.assertEqual(t.gh_title, 'Add auth'); self.assertIsNotNone(t.gh_checked_at)

    def test_fetch_non_pr_url_returns_none(self):
        from devtracking.github import fetch_pr_status
        self.assertIsNone(fetch_pr_status('not a url'))  # no network call made

    def test_refresh_if_stale_noop_without_url(self):
        from devtracking.models import DevTask
        from devtracking.github import refresh_if_stale
        dev = mkuser('d3', Role.DEVELOPER)
        t = DevTask.objects.create(title='T', developer=dev)  # no github_url
        refresh_if_stale(t)  # must not raise / not call network
        self.assertEqual(t.gh_state, '')


class AssignableRolesTests(TestCase):
    def test_ai_roles_are_assignable(self):
        from devtracking.forms import DevTaskForm
        eng = mkuser('eng', Role.AI_ENGINEER)
        sales = mkuser('sales', Role.SALES_REP)
        qs = DevTaskForm().fields['developer'].queryset
        self.assertIn(eng, qs)
        self.assertNotIn(sales, qs)
