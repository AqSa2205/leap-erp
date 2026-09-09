import json

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Project, ProjectStatus, Region

from .models import ResponsibilityMatrix


class PmDashboardAccessTests(TestCase):
    """pm_dashboard is capability-gated (pm_dashboard.access), independent of
    any specific role — see accounts/permissions.py DEFAULT_MODULE_ACCESS."""

    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.dev_role, _ = Role.objects.get_or_create(name=Role.DEVELOPER)
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='Test Project', status=self.status, region=self.region,
            po_number='PO-123')
        self.pm_user = User.objects.create_user(
            'pm1', password='pw', role=self.pm_role, region=self.region)
        self.dev_user = User.objects.create_user(
            'dev1', password='pw', role=self.dev_role, region=self.region)

    def test_role_without_capability_is_denied(self):
        self.client.force_login(self.dev_user)
        r = self.client.get(reverse('pm_dashboard:index'))
        self.assertEqual(r.status_code, 403)

    def test_project_manager_can_open_dashboard_and_project(self):
        self.client.force_login(self.pm_user)
        self.assertEqual(self.client.get(reverse('pm_dashboard:index')).status_code, 200)
        r = self.client.get(reverse('pm_dashboard:project_detail', args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'PO-123')


class ResponsibilityMatrixEditTests(TestCase):
    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='Test Project', status=self.status, region=self.region)
        self.pm_user = User.objects.create_user(
            'pm2', password='pw', role=self.pm_role, region=self.region)

    def test_edit_saves_sanitized_grid_and_drops_empty_rows(self):
        self.client.force_login(self.pm_user)
        url = reverse('pm_dashboard:responsibility_matrix_edit', args=[self.project.pk])
        payload = {
            'columns_json': json.dumps([{'key': 'c1', 'name': 'Task'}, {'key': 'c2', 'name': 'Owner'}]),
            'rows_json': json.dumps([
                {'cells': {'c1': {'text': 'Kickoff'}, 'c2': {'text': 'PM'}}},
                {'cells': {'c1': {'text': ''}, 'c2': {'text': ''}}},  # all-empty row, should be dropped
            ]),
        }
        r = self.client.post(url, payload)
        self.assertRedirects(r, reverse('pm_dashboard:project_detail', args=[self.project.pk]))
        matrix = ResponsibilityMatrix.objects.get(project=self.project)
        self.assertEqual(len(matrix.rows), 1)
        self.assertEqual(matrix.rows[0]['cells']['c1']['text'], 'Kickoff')
        self.assertEqual(matrix.updated_by, self.pm_user)


class CommunicationMatrixExportTests(TestCase):
    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='Test Project', status=self.status, region=self.region)
        self.pm_user = User.objects.create_user(
            'pm3', password='pw', role=self.pm_role, region=self.region)

    def test_export_pdf(self):
        self.client.force_login(self.pm_user)
        url = reverse('pm_dashboard:communication_matrix_export_pdf', args=[self.project.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
