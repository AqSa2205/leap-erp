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


class PipelineVisibilityTests(TestCase):
    """Sales + proposal teams see their whole region's pipeline (view), but can
    only edit projects they own. Creating a project notifies the region team."""

    def setUp(self):
        self.lna = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.uk = Region.objects.create(name='UK', code='UK', currency='GBP')
        self.status = ProjectStatus.objects.create(name='Open', category='active')

        def mkuser(username, role_name, region):
            role, _ = Role.objects.get_or_create(name=role_name)
            u = User.objects.create_user(username, password='x')
            u.role = role
            u.region = region
            u.save()
            return u

        self.admin = mkuser('adm', Role.ADMIN, self.lna)
        self.sales = mkuser('sales', Role.SALES_REP, self.lna)
        self.proposal = mkuser('prop', Role.PROPOSAL_REP, self.lna)
        # Seed the baseline role grants (pipeline.access etc.) so the
        # capability-gated pipeline views are reachable for these roles.
        from accounts.permissions import seed_default_permissions
        seed_default_permissions()
        # Region projects owned by the admin, not the sales/proposal users.
        self.lna_proj = Project.objects.create(
            project_name='LNA P', proposal_reference='LNA-1',
            status=self.status, region=self.lna, owner=self.admin)
        self.uk_proj = Project.objects.create(
            project_name='UK P', proposal_reference='UK-1',
            status=self.status, region=self.uk, owner=self.admin)

    def test_sales_sees_region_pipeline_not_other_region(self):
        self.client.force_login(self.sales)
        ids = {p.pk for p in self.client.get(reverse('projects:list')).context['projects']}
        self.assertIn(self.lna_proj.pk, ids)        # region project visible now
        self.assertNotIn(self.uk_proj.pk, ids)      # other region stays hidden

    def test_proposal_sees_region_pipeline(self):
        self.client.force_login(self.proposal)
        ids = {p.pk for p in self.client.get(reverse('projects:list')).context['projects']}
        self.assertIn(self.lna_proj.pk, ids)

    def test_sales_can_view_region_project_detail(self):
        self.client.force_login(self.sales)
        r = self.client.get(reverse('projects:detail', kwargs={'pk': self.lna_proj.pk}))
        self.assertEqual(r.status_code, 200)

    def test_sales_cannot_edit_unowned_project(self):
        self.client.force_login(self.sales)
        r = self.client.get(reverse('projects:edit', kwargs={'pk': self.lna_proj.pk}))
        self.assertEqual(r.status_code, 404)  # not in the owner-scoped edit queryset

    def test_sales_can_edit_own_project(self):
        own = Project.objects.create(
            project_name='Own', proposal_reference='LNA-OWN',
            status=self.status, region=self.lna, owner=self.sales)
        self.client.force_login(self.sales)
        r = self.client.get(reverse('projects:edit', kwargs={'pk': own.pk}))
        self.assertEqual(r.status_code, 200)

    def _create_post(self, ref):
        return {
            'project_name': 'New Pipeline', 'proposal_reference': ref,
            'status': self.status.pk, 'region': self.lna.pk, 'owner': '',
            'estimated_value': '0', 'estimated_value_usd': '0',
            'estimated_value_per_annum': '0', 'estimated_gp': '0',
            'actual_sales': '0', 'success_quotient': '0',
            'client_rfq_reference': '', 'po_number': '', 'customer': '',
            'end_user': '', 'project_stage': '', 'year': '', 'po_award_quarter': '',
            'contact_with': '', 'remarks': '', 'notes': '', 'portal_url': '',
        }

    def test_create_notifies_region_sales_and_proposal(self):
        from notifications.models import Notification
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('projects:create'), self._create_post('LNA-NEW'))
        self.assertEqual(resp.status_code, 302)  # created -> redirect
        proj = Project.objects.get(proposal_reference='LNA-NEW')
        recips = set(Notification.objects.filter(target_object_id=proj.pk)
                     .values_list('recipient__username', flat=True))
        self.assertIn('sales', recips)     # region sales notified
        self.assertIn('prop', recips)      # region proposal notified
        self.assertNotIn('adm', recips)    # actor not self-notified
