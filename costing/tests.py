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


class StrictGateTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        from projects.models import Region, ProjectStatus, Project
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(project_name='P', proposal_reference='REF-G',
                                              status=self.status, region=self.region)

        def mkuser(username, role_name):
            role, _ = Role.objects.get_or_create(name=role_name)
            u = User.objects.create_user(username, password='x')
            u.role = role; u.region = self.region; u.save()
            return u
        self.superadmin = mkuser('sa', Role.SUPER_ADMIN)
        self.proposal = mkuser('pr', Role.PROPOSAL_REP)
        self.sales = mkuser('sr', Role.SALES_REP)
        self.finance = mkuser('fr', Role.FINANCE_REP)

    def _sheet(self, stage, strict=True):
        from costing.models import CostingSheet
        return CostingSheet.objects.create(title='S', project=self.project,
                                           created_by=self.proposal, workflow_stage=stage,
                                           enforce_stage_barriers=strict)

    def _can(self, user, sheet):
        from costing.views import _user_can_edit_sheet
        return _user_can_edit_sheet(user, sheet)

    def test_bom_stage_proposal_only(self):
        s = self._sheet('bom_in_progress')
        self.assertTrue(self._can(self.proposal, s))
        self.assertFalse(self._can(self.sales, s))
        self.assertTrue(self._can(self.superadmin, s))

    def test_ready_for_costing_locked_for_all(self):
        s = self._sheet('ready_for_costing')
        self.assertFalse(self._can(self.proposal, s))
        self.assertFalse(self._can(self.sales, s))
        self.assertTrue(self._can(self.superadmin, s))  # only override

    def test_costing_stage_sales_only(self):
        s = self._sheet('costing_in_progress')
        self.assertTrue(self._can(self.sales, s))
        self.assertFalse(self._can(self.proposal, s))

    def test_costing_stage_sales_out_of_region_blocked(self):
        from projects.models import Region
        other = Region.objects.create(name='UK', code='UK', currency='GBP')
        self.sales.region = other; self.sales.save()
        s = self._sheet('costing_in_progress')
        self.assertFalse(self._can(self.sales, s))

    def test_finalized_stage_sales_only(self):
        s = self._sheet('finalized')
        self.assertTrue(self._can(self.sales, s))
        self.assertFalse(self._can(self.proposal, s))

    def test_finance_stage_unchanged(self):
        s = self._sheet('finance_review')
        self.assertTrue(self._can(self.finance, s))
        self.assertFalse(self._can(self.sales, s))

    def test_finance_approved_locked(self):
        s = self._sheet('finance_approved')
        self.assertFalse(self._can(self.finance, s))
        self.assertTrue(self._can(self.superadmin, s))

    def test_grandfathered_sheet_keeps_lenient_rules(self):
        # Non-strict sheet: proposal can edit even at costing_in_progress (legacy global proposal rule).
        s = self._sheet('costing_in_progress', strict=False)
        self.assertTrue(self._can(self.proposal, s))


class BarrierEndpointTests(TestCase):
    """End-to-end HTTP tests proving _user_can_edit_sheet gates real mutation
    endpoints: 403 out-of-stage, 200 in-stage.

    Endpoint used: ajax_add_sow_item (costing:add_sow_item, POST pk)
      - requires: description (non-empty)
      - optional: quantity (defaults to 1)
      - success: HTTP 200, JSON {ok: true}
      - gate: _user_can_edit_sheet -> 403 on failure
    """

    def setUp(self):
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='P', proposal_reference='REF-E',
            status=self.status, region=self.region)

        def mkuser(username, role_name):
            role, _ = Role.objects.get_or_create(name=role_name)
            u = User.objects.create_user(username, password='x')
            u.role = role
            u.region = self.region
            u.save()
            return u

        self.proposal = mkuser('pr', Role.PROPOSAL_REP)
        self.sales = mkuser('sr', Role.SALES_REP)

    def _sheet(self, stage):
        return CostingSheet.objects.create(
            title='S', project=self.project,
            created_by=self.proposal, workflow_stage=stage)

    def _mutate(self, user, sheet):
        """POST to costing:add_sow_item - the real _user_can_edit_sheet-gated endpoint."""
        self.client.force_login(user)
        return self.client.post(
            reverse('costing:add_sow_item', kwargs={'pk': sheet.pk}),
            {'description': 'Cabling works', 'quantity': '1'})

    def test_sales_blocked_during_bom(self):
        self.assertEqual(self._mutate(self.sales, self._sheet('bom_in_progress')).status_code, 403)

    def test_proposal_allowed_during_bom(self):
        self.assertEqual(self._mutate(self.proposal, self._sheet('bom_in_progress')).status_code, 200)

    def test_ready_for_costing_locked_for_all(self):
        s = self._sheet('ready_for_costing')
        self.assertEqual(self._mutate(self.sales, s).status_code, 403)
        self.assertEqual(self._mutate(self.proposal, s).status_code, 403)

    def test_sales_allowed_during_costing_proposal_blocked(self):
        s = self._sheet('costing_in_progress')
        self.assertEqual(self._mutate(self.sales, s).status_code, 200)
        self.assertEqual(self._mutate(self.proposal, s).status_code, 403)


class DetailCanEditContextTests(TestCase):
    def setUp(self):
        from accounts.models import Role, User
        from projects.models import Region, ProjectStatus, Project
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(project_name='P', proposal_reference='REF-D',
                                              status=self.status, region=self.region)

        def mkuser(username, role_name):
            role, _ = Role.objects.get_or_create(name=role_name)
            u = User.objects.create_user(username, password='x')
            u.role = role; u.region = self.region; u.save()
            return u
        self.proposal = mkuser('pr', Role.PROPOSAL_REP)
        self.sales = mkuser('sr', Role.SALES_REP)

    def _sheet(self, stage):
        from costing.models import CostingSheet
        return CostingSheet.objects.create(title='S', project=self.project,
                                           created_by=self.proposal, workflow_stage=stage)

    def test_context_can_edit_per_role(self):
        s = self._sheet('bom_in_progress')
        self.client.force_login(self.sales)
        resp = self.client.get(reverse('costing:detail', kwargs={'pk': s.pk}))
        self.assertFalse(resp.context['can_edit'])
        self.assertTrue(resp.context['edit_lock_reason'])  # non-empty reason shown
        self.client.force_login(self.proposal)
        resp = self.client.get(reverse('costing:detail', kwargs={'pk': s.pk}))
        self.assertTrue(resp.context['can_edit'])
