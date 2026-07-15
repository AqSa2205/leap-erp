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


class PipelineValueSummaryTests(TestCase):
    """The Won / Hot-Leads headline split on the pipeline list.

    Won  = closed contract value; Hot Leads = costing value (both converted
    to SAR). hot_no_costing_count = hot leads without a priced costing sheet.
    """

    def setUp(self):
        from decimal import Decimal
        from costing.models import ExchangeRate
        self.Decimal = Decimal
        self.lna = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.uk = Region.objects.create(name='UK', code='UK', currency='GBP')
        self.won = ProjectStatus.objects.create(name='Won', category='won')
        self.hot = ProjectStatus.objects.create(name='Hot', category='hot_lead')
        self.active = ProjectStatus.objects.create(name='Open', category='active')
        # rate_to_usd = units per 1 USD. (Rows may be migration-seeded.)
        ExchangeRate.objects.update_or_create(
            currency_code='SAR', defaults={'rate_to_usd': Decimal('3.75')})
        ExchangeRate.objects.update_or_create(
            currency_code='GBP', defaults={'rate_to_usd': Decimal('0.80')})
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('boss', password='x')
        self.user.role = role
        self.user.region = self.lna
        self.user.save()
        self.client.force_login(self.user)

    def _summary(self):
        from costing.models import ExchangeRate
        from projects.views import _pipeline_value_summary
        rates = {r.currency_code: r.rate_to_usd for r in ExchangeRate.objects.all()}
        qs = Project.objects.prefetch_related('costing_sheets__sections__line_items')
        return _pipeline_value_summary(qs, rates)

    def test_won_uses_costing_value_when_priced(self):
        D = self.Decimal
        proj = Project.objects.create(project_name='W1', proposal_reference='W-1',
                                      status=self.won, region=self.lna)
        # scope_of_work_total feeds contract_total without building line items.
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR', scope_of_work_total=D('10000'))
        s = self._summary()
        self.assertEqual(s['won_count'], 1)
        self.assertEqual(s['won_value_sar'], D('10000'))

    def test_won_falls_back_to_actual_sales(self):
        D = self.Decimal
        Project.objects.create(project_name='W2', proposal_reference='W-2',
                               status=self.won, region=self.lna, actual_sales=D('5000'))
        s = self._summary()
        self.assertEqual(s['won_count'], 1)
        self.assertEqual(s['won_value_sar'], D('5000'))

    def test_hot_lead_without_costing_is_counted(self):
        D = self.Decimal
        Project.objects.create(project_name='H1', proposal_reference='H-1',
                               status=self.hot, region=self.lna, estimated_value=D('2000'))
        s = self._summary()
        self.assertEqual(s['hot_count'], 1)
        self.assertEqual(s['hot_no_costing_count'], 1)   # estimate, not costing
        self.assertEqual(s['hot_value_sar'], D('2000'))

    def test_hot_lead_with_costing_not_flagged(self):
        D = self.Decimal
        proj = Project.objects.create(project_name='H2', proposal_reference='H-2',
                                      status=self.hot, region=self.lna)
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR', scope_of_work_total=D('7000'))
        s = self._summary()
        self.assertEqual(s['hot_count'], 1)
        self.assertEqual(s['hot_no_costing_count'], 0)   # priced sheet exists
        self.assertEqual(s['hot_value_sar'], D('7000'))

    def test_gbp_won_converts_to_sar(self):
        D = self.Decimal
        # UK won project, actual_sales in GBP → SAR = 800 / 0.80 * 3.75 = 3750.
        Project.objects.create(project_name='UKW', proposal_reference='UK-W',
                               status=self.won, region=self.uk, actual_sales=D('800'))
        s = self._summary()
        self.assertEqual(s['won_value_sar'], D('3750'))

    def test_active_projects_excluded_from_both_buckets(self):
        D = self.Decimal
        Project.objects.create(project_name='A1', proposal_reference='A-1',
                               status=self.active, region=self.lna,
                               estimated_value=D('9999'), actual_sales=D('9999'))
        s = self._summary()
        self.assertEqual(s['won_count'], 0)
        self.assertEqual(s['hot_count'], 0)
        self.assertEqual(s['won_value_sar'], D('0'))
        self.assertEqual(s['hot_value_sar'], D('0'))

    def test_list_page_renders_split_cards(self):
        D = self.Decimal
        Project.objects.create(project_name='W', proposal_reference='W-9',
                               status=self.won, region=self.lna, actual_sales=D('1000'))
        resp = self.client.get(reverse('projects:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Won Value')
        self.assertContains(resp, 'Hot Leads Value')


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

    def test_detail_shows_edit_button_when_allowed(self):
        edit_url = reverse('projects:edit', kwargs={'pk': self.lna_proj.pk})
        # Admin owns / can edit the region project → Edit button present.
        self.client.force_login(self.admin)
        r = self.client.get(reverse('projects:detail', kwargs={'pk': self.lna_proj.pk}))
        self.assertTrue(r.context['can_edit'])
        self.assertContains(r, edit_url)
        # Sales rep can view but doesn't own it → no Edit button.
        self.client.force_login(self.sales)
        r = self.client.get(reverse('projects:detail', kwargs={'pk': self.lna_proj.pk}))
        self.assertFalse(r.context['can_edit'])
        self.assertNotContains(r, edit_url)

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

    def test_create_autocreates_bom_not_started_sheet(self):
        # A new pipeline project auto-gets a costing sheet at "BOM not started"
        # so it appears on the costing list for the proposal team to pick up.
        from costing.models import CostingSheet
        self.client.force_login(self.admin)
        self.client.post(reverse('projects:create'), self._create_post('LNA-BOM'))
        proj = Project.objects.get(project_name='New Pipeline')
        sheet = CostingSheet.objects.get(project=proj)
        self.assertEqual(sheet.workflow_stage, 'bom_not_started')
        self.assertEqual(sheet.created_by, self.admin)

    def test_create_notifies_region_sales_and_proposal(self):
        from notifications.models import Notification
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('projects:create'), self._create_post('LNA-NEW'))
        self.assertEqual(resp.status_code, 302)  # created -> redirect
        # LNA reference is auto-generated, so look the project up by name.
        proj = Project.objects.get(project_name='New Pipeline')
        recips = set(Notification.objects.filter(target_object_id=proj.pk)
                     .values_list('recipient__username', flat=True))
        self.assertIn('sales', recips)     # region sales notified
        self.assertIn('prop', recips)      # region proposal notified
        self.assertNotIn('adm', recips)    # actor not self-notified


