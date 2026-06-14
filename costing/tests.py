from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Region, ProjectStatus, Project
from costing.models import CostingSheet


class CostingProjectAutofillTests(TestCase):
    """The costing form auto-fills the PDF-header fields from the selected
    project via a `project_data_json` map embedded in the page. These guard that
    the map is present (and carries project data) on BOTH the create and edit
    forms — the edit form previously shipped an empty map, so auto-fill was dead.
    """

    def setUp(self):
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('boss', password='x')
        self.user.role = role
        self.user.region = self.region
        self.user.save()
        # 'Acme Industries' (the customer) only ever appears via project_data_json,
        # so finding it in the page proves the auto-fill map carries this project.
        self.project = Project.objects.create(
            project_name='Acme Plant Upgrade', proposal_reference='LEAP-2026-014',
            customer='Acme Industries', end_user='Acme Power', contact_with='Jane Doe',
            status=self.status, region=self.region)
        self.client.force_login(self.user)

    def test_create_form_embeds_project_autofill_data(self):
        resp = self.client.get(reverse('costing:create'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Acme Industries', resp.content.decode())

    def test_edit_form_embeds_project_autofill_data(self):
        sheet = CostingSheet.objects.create(title='Sheet 1', project=self.project,
                                            created_by=self.user)
        resp = self.client.get(reverse('costing:edit', kwargs={'pk': sheet.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Acme Industries', resp.content.decode())  # was absent before the fix


class EnforceFlagTests(TestCase):
    def test_new_sheet_defaults_strict(self):
        from projects.models import Region, ProjectStatus, Project
        from accounts.models import User
        region = Region.objects.create(name='R', code='LNA', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='active')
        proj = Project.objects.create(project_name='P', proposal_reference='REF-X',
                                      status=status, region=region)
        u = User.objects.create_user('u', password='x')
        from costing.models import CostingSheet
        sheet = CostingSheet.objects.create(title='S', project=proj, created_by=u)
        self.assertTrue(sheet.enforce_stage_barriers)
