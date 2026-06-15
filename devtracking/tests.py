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
