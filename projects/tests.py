from types import SimpleNamespace

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Region, ProjectStatus, Project
from costing.models import CostingSheet, most_advanced_stage, pipeline_stage_badge


class PipelineStageHelperTests(TestCase):
    def _sheet(self, stage):
        return SimpleNamespace(workflow_stage=stage)

    def test_most_advanced_picks_furthest_along(self):
        sheets = [self._sheet('bom_in_progress'),
                  self._sheet('finance_review'),
                  self._sheet('finalized')]
        self.assertEqual(most_advanced_stage(sheets), 'finance_review')

    def test_most_advanced_none_when_empty(self):
        self.assertIsNone(most_advanced_stage([]))

    def test_badge_label_and_css(self):
        badge = pipeline_stage_badge([self._sheet('ready_for_costing')])
        self.assertEqual(badge['label'], 'Handed to Sales')
        self.assertIn('bg-info', badge['css'])

    def test_badge_none_when_no_sheets(self):
        self.assertIsNone(pipeline_stage_badge([]))


class PipelineListBadgeTests(TestCase):
    def setUp(self):
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('boss', password='x')
        self.user.role = role
        self.user.region = self.region
        self.user.save()
        self.client.force_login(self.user)

    def _project(self, ref):
        return Project.objects.create(project_name='Project ' + ref, proposal_reference=ref,
                                      status=self.status, region=self.region)

    def test_list_shows_workflow_badge(self):
        proj = self._project('REF-1')
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    workflow_stage='finalized')
        resp = self.client.get(reverse('projects:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Sales finalised')

    def test_list_shows_not_started_without_sheet(self):
        self._project('REF-2')
        resp = self.client.get(reverse('projects:list'))
        self.assertContains(resp, 'Not started')

    def test_list_shows_most_advanced_when_multiple_sheets(self):
        proj = self._project('REF-3')
        CostingSheet.objects.create(title='S1', project=proj, created_by=self.user,
                                    workflow_stage='bom_in_progress')
        CostingSheet.objects.create(title='S2', project=proj, created_by=self.user,
                                    workflow_stage='finance_review')
        resp = self.client.get(reverse('projects:list'))
        self.assertContains(resp, 'Handed to Finance')