class LnaReferenceTests(TestCase):
    """LNA proposal references auto-generate as 'LNA <n> - <name>', n starting
    at 2870 and auto-incrementing; the name mirrors renames; legacy refs and
    other regions are untouched."""

    def setUp(self):
        self.lna = Region.objects.create(name='LNA', code='LNA', currency='SAR')
        self.uk = Region.objects.create(name='UK', code='UK', currency='GBP')
        self.status = ProjectStatus.objects.create(name='Open', category='active')

    def _data(self, **over):
        d = {
            'project_name': 'CCTV Upgrade', 'proposal_reference': '',
            'status': self.status.pk, 'region': self.lna.pk, 'owner': '',
            'estimated_value': '0', 'estimated_value_usd': '0',
            'estimated_value_per_annum': '0', 'estimated_gp': '0',
            'actual_sales': '0', 'success_quotient': '0',
            'client_rfq_reference': '', 'po_number': '', 'customer': '',
            'end_user': '', 'project_stage': '', 'year': '', 'po_award_quarter': '',
            'contact_with': '', 'remarks': '', 'notes': '', 'portal_url': '',
        }
        d.update(over)
        return d

    def _save(self, instance=None, **over):
        from projects.forms import ProjectForm
        f = ProjectForm(data=self._data(**over), instance=instance)
        self.assertTrue(f.is_valid(), f.errors)
        return f.save()

    def test_new_lna_starts_at_2870(self):
        p = self._save(project_name='CCTV Upgrade')
        self.assertEqual(p.proposal_reference, 'LNA 2870 - CCTV Upgrade')

    def test_lna_auto_increments(self):
        self._save(project_name='First')
        p2 = self._save(project_name='Second')
        self.assertEqual(p2.proposal_reference, 'LNA 2871 - Second')

    def test_rename_mirrors_name_keeps_number(self):
        p = self._save(project_name='Old Name')
        p2 = self._save(instance=p, project_name='New Name')
        self.assertEqual(p2.proposal_reference, 'LNA 2870 - New Name')

    def test_legacy_lna_reference_converts_and_keeps_number(self):
        # Legacy 'LNA-2289' becomes name-bearing on rename, keeping its number.
        legacy = Project.objects.create(
            project_name='Old', proposal_reference='LNA-2289',
            status=self.status, region=self.lna)
        p = self._save(instance=legacy, project_name='Old Renamed')
        self.assertEqual(p.proposal_reference, 'LNA 2289 - Old Renamed')

    def test_legacy_lna_reference_preserves_revision(self):
        # 'LNA02158-R03' -> 'LNA 2158 - <name> (R03)', revision kept.
        legacy = Project.objects.create(
            project_name='Pkg 3', proposal_reference='LNA02158-R03',
            status=self.status, region=self.lna)
        legacy.refresh_from_db()  # save() normalises on create
        self.assertEqual(legacy.proposal_reference, 'LNA 2158 - Pkg 3 (R03)')
        legacy.project_name = 'Pkg 3 Renamed'
        legacy.save()
        legacy.refresh_from_db()
        self.assertEqual(legacy.proposal_reference, 'LNA 2158 - Pkg 3 Renamed (R03)')

    def test_revision_is_editable(self):
        p = Project.objects.create(
            project_name='Alpha', proposal_reference='LNA 2158 - Alpha (R03)',
            status=self.status, region=self.lna)
        # change revision R03 -> R04
        out = self._save(instance=p, project_name='Alpha', lna_revision='R04')
        self.assertEqual(out.proposal_reference, 'LNA 2158 - Alpha (R04)')
        # bare number normalises to R5
        out = self._save(instance=p, project_name='Alpha', lna_revision='5')
        self.assertEqual(out.proposal_reference, 'LNA 2158 - Alpha (R5)')
        # clearing removes the revision
        out = self._save(instance=p, project_name='Alpha', lna_revision='')
        self.assertEqual(out.proposal_reference, 'LNA 2158 - Alpha')

    def test_auto_number_fills_gap_below_existing(self):
        from projects.models import next_lna_reference_number
        # 2870–2876 taken, then a project at 2890 (e.g. from an earlier skip).
        for i in range(2870, 2877):
            Project.objects.create(
                project_name=f'P{i}', proposal_reference=f'LNA {i} - P{i}',
                status=self.status, region=self.lna)
        Project.objects.create(
            project_name='High', proposal_reference='LNA 2890 - High',
            status=self.status, region=self.lna)
        # Gap 2877–2889 is reused rather than jumping to 2891.
        self.assertEqual(next_lna_reference_number(), 2877)

    def test_auto_number_never_collides_with_imports(self):
        from projects.models import next_lna_reference_number
        # If a dash-import already uses 2877, the next free number skips it.
        for i in range(2870, 2877):
            Project.objects.create(
                project_name=f'P{i}', proposal_reference=f'LNA {i} - P{i}',
                status=self.status, region=self.lna)
        Project.objects.create(
            project_name='Imp', proposal_reference='LNA-2877-Imported BSP-R04',
            status=self.status, region=self.lna)
        self.assertEqual(next_lna_reference_number(), 2878)

    def test_dash_format_revision_editable_in_place(self):
        # Imported dash-joined refs (LNA-2817-Name-R04) get only their trailing
        # revision swapped — the base is preserved byte-for-byte, never reformatted.
        p = Project.objects.create(
            project_name='Security System Samha 380KV BSP',
            proposal_reference='LNA-2817-Security System Samha 380KV BSP-R04',
            status=self.status, region=self.lna)
        out = self._save(instance=p, project_name=p.project_name, lna_revision='R05')
        self.assertEqual(
            out.proposal_reference, 'LNA-2817-Security System Samha 380KV BSP-R05')

    def test_dash_format_rename_does_not_mangle_reference(self):
        # Renaming a dash-format project leaves its reference untouched (no
        # canonical rebuild that would inject project_name).
        p = Project.objects.create(
            project_name='Old', proposal_reference='LNA-2817-Old Name-R04',
            status=self.status, region=self.lna)
        p.project_name = 'New'
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.proposal_reference, 'LNA-2817-Old Name-R04')

    def test_unparseable_reference_left_untouched(self):
        p = Project.objects.create(
            project_name='Demo', proposal_reference='DEMO-FIN-CLOSED',
            status=self.status, region=self.lna)
        p.project_name = 'Demo Renamed'
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.proposal_reference, 'DEMO-FIN-CLOSED')

    def test_non_lna_requires_manual_reference(self):
        from projects.forms import ProjectForm
        f = ProjectForm(data=self._data(region=self.uk.pk, proposal_reference=''))
        self.assertFalse(f.is_valid())
        self.assertIn('proposal_reference', f.errors)

    def test_non_lna_keeps_manual_reference(self):
        p = self._save(region=self.uk.pk, proposal_reference='LNUK-P999')
        self.assertEqual(p.proposal_reference, 'LNUK-P999')

    def test_save_syncs_name_on_direct_rename(self):
        # Renaming via a plain .save() (no form) still mirrors the name into the
        # LNA reference, preserving the number.
        p = Project.objects.create(
            project_name='Alpha', proposal_reference='LNA 2870 - Alpha',
            status=self.status, region=self.lna)
        p.project_name = 'Beta Gamma'
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.proposal_reference, 'LNA 2870 - Beta Gamma')

    def test_save_leaves_non_lna_reference_alone(self):
        p = Project.objects.create(
            project_name='UK One', proposal_reference='CUSTOM-1',
            status=self.status, region=self.uk)
        p.project_name = 'UK Two'
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.proposal_reference, 'CUSTOM-1')

    def test_lna_user_region_defaulted_and_field_locked(self):
        from accounts.models import User
        from projects.forms import ProjectForm
        u = User.objects.create_user('lnauser', password='x')
        u.region = self.lna
        u.save()
        f = ProjectForm(user=u)  # unbound create form
        self.assertEqual(f.initial.get('region'), self.lna.id)  # region pre-filled
        self.assertEqual(
            f.fields['proposal_reference'].widget.attrs.get('readonly'), 'readonly')

    def test_non_lna_user_region_defaulted_field_editable(self):
        from accounts.models import User
        from projects.forms import ProjectForm
        u = User.objects.create_user('ukuser', password='x')
        u.region = self.uk
        u.save()
        f = ProjectForm(user=u)
        self.assertEqual(f.initial.get('region'), self.uk.id)
        self.assertNotIn('readonly', f.fields['proposal_reference'].widget.attrs)


class ProposalTeamDocumentVisibilityTests(TestCase):
    """Proposal team sees Client RFQ (and other) documents in their region."""

    def setUp(self):
        from django.core.files.base import ContentFile
        from accounts.models import User, Role
        from projects.models import Region, ProjectStatus, Project, Document
        self.r1 = Region.objects.create(name='RegA', code='DRA')
        self.r2 = Region.objects.create(name='RegB', code='DRB')
        self.st = ProjectStatus.objects.create(name='Open', category='open')
        head_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_HEAD)
        self.head = User.objects.create_user('phd', password='x', role=head_role, region=self.r1)
        self.author = User.objects.create_user('auth', password='x')
        pa = Project.objects.create(project_name='PA', region=self.r1, status=self.st, proposal_reference='DRA-1')
        pb = Project.objects.create(project_name='PB', region=self.r2, status=self.st, proposal_reference='DRB-1')
        self.da = Document.objects.create(name='rfqA', document_type='rfq', project=pa, uploaded_by=self.author)
        self.da.file.save('a.pdf', ContentFile(b'x'), save=True)
        self.db = Document.objects.create(name='rfqB', document_type='rfq', project=pb, uploaded_by=self.author)
        self.db.file.save('b.pdf', ContentFile(b'x'), save=True)

    def test_proposal_head_sees_region_rfqs(self):
        from projects.views import _documents_visible_to
        names = set(_documents_visible_to(self.head).values_list('name', flat=True))
        self.assertIn('rfqA', names)
        self.assertNotIn('rfqB', names)


class ProjectRecoveryViewTests(TestCase):
    """The super-admin browser recovery page: gated to super admins, and shows
    setup instructions when no recovery DB is connected (the normal state)."""

    def setUp(self):
        from accounts.models import Role, User
        self.su = User.objects.create_user('surec', password='x')
        self.su.role = Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0]
        self.su.save()
        self.other = User.objects.create_user('orec', password='x')
        self.other.role = Role.objects.get_or_create(name=Role.SALES_REP)[0]
        self.other.save()

    def test_non_superadmin_blocked(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('projects:recover')).status_code, 302)

    def test_superadmin_sees_setup_instructions(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse('projects:recover'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['available'])   # no recovery DB in tests
        self.assertContains(r, 'Recovery database not connected')
