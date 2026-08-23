import json
from email.message import EmailMessage as StdEmailMessage
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Region, ProjectStatus, Project
from costing.models import CostingSheet, most_advanced_stage, pipeline_stage_badge
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User, Role
from projects.models import Project, Region, ProjectStatus
from projects import graph_mail


def _make_eml_bytes(subject='Vendor Quote', sender='Vendor <vendor@example.com>',
                     to='sales@leap-arabia.com', attachments=None):
    """Build real raw MIME bytes for the email-linking tests, so
    _attach_email_to_project exercises the actual parse_eml_file() code path
    instead of a mocked-out parse result. attachments is a list of
    (filename, content_bytes, maintype, subtype)."""
    msg = StdEmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    msg.set_content('Please find the attached document(s).')
    for filename, content, maintype, subtype in (attachments or []):
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


class ProjectRegionFilterTests(TestCase):
    """BUG-001: users with no region assigned must not see other regions'
    projects, and should get a clear warning instead of a silent empty list."""

    def setUp(self):
        self.client = Client()
        self.region_lna = Region.objects.create(code='LNA', name='Leap Arabia', is_active=True)
        self.status = ProjectStatus.objects.create(name='Open', category='open', is_active=True)

        from accounts.models import Role
        from accounts.permissions import seed_default_permissions
        # Region filtering now applies to region-scoped roles (manager/admin);
        # sales reps are owner-scoped and tested separately.
        region_role, _ = Role.objects.get_or_create(name=Role.MANAGER)
        seed_default_permissions()

        self.user_no_region = User.objects.create_user(
            username='noregion', password='testpass123', region=None, role=region_role,
        )
        self.user_lna = User.objects.create_user(
            username='lnauser', password='testpass123', region=self.region_lna, role=region_role,
        )

        self.project = Project.objects.create(
            project_name='Test Project', proposal_reference='LNA 9999',
            region=self.region_lna, status=self.status,
        )

    def test_user_with_no_region_sees_empty_list_with_warning(self):
        self.client.login(username='noregion', password='testpass123')
        response = self.client.get(reverse('projects:list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['projects']), 0)
        messages = list(response.context['messages'])
        self.assertTrue(
            any('no region assigned' in str(m).lower() for m in messages),
            'Expected a warning message about missing region assignment.'
        )

    def test_user_with_region_sees_matching_projects(self):
        self.client.login(username='lnauser', password='testpass123')
        response = self.client.get(reverse('projects:list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.project, response.context['projects'])


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
    """Sales reps see only the projects they OWN (view + edit). The proposal
    team still sees the whole region's pipeline. Creating a project notifies
    the region team."""

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

    def test_sales_rep_sees_only_owned_not_region(self):
        own = Project.objects.create(
            project_name='Sales Own', proposal_reference='LNA-SOWN',
            status=self.status, region=self.lna, owner=self.sales)
        self.client.force_login(self.sales)
        ids = {p.pk for p in self.client.get(reverse('projects:list')).context['projects']}
        self.assertIn(own.pk, ids)                  # their own project
        self.assertNotIn(self.lna_proj.pk, ids)     # region project (owned by admin) hidden
        self.assertNotIn(self.uk_proj.pk, ids)      # other region hidden

    def test_proposal_sees_region_pipeline(self):
        self.client.force_login(self.proposal)
        ids = {p.pk for p in self.client.get(reverse('projects:list')).context['projects']}
        self.assertIn(self.lna_proj.pk, ids)

    def test_sales_cannot_view_unowned_project_detail(self):
        self.client.force_login(self.sales)
        r = self.client.get(reverse('projects:detail', kwargs={'pk': self.lna_proj.pk}))
        self.assertEqual(r.status_code, 404)  # owned by admin, not this rep

    def test_sales_cannot_edit_unowned_project(self):
        self.client.force_login(self.sales)
        r = self.client.get(reverse('projects:edit', kwargs={'pk': self.lna_proj.pk}))
        self.assertEqual(r.status_code, 404)  # not in the owner-scoped edit queryset

    def test_detail_shows_edit_button_for_owner(self):
        edit_url = reverse('projects:edit', kwargs={'pk': self.lna_proj.pk})
        # Admin owns / can edit the region project → Edit button present.
        self.client.force_login(self.admin)
        r = self.client.get(reverse('projects:detail', kwargs={'pk': self.lna_proj.pk}))
        self.assertTrue(r.context['can_edit'])
        self.assertContains(r, edit_url)
        # Sales rep doesn't own it → can't even open it.
        self.client.force_login(self.sales)
        r = self.client.get(reverse('projects:detail', kwargs={'pk': self.lna_proj.pk}))
        self.assertEqual(r.status_code, 404)

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

    def _create_and_get_recipients(self, ref):
        from notifications.models import Notification
        resp = self.client.post(reverse('projects:create'), self._create_post(ref))
        self.assertEqual(resp.status_code, 302)  # created -> redirect
        # LNA reference is auto-generated, so look the project up by name.
        proj = Project.objects.get(project_name='New Pipeline')
        return proj, set(Notification.objects.filter(target_object_id=proj.pk)
                         .values_list('recipient__username', flat=True))

    def test_create_notifies_proposal_but_not_unowning_reps(self):
        """Reps who don't own the project would land on a 404, so they are
        deliberately left out of the region-wide notice."""
        self.client.force_login(self.admin)
        proj, recips = self._create_and_get_recipients('LNA-NEW')
        self.assertEqual(proj.owner_id, self.admin.pk)
        self.assertIn('prop', recips)       # region proposal notified
        self.assertNotIn('sales', recips)   # non-owning rep would get a 404
        self.assertNotIn('adm', recips)     # actor not self-notified

    def test_create_notifies_the_owning_rep(self):
        """The one rep who CAN open the project still gets told about it."""
        self.client.force_login(self.sales)
        proj, recips = self._create_and_get_recipients('LNA-NEW2')
        self.assertEqual(proj.owner_id, self.sales.pk)
        # Owner is the actor here, so they aren't self-notified; the guard is
        # that creating a project no longer notifies the *other* reps.
        self.assertNotIn('sales', recips)
        self.assertIn('prop', recips)


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


class ProjectSoftDeleteTests(TestCase):
    """Deleting a project must be reversible and must not destroy anything.

    A hard delete CASCADEs into finance/history rows and SET_NULLs the project
    link on costing sheets and purchase orders — the failure mode that
    previously cost this project its commercial pipeline. Soft delete has to
    leave every one of those relationships intact.
    """

    def setUp(self):
        self.admin = User.objects.create_user('sdadmin', password='x')
        self.admin.role = Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0]
        self.admin.save()
        self.region = Region.objects.create(name='LNA', code='LNA')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='Aramco Tank Farm', proposal_reference='SD-REF-1',
            region=self.region, status=self.status, created_by=self.admin)
        self.client.force_login(self.admin)

    def test_default_manager_hides_deleted_but_all_objects_sees_it(self):
        self.project.soft_delete(user=self.admin)
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertTrue(Project.all_objects.filter(pk=self.project.pk).exists())

    def test_delete_view_soft_deletes_and_keeps_related_records(self):
        sheet = CostingSheet.objects.create(
            title='BOM 1', project=self.project, created_by=self.admin)

        r = self.client.post(
            reverse('projects:delete', kwargs={'pk': self.project.pk}))
        self.assertEqual(r.status_code, 302)

        # Row retained, just hidden.
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_deleted)
        self.assertIsNotNone(self.project.deleted_at)
        self.assertEqual(self.project.deleted_by, self.admin)

        # The critical assertion: the costing sheet still exists AND still
        # points at the project (a hard delete would have nulled project_id).
        sheet.refresh_from_db()
        self.assertEqual(sheet.project_id, self.project.pk)

    def test_restore_brings_it_back(self):
        self.project.soft_delete(user=self.admin)
        r = self.client.post(
            reverse('projects:restore', kwargs={'pk': self.project.pk}))
        self.assertEqual(r.status_code, 302)
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_deleted)
        self.assertIsNone(self.project.deleted_at)
        self.assertIsNone(self.project.deleted_by)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_deleted_project_is_gone_from_the_pipeline_list(self):
        r = self.client.get(reverse('projects:list'))
        self.assertContains(r, 'Aramco Tank Farm')
        self.project.soft_delete(user=self.admin)
        r = self.client.get(reverse('projects:list'))
        self.assertNotContains(r, 'Aramco Tank Farm')

    def test_recycle_bin_lists_deleted_and_requires_permission(self):
        self.project.soft_delete(user=self.admin)
        r = self.client.get(reverse('projects:recycle_bin'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Aramco Tank Farm')

        rep = User.objects.create_user('sdrep', password='x')
        rep.role = Role.objects.get_or_create(name=Role.SALES_REP)[0]
        rep.save()
        self.client.force_login(rep)
        # UserPassesTestMixin denies an authenticated-but-unauthorised user
        # with 403 rather than bouncing them to the login page.
        self.assertEqual(self.client.get(reverse('projects:recycle_bin')).status_code, 403)

    def test_restore_requires_permission(self):
        self.project.soft_delete(user=self.admin)
        rep = User.objects.create_user('sdrep2', password='x')
        rep.role = Role.objects.get_or_create(name=Role.SALES_REP)[0]
        rep.save()
        self.client.force_login(rep)
        self.client.post(reverse('projects:restore', kwargs={'pk': self.project.pk}))
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_deleted)   # still deleted


class MilestoneDeadlineTests(TestCase):
    def setUp(self):
        self.region = Region.objects.create(code='LNA', name='Leap Arabia', is_active=True)
        self.status = ProjectStatus.objects.create(name='Open', category='open', is_active=True)

    def _project(self, name, **deadlines):
        return Project.objects.create(
            project_name=name, proposal_reference=f'LNA-TEST-{name}',
            region=self.region, status=self.status, **deadlines)

    def test_no_deadlines_gives_no_status(self):
        p = self._project('NoDeadlines')
        self.assertEqual(p.milestone_overall_status, '')

    def test_overdue_milestone_gives_red(self):
        from datetime import date, timedelta
        p = self._project('Overdue', bom_started_deadline=date.today() - timedelta(days=3))
        self.assertEqual(p.milestone_overall_status, 'danger')

    def test_deadline_not_yet_passed_gives_orange(self):
        from datetime import date, timedelta
        p = self._project('InProgress', bom_started_deadline=date.today() + timedelta(days=3))
        self.assertEqual(p.milestone_overall_status, 'warning')

    def test_finalised_sheet_gives_green(self):
        from django.utils import timezone
        from datetime import timedelta
        p = self._project('Finalised')
        sheet = CostingSheet.objects.create(
            project=p, title='t', margin=20, discount_rate=0, shipping_rate=5,
            customs_rate=5, finances_rate=2, installation_rate=3,
            output_currency='SAR', default_supplier_currency='SAR',
            scope_of_work_total=0, status='draft', workflow_stage='finalized',
            finalized_at=timezone.now() - timedelta(days=1))
        p.cycle_sheet = sheet
        self.assertEqual(p.milestone_overall_status, 'success')

    def test_milestone_display_rows_shows_variance(self):
        from django.utils import timezone
        from datetime import date, timedelta
        today = timezone.now()
        p = self._project('Variance', bom_started_deadline=date.today() - timedelta(days=5))
        sheet = CostingSheet.objects.create(
            project=p, title='t', margin=20, discount_rate=0, shipping_rate=5,
            customs_rate=5, finances_rate=2, installation_rate=3,
            output_currency='SAR', default_supplier_currency='SAR',
            scope_of_work_total=0, status='draft', workflow_stage='bom_in_progress',
            bom_started_at=today - timedelta(days=7))
        p.cycle_sheet = sheet
        rows = p.milestone_display_rows
        bom_row = next(r for r in rows if r['label'] == 'BOM started')
        self.assertEqual(bom_row['variance_display'], '+2d')

    def test_milestone_display_rows_no_sheet_shows_dashes(self):
        p = self._project('NoSheet')
        rows = p.milestone_display_rows
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertIsNone(r['variance_display'])


class OpportunityLostTests(TestCase):
    """A project marked Lost has to record why, as a choice not free text."""

    def setUp(self):
        from accounts.permissions import seed_default_permissions
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.open_status = ProjectStatus.objects.create(name='Open', category='active')
        self.lost_status = ProjectStatus.objects.create(name='Lost', category='lost')
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('sa_lost', password='x')
        self.user.role = role
        self.user.region = self.region
        self.user.save()
        seed_default_permissions()

    def _form(self, **overrides):
        from projects.forms import ProjectForm
        data = {
            'project_name': 'Lost deal', 'proposal_reference': 'REF-L1',
            'status': self.lost_status.pk, 'region': self.region.pk,
            'estimated_value': '0', 'estimated_value_usd': '0',
            'estimated_value_per_annum': '0', 'estimated_gp': '0',
            'actual_sales': '0', 'success_quotient': '0', 'minimum_achievement': '0',
        }
        data.update(overrides)
        return ProjectForm(data=data, user=self.user)

    def test_lost_without_a_reason_is_rejected(self):
        form = self._form()
        self.assertFalse(form.is_valid())
        self.assertIn('lost_reason', form.errors)

    def test_lost_with_a_reason_is_accepted(self):
        form = self._form(lost_reason=Project.LOST_COMPETITOR)
        self.assertTrue(form.is_valid(), form.errors)

    def test_other_requires_a_comment(self):
        """'Other' with no comment records nothing anyone can interpret later."""
        form = self._form(lost_reason=Project.LOST_OTHER)
        self.assertFalse(form.is_valid())
        self.assertIn('lost_comment', form.errors)

    def test_other_with_a_comment_is_accepted(self):
        form = self._form(lost_reason=Project.LOST_OTHER,
                          lost_comment='Client merged with a competitor.')
        self.assertTrue(form.is_valid(), form.errors)

    def test_whitespace_only_comment_does_not_satisfy_other(self):
        form = self._form(lost_reason=Project.LOST_OTHER, lost_comment='   ')
        self.assertFalse(form.is_valid())
        self.assertIn('lost_comment', form.errors)

    def test_non_lost_status_does_not_demand_a_reason(self):
        form = self._form(status=self.open_status.pk, proposal_reference='REF-L2')
        self.assertTrue(form.is_valid(), form.errors)

    def test_did_not_bid_is_a_distinct_choice(self):
        """Choosing not to pursue is not the same as being beaten."""
        codes = dict(Project.LOST_REASON_CHOICES)
        self.assertIn(Project.LOST_NO_BID, codes)
        self.assertNotEqual(codes[Project.LOST_NO_BID], codes[Project.LOST_COMPETITOR])

    def test_lost_summary_reads_as_one_line(self):
        proj = Project.objects.create(
            project_name='P', proposal_reference='REF-S', status=self.lost_status,
            region=self.region, lost_reason=Project.LOST_PRICE, lost_comment='15% over')
        self.assertIn('Price', proj.lost_summary)
        self.assertIn('15% over', proj.lost_summary)

    def test_lost_summary_is_blank_when_not_lost(self):
        proj = Project.objects.create(
            project_name='P2', proposal_reference='REF-S2', status=self.open_status,
            region=self.region, lost_reason=Project.LOST_PRICE)
        self.assertEqual(proj.lost_summary, '')

    def test_reason_survives_a_project_being_revived(self):
        """Revivals happen; wiping the reason would lose why it was lost."""
        proj = Project.objects.create(
            project_name='P3', proposal_reference='REF-S3', status=self.lost_status,
            region=self.region, lost_reason=Project.LOST_BUDGET)
        proj.status = self.open_status
        proj.save()
        proj.refresh_from_db()
        self.assertEqual(proj.lost_reason, Project.LOST_BUDGET)

    def test_form_exposes_which_statuses_mean_lost(self):
        """The template reveals the fields from this, not from a label match."""
        from projects.forms import ProjectForm
        form = ProjectForm(user=self.user)
        self.assertIn(self.lost_status.pk, form.lost_status_ids)
        self.assertNotIn(self.open_status.pk, form.lost_status_ids)


class TemplateCommentSyntaxTests(TestCase):
    """No template may use a multi-line hash comment.

    Django's tag regex has no DOTALL flag, so a hash comment spanning more than
    one line is never matched and prints verbatim on the rendered page. In the
    source it looks exactly like a correct comment, which is why this is worth
    a test rather than vigilance — it shipped to production once already.
    """

    NEWLINE = '\n'

    def _template_roots(self):
        import os
        from django.conf import settings
        base = settings.BASE_DIR
        roots = [os.path.join(base, 'templates')]
        for entry in os.listdir(base):
            candidate = os.path.join(base, entry, 'templates')
            if os.path.isdir(candidate):
                roots.append(candidate)
        return [r for r in roots if os.path.isdir(r)]

    def test_no_multiline_hash_comments_in_any_template(self):
        import os
        import re
        offenders = []
        for root in self._template_roots():
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    if not filename.endswith('.html'):
                        continue
                    path = os.path.join(dirpath, filename)
                    with open(path, encoding='utf-8', errors='replace') as handle:
                        text = handle.read()
                    for match in re.finditer(re.escape('{#'), text):
                        close = text.find('#}', match.start())
                        if close == -1:
                            continue
                        if self.NEWLINE in text[match.start():close]:
                            line = text[:match.start()].count(self.NEWLINE) + 1
                            offenders.append(f'{path}:{line}')
        self.assertEqual(
            offenders, [],
            'Multi-line hash comments render as visible text. Use '
            '{% comment %} ... {% endcomment %} instead. Offenders: ' + ', '.join(offenders))

    def test_the_mechanism_this_guards_against(self):
        """Proves the failure mode, so the check above is not cargo-culted."""
        from django.template import Context, Template
        single = Template('A{# one line #}B').render(Context({}))
        self.assertEqual(single, 'AB')
        spanning = Template('A{# first' + self.NEWLINE + 'second #}B').render(Context({}))
        self.assertIn('{#', spanning)          # the comment leaks into the output


class AttachEmailToProjectHelperTests(TestCase):
    """_attach_email_to_project() is the single place that turns a live-inbox
    email into real PipelineEmail + Document rows (used by both the
    existing-project attach flow and project creation). These tests exercise
    it directly, with a real parsed .eml, so the per-attachment document-type
    matching and the "delink keeps documents" invariant are proven against
    the actual parsing code, not a mocked-out shortcut."""

    def setUp(self):
        from projects.models import Document, PipelineEmail
        self.Document = Document
        self.PipelineEmail = PipelineEmail
        self.region = Region.objects.create(name='Test Region', code='TST')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.user = User.objects.create_user('attacher', password='x')
        self.project = Project.objects.create(
            project_name='Helper Target', proposal_reference='TST-HELP-1',
            region=self.region, status=self.status)

    def _attach(self, message_id='msg-1', attachments=None, attachment_types=None,
                subject='Vendor Quote'):
        from projects.views import _attach_email_to_project
        raw = _make_eml_bytes(subject=subject, attachments=attachments or [])
        with patch('projects.graph_mail.fetch_raw_message_bytes', return_value=raw):
            return _attach_email_to_project(
                self.project, self.user, message_id, attachment_types=attachment_types)

    def test_creates_pipeline_email_and_document_with_default_type(self):
        pipeline_email, created_count = self._attach(
            subject='Q-100', attachments=[('quote.pdf', b'%PDF-fake', 'application', 'pdf')])
        self.assertEqual(created_count, 1)
        self.assertEqual(pipeline_email.subject, 'Q-100')
        self.assertTrue(pipeline_email.is_active)
        self.assertEqual(pipeline_email.linked_by, self.user)
        doc = self.Document.objects.get(source_pipeline_email=pipeline_email)
        self.assertEqual(doc.name, 'quote.pdf')
        self.assertEqual(doc.document_type, 'vendor_quotation')  # documented default
        self.assertEqual(doc.project, self.project)
        self.assertEqual(doc.uploaded_by, self.user)

    def test_per_attachment_type_applied_by_filename_match(self):
        _, created_count = self._attach(
            attachments=[
                ('quote.pdf', b'%PDF-1', 'application', 'pdf'),
                ('po.pdf', b'%PDF-2', 'application', 'pdf'),
            ],
            attachment_types={'quote.pdf': 'vendor_quotation', 'po.pdf': 'po_document'},
        )
        self.assertEqual(created_count, 2)
        self.assertEqual(
            self.Document.objects.get(name='quote.pdf').document_type, 'vendor_quotation')
        self.assertEqual(
            self.Document.objects.get(name='po.pdf').document_type, 'po_document')

    def test_invalid_document_type_fails_closed_to_default(self):
        """A document_type that isn't one of Document.DOCUMENT_TYPE_CHOICES
        (e.g. a tampered form submission) must never be persisted verbatim —
        it must fall back to the safe default, not crash and not store
        arbitrary text into a choices field."""
        self._attach(
            attachments=[('quote.pdf', b'%PDF', 'application', 'pdf')],
            attachment_types={'quote.pdf': '<script>alert(1)</script>'},
        )
        doc = self.Document.objects.get(name='quote.pdf')
        self.assertEqual(doc.document_type, 'vendor_quotation')

    def test_type_missing_from_map_falls_back_to_default(self):
        self._attach(
            attachments=[('quote.pdf', b'%PDF', 'application', 'pdf')],
            attachment_types={},
        )
        self.assertEqual(
            self.Document.objects.get(name='quote.pdf').document_type, 'vendor_quotation')

    def test_email_with_no_attachments_creates_zero_documents(self):
        pipeline_email, created_count = self._attach(attachments=[])
        self.assertEqual(created_count, 0)
        self.assertEqual(self.Document.objects.filter(project=self.project).count(), 0)
        self.assertTrue(self.PipelineEmail.objects.filter(pk=pipeline_email.pk).exists())

    def test_relinking_delinks_previous_email_but_never_touches_its_documents(self):
        """The critical Commercial Pipeline invariant: attaching a new email
        replaces which one is 'active', but every document already created
        from the previous email must remain exactly as it was."""
        first_email, _ = self._attach(
            message_id='msg-1', subject='First',
            attachments=[('first.pdf', b'%PDF-1', 'application', 'pdf')])
        first_doc = self.Document.objects.get(name='first.pdf')

        second_email, _ = self._attach(
            message_id='msg-2', subject='Second',
            attachments=[('second.pdf', b'%PDF-2', 'application', 'pdf')])

        first_email.refresh_from_db()
        second_email.refresh_from_db()
        self.assertFalse(first_email.is_active)
        self.assertIsNotNone(first_email.delinked_at)
        self.assertTrue(second_email.is_active)

        # First document: untouched — still exists, still on the project,
        # still attributed to the (now inactive) first email.
        first_doc.refresh_from_db()
        self.assertEqual(first_doc.project, self.project)
        self.assertEqual(first_doc.source_pipeline_email, first_email)
        self.assertEqual(self.Document.objects.filter(project=self.project).count(), 2)

    def test_graph_failure_creates_no_partial_rows(self):
        """If the raw message can't be fetched, nothing must be written —
        no orphan PipelineEmail with no attachments, no partial state."""
        from projects.views import _attach_email_to_project
        with patch('projects.graph_mail.fetch_raw_message_bytes',
                   side_effect=graph_mail.GraphMailError('mailbox unreachable')):
            with self.assertRaises(graph_mail.GraphMailError):
                _attach_email_to_project(self.project, self.user, 'msg-x')
        self.assertEqual(self.PipelineEmail.objects.filter(project=self.project).count(), 0)
        self.assertEqual(self.Document.objects.filter(project=self.project).count(), 0)

    def test_unparseable_email_creates_no_partial_rows(self):
        from projects.email_parsing import EmailParseError
        from projects.views import _attach_email_to_project
        with patch('projects.graph_mail.fetch_raw_message_bytes', return_value=b''):
            with self.assertRaises(EmailParseError):
                _attach_email_to_project(self.project, self.user, 'msg-empty')
        self.assertEqual(self.PipelineEmail.objects.filter(project=self.project).count(), 0)


class LinkPipelineEmailExistingProjectViewTests(TestCase):
    """The live-inbox 'Add Emails' flow for an already-saved pipeline entry."""

    def setUp(self):
        from projects.models import Document, PipelineEmail
        self.Document = Document
        self.PipelineEmail = PipelineEmail
        self.region = Region.objects.create(name='Test Region', code='TST')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        sales_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        self.user = User.objects.create_user('linker', password='pass12345', role=sales_role)
        self.project = Project.objects.create(
            project_name='Linkable', proposal_reference='TST-LINK-1',
            region=self.region, status=self.status, owner=self.user)
        self.url = reverse('projects:link_pipeline_email', kwargs={'pk': self.project.pk})

    def _inbox_message(self, message_id='m1', name='quote.pdf'):
        return {
            'id': message_id, 'subject': 'A vendor quote', 'sender_name': 'Vendor',
            'sender_email': 'vendor@example.com', 'cc': '', 'received_at': '2026-08-01T09:00:00Z',
            'body_preview': 'preview text',
            'attachments': [
                {'id': 'att-1', 'name': name, 'content_type': 'application/pdf', 'size': 10},
            ],
        }

    # --- auth ---

    def test_anonymous_get_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_anonymous_post_redirected_to_login_and_creates_nothing(self):
        response = self.client.post(self.url, {'message_id': 'm1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)
        self.assertEqual(self.PipelineEmail.objects.count(), 0)

    def test_unauthorized_role_denied_get_and_post(self):
        # Add Emails is restricted to Sales/Admin/Super Admin — a role
        # outside that (e.g. Finance) can view the project but not this.
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        other = User.objects.create_user('finance1', password='x', role=finance_role, region=self.region)
        self.client.force_login(other)

        with patch('projects.graph_mail.list_inbox_messages') as mock_list:
            response = self.client.get(self.url)
        mock_list.assert_not_called()
        self.assertRedirects(response, reverse('projects:detail', kwargs={'pk': self.project.pk}))

        response = self.client.post(self.url, {'message_id': 'm1'})
        self.assertRedirects(response, reverse('projects:detail', kwargs={'pk': self.project.pk}))
        self.assertEqual(self.PipelineEmail.objects.count(), 0)

    # --- GET / inbox listing ---

    def test_get_renders_inbox_with_type_dropdown_per_attachment(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.list_inbox_messages',
                   return_value=[self._inbox_message()]):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'quote.pdf')
        self.assertContains(response, 'name="doc_type"')
        self.assertEqual(response.context['document_type_choices'], self.Document.DOCUMENT_TYPE_CHOICES)

    def test_get_degrades_gracefully_when_graph_unreachable(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.list_inbox_messages',
                   side_effect=graph_mail.GraphMailError('no credentials configured')):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)  # never a 500
        self.assertIn('no credentials configured', response.context['inbox_error'])

    # --- POST / attach ---

    def test_post_without_message_id_shows_error_and_creates_nothing(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {})
        self.assertRedirects(response, self.url)
        self.assertEqual(self.PipelineEmail.objects.count(), 0)

    def test_post_happy_path_creates_email_and_documents_with_chosen_types(self):
        self.client.force_login(self.user)
        raw = _make_eml_bytes(subject='Q-1', attachments=[
            ('a.pdf', b'%PDF-A', 'application', 'pdf'),
            ('b.xlsx', b'PK-fake', 'application', 'vnd.ms-excel'),
        ])
        with patch('projects.graph_mail.fetch_raw_message_bytes', return_value=raw):
            response = self.client.post(self.url, {
                'message_id': 'm1',
                'doc_filename': ['a.pdf', 'b.xlsx'],
                'doc_type': ['po_document', 'contract'],
            })
        self.assertRedirects(response, reverse('projects:detail', kwargs={'pk': self.project.pk}))
        self.assertEqual(self.Document.objects.get(name='a.pdf').document_type, 'po_document')
        self.assertEqual(self.Document.objects.get(name='b.xlsx').document_type, 'contract')

    def test_post_invalid_doc_type_fails_closed(self):
        self.client.force_login(self.user)
        raw = _make_eml_bytes(attachments=[('a.pdf', b'%PDF-A', 'application', 'pdf')])
        with patch('projects.graph_mail.fetch_raw_message_bytes', return_value=raw):
            self.client.post(self.url, {
                'message_id': 'm1', 'doc_filename': ['a.pdf'], 'doc_type': ['not-a-real-type'],
            })
        self.assertEqual(self.Document.objects.get(name='a.pdf').document_type, 'vendor_quotation')

    def test_post_graph_failure_shows_error_and_creates_nothing(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.fetch_raw_message_bytes',
                   side_effect=graph_mail.GraphMailError('timeout')):
            response = self.client.post(self.url, {'message_id': 'm1'})
        self.assertRedirects(response, self.url)
        self.assertEqual(self.PipelineEmail.objects.count(), 0)

    def test_relink_via_view_keeps_previous_email_documents(self):
        self.client.force_login(self.user)
        raw1 = _make_eml_bytes(subject='First', attachments=[
            ('first.pdf', b'%PDF-1', 'application', 'pdf')])
        with patch('projects.graph_mail.fetch_raw_message_bytes', return_value=raw1):
            self.client.post(self.url, {'message_id': 'm1'})

        raw2 = _make_eml_bytes(subject='Second', attachments=[
            ('second.pdf', b'%PDF-2', 'application', 'pdf')])
        with patch('projects.graph_mail.fetch_raw_message_bytes', return_value=raw2):
            self.client.post(self.url, {'message_id': 'm2'})

        self.assertEqual(self.Document.objects.filter(project=self.project).count(), 2)
        active = self.project.linked_emails.filter(is_active=True).first()
        self.assertEqual(active.subject, 'Second')
        self.assertFalse(self.PipelineEmail.objects.get(subject='First').is_active)

    def test_csrf_enforced(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(self.url, {'message_id': 'm1'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.PipelineEmail.objects.count(), 0)


class LinkPipelineEmailNewViewTests(TestCase):
    """The 'Add Emails' flow while creating a brand-new pipeline entry.

    This is the flow that was explicitly redesigned mid-project after an
    earlier version wrongly auto-created a real Project row (and fired its
    costing-sheet + team-notification side effects) the moment an email was
    picked. These tests guard that regression directly: nothing here may
    ever touch Project/PipelineEmail/Document."""

    def setUp(self):
        from projects.models import Document, PipelineEmail
        self.Document = Document
        self.PipelineEmail = PipelineEmail
        sales_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        self.user = User.objects.create_user('creator', password='pass12345', role=sales_role)
        self.url = reverse('projects:link_pipeline_email_new')

    def _inbox_message(self):
        return {
            'id': 'm1', 'subject': 'A vendor quote', 'sender_name': 'Vendor',
            'sender_email': 'vendor@example.com', 'cc': '', 'received_at': '2026-08-01T09:00:00Z',
            'body_preview': 'preview text',
            'attachments': [
                {'id': 'att-1', 'name': 'quote.pdf', 'content_type': 'application/pdf', 'size': 10},
            ],
        }

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_unauthorized_role_denied_get_and_post(self):
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        other = User.objects.create_user('finance2', password='x', role=finance_role)
        self.client.force_login(other)

        with patch('projects.graph_mail.list_inbox_messages') as mock_list:
            response = self.client.get(self.url)
        mock_list.assert_not_called()
        self.assertRedirects(response, reverse('projects:create'))

        response = self.client.post(self.url, {'message_id': 'm1'})
        self.assertRedirects(response, reverse('projects:create'))
        self.assertEqual(Project.objects.count(), 0)

    def test_get_lists_inbox_and_creates_nothing(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.list_inbox_messages',
                   return_value=[self._inbox_message()]):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'quote.pdf')
        # No per-document type dropdown here — there's no project yet to
        # attach to; typing happens on the create form after the redirect.
        self.assertNotContains(response, 'name="doc_type"')
        self.assertEqual(Project.objects.count(), 0)

    def test_post_without_message_id_redirects_with_error_and_creates_nothing(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {})
        self.assertRedirects(response, self.url)
        self.assertEqual(Project.objects.count(), 0)

    def test_post_happy_path_never_touches_the_database(self):
        """The core regression guard: picking an email while creating a new
        entry must be a pure redirect, never a database write."""
        self.client.force_login(self.user)
        response = self.client.post(self.url, {'message_id': 'special-id-123'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(self.PipelineEmail.objects.count(), 0)
        self.assertEqual(self.Document.objects.count(), 0)

    def test_post_redirect_round_trips_special_characters_in_message_id(self):
        """Graph message IDs can contain '+' and '/'. An un-encoded '+' in a
        query string decodes back as a space, corrupting the id — this
        proves urlencode() is actually protecting the round trip."""
        from urllib.parse import urlsplit, parse_qs
        self.client.force_login(self.user)
        tricky_id = 'AAMkAGI2+special/chars=='
        response = self.client.post(self.url, {'message_id': tricky_id})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.url).query)
        self.assertEqual(query['picked_email'][0], tricky_id)

    def test_csrf_enforced_creates_nothing(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(self.url, {'message_id': 'm1'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Project.objects.count(), 0)


class ViewPipelineInboxAttachmentTests(TestCase):
    """Opening/previewing one attachment straight from the live inbox,
    before it's attached to anything. Read-only, and must work both for an
    existing project (pk given) and while creating a new one (pk=None)."""

    def setUp(self):
        self.region = Region.objects.create(name='Test Region', code='TST')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        sales_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        self.user = User.objects.create_user('viewer', password='pass12345', role=sales_role)
        self.project = Project.objects.create(
            project_name='Viewable', proposal_reference='TST-VIEW-1',
            region=self.region, status=self.status)
        self.url_with_pk = reverse('projects:view_pipeline_inbox_attachment', kwargs={
            'pk': self.project.pk, 'message_id': 'm1', 'attachment_id': 'a1'})
        self.url_without_pk = reverse('projects:view_pipeline_inbox_attachment_new', kwargs={
            'message_id': 'm1', 'attachment_id': 'a1'})

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url_with_pk)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_unauthorized_role_denied_with_and_without_pk(self):
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        other = User.objects.create_user('finance3', password='x', role=finance_role, region=self.region)
        self.client.force_login(other)

        with patch('projects.graph_mail.fetch_attachment_bytes') as mock_fetch:
            response = self.client.get(self.url_with_pk)
        mock_fetch.assert_not_called()
        self.assertRedirects(response, reverse('projects:detail', kwargs={'pk': self.project.pk}))

        with patch('projects.graph_mail.fetch_attachment_bytes') as mock_fetch:
            response = self.client.get(self.url_without_pk)
        mock_fetch.assert_not_called()
        self.assertRedirects(response, reverse('projects:create'))

    def test_404_when_project_does_not_exist(self):
        self.client.force_login(self.user)
        url = reverse('projects:view_pipeline_inbox_attachment', kwargs={
            'pk': 999999, 'message_id': 'm1', 'attachment_id': 'a1'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_with_pk_returns_file_bytes(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.fetch_attachment_bytes',
                   return_value=('quote.pdf', 'application/pdf', b'%PDF-fake-bytes')):
            response = self.client.get(self.url_with_pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'%PDF-fake-bytes')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('quote.pdf', response['Content-Disposition'])

    def test_without_pk_works_with_no_project(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.fetch_attachment_bytes',
                   return_value=('quote.pdf', 'application/pdf', b'%PDF-fake-bytes')):
            response = self.client.get(self.url_without_pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'%PDF-fake-bytes')

    def test_graph_failure_redirects_gracefully_with_pk(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.fetch_attachment_bytes',
                   side_effect=graph_mail.GraphMailError('gone')):
            response = self.client.get(self.url_with_pk)
        self.assertRedirects(
            response, reverse('projects:link_pipeline_email', kwargs={'pk': self.project.pk}))

    def test_graph_failure_redirects_gracefully_without_pk(self):
        self.client.force_login(self.user)
        with patch('projects.graph_mail.fetch_attachment_bytes',
                   side_effect=graph_mail.GraphMailError('gone')):
            response = self.client.get(self.url_without_pk)
        self.assertRedirects(response, reverse('projects:link_pipeline_email_new'))

    def test_malicious_filename_cannot_inject_a_header_or_crash_the_request(self):
        """Regression test: the attachment filename comes straight from
        Graph (untrusted, external data). A filename containing CRLF used
        to reach HttpResponse unsanitised and raise an unhandled
        BadHeaderError (a 500) — worse, it's the shape of a header-injection
        attempt. This must now fail closed: 200, no crash, no injected
        header line."""
        self.client.force_login(self.user)
        evil_filename = 'evil.pdf"\r\nX-Injected-Header: yes\r\n\r\n'
        with patch('projects.graph_mail.fetch_attachment_bytes',
                   return_value=(evil_filename, 'application/pdf', b'%PDF')):
            response = self.client.get(self.url_with_pk)
        self.assertEqual(response.status_code, 200)
        header_value = response['Content-Disposition']
        self.assertNotIn('\r', header_value)
        self.assertNotIn('\n', header_value)
        self.assertNotIn('X-Injected-Header', response.headers)


class DelinkPipelineEmailViewTests(TestCase):
    """Delinking an email must never remove the documents it created —
    documented invariant of PipelineEmail (see projects/models.py)."""

    def setUp(self):
        from projects.models import Document, PipelineEmail
        from django.core.files.base import ContentFile
        self.Document = Document
        self.region = Region.objects.create(name='Test Region', code='TST')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        sales_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        self.user = User.objects.create_user('delinker', password='pass12345', role=sales_role)
        self.project = Project.objects.create(
            project_name='Delinkable', proposal_reference='TST-DEL-1',
            region=self.region, status=self.status, owner=self.user)
        self.email = PipelineEmail.objects.create(
            project=self.project, subject='Active email', is_active=True, linked_by=self.user)
        self.doc = Document.objects.create(
            project=self.project, document_type='vendor_quotation', name='kept.pdf',
            uploaded_by=self.user, source_pipeline_email=self.email)
        self.doc.file.save('kept.pdf', ContentFile(b'x'), save=True)
        self.url = reverse('projects:delink_pipeline_email', kwargs={'pk': self.project.pk})

    def test_anonymous_redirected_to_login(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_unauthorized_role_denied_and_email_stays_linked(self):
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        other = User.objects.create_user('finance4', password='x', role=finance_role, region=self.region)
        self.client.force_login(other)
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('projects:detail', kwargs={'pk': self.project.pk}))
        self.email.refresh_from_db()
        self.assertTrue(self.email.is_active)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_post_with_no_active_email_shows_info_and_does_not_crash(self):
        self.email.is_active = False
        self.email.save(update_fields=['is_active'])
        self.client.force_login(self.user)
        response = self.client.post(self.url, follow=True)
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in response.context['messages']]
        self.assertTrue(any('no linked email' in m.lower() for m in msgs))

    def test_post_delinks_active_email_but_keeps_its_documents(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('projects:detail', kwargs={'pk': self.project.pk}))

        self.email.refresh_from_db()
        self.assertFalse(self.email.is_active)
        self.assertEqual(self.email.delinked_by, self.user)
        self.assertIsNotNone(self.email.delinked_at)

        # The critical assertion: the document is completely untouched.
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.project, self.project)
        self.assertEqual(self.doc.source_pipeline_email, self.email)
        self.assertTrue(self.Document.objects.filter(pk=self.doc.pk).exists())

    def test_csrf_enforced(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(self.url)
        self.assertEqual(response.status_code, 403)
        self.email.refresh_from_db()
        self.assertTrue(self.email.is_active)  # untouched


class AddProjectDocumentsBulkViewTests(TestCase):
    """Uploading several documents at once, each with its own document type
    chosen in the preview list before upload."""

    def setUp(self):
        from projects.models import Document
        self.Document = Document
        self.region = Region.objects.create(name='Test Region', code='TST')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.user = User.objects.create_user('bulkuploader', password='pass12345')
        self.project = Project.objects.create(
            project_name='Bulk Target', proposal_reference='TST-BULK-1',
            region=self.region, status=self.status, owner=self.user)
        self.url = reverse('projects:add_documents_bulk', kwargs={'pk': self.project.pk})

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_get_renders_with_document_type_choices(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['document_type_choices'], self.Document.DOCUMENT_TYPE_CHOICES)

    def test_post_no_files_shows_error_and_creates_nothing(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {})
        self.assertRedirects(response, self.url)
        self.assertEqual(self.Document.objects.count(), 0)

    def test_post_happy_path_creates_one_document_per_file_with_chosen_types(self):
        self.client.force_login(self.user)
        f1 = SimpleUploadedFile('one.pdf', b'%PDF-1', content_type='application/pdf')
        f2 = SimpleUploadedFile('two.docx', b'PK-fake', content_type='application/msword')
        response = self.client.post(self.url, {
            'files': [f1, f2],
            'document_type_0': 'po_document',
            'document_type_1': 'contract',
        })
        self.assertRedirects(response, reverse('projects:detail', kwargs={'pk': self.project.pk}))
        self.assertEqual(self.Document.objects.filter(project=self.project).count(), 2)
        self.assertEqual(self.Document.objects.get(name='one.pdf').document_type, 'po_document')
        self.assertEqual(self.Document.objects.get(name='two.docx').document_type, 'contract')

    def test_post_invalid_type_fails_closed_to_other(self):
        self.client.force_login(self.user)
        f1 = SimpleUploadedFile('one.pdf', b'%PDF-1', content_type='application/pdf')
        self.client.post(self.url, {'files': [f1], 'document_type_0': '<script>alert(1)</script>'})
        self.assertEqual(self.Document.objects.get(name='one.pdf').document_type, 'other')

    def test_post_missing_type_for_a_file_fails_closed_to_other(self):
        self.client.force_login(self.user)
        f1 = SimpleUploadedFile('one.pdf', b'%PDF-1', content_type='application/pdf')
        self.client.post(self.url, {'files': [f1]})  # no document_type_0 at all
        self.assertEqual(self.Document.objects.get(name='one.pdf').document_type, 'other')

    def test_csrf_enforced_creates_nothing(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        f1 = SimpleUploadedFile('one.pdf', b'%PDF-1', content_type='application/pdf')
        response = csrf_client.post(self.url, {'files': [f1], 'document_type_0': 'po_document'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.Document.objects.count(), 0)


class ProjectCreateViewPickedEmailTests(TestCase):
    """The deferred-materialization design: picking an email while creating
    a new pipeline entry must never touch the database until the real
    'Create' button is clicked, and the project/costing-sheet/notification
    side effects that already fire on every real create must keep firing
    unconditionally, whether or not the picked email attaches successfully.

    This whole feature exists because an earlier version got this wrong —
    it auto-created a real Project the moment an email was picked. These
    tests guard that regression directly."""

    def setUp(self):
        from projects.models import Document, PipelineEmail
        self.Document = Document
        self.PipelineEmail = PipelineEmail
        from accounts.permissions import seed_default_permissions
        self.region = Region.objects.create(name='Test Region', code='TST')  # non-LNA
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        seed_default_permissions()
        self.user = User.objects.create_user('pmcreator', password='pass12345')
        self.user.role = role
        self.user.region = self.region
        self.user.save()
        self.client.force_login(self.user)
        self.url = reverse('projects:create')

    def _post_data(self, ref, **over):
        d = {
            'project_name': 'New Pipeline', 'proposal_reference': ref,
            'status': self.status.pk, 'region': self.region.pk, 'owner': '',
            'estimated_value': '0', 'estimated_value_usd': '0',
            'estimated_value_per_annum': '0', 'estimated_gp': '0',
            'actual_sales': '0', 'success_quotient': '0',
            'client_rfq_reference': '', 'po_number': '', 'customer': '',
            'end_user': '', 'project_stage': '', 'year': '', 'po_award_quarter': '',
            'contact_with': '', 'remarks': '', 'notes': '', 'portal_url': '',
        }
        d.update(over)
        return d

    def _picked_email_json(self, message_id='msg-9', document_type='po_document',
                            filename='quote.pdf'):
        return json.dumps({
            'id': message_id, 'subject': 'Vendor Quote Msg', 'sender_name': 'Vendor',
            'sender_email': 'vendor@example.com', 'cc': '', 'received_at': '2026-08-01T10:00:00Z',
            'body_preview': 'preview',
            'attachments': [{
                'id': 'att-1', 'name': filename, 'content_type': 'application/pdf',
                'size': 10, 'document_type': document_type,
            }],
        })

    # --- GET must never create anything, must only fetch on GET ---

    def test_get_without_picked_email_never_calls_graph(self):
        with patch('projects.graph_mail.get_message_summary') as mock_summary:
            response = self.client.get(self.url)
        mock_summary.assert_not_called()
        self.assertEqual(response.status_code, 200)

    def test_get_with_picked_email_populates_initial_and_creates_nothing(self):
        """The core regression guard: merely landing on the create page with
        ?picked_email=... (a GET) must never create a Project."""
        summary = {
            'id': 'msg-9', 'subject': 'Vendor Quote Msg', 'sender_name': 'Vendor',
            'sender_email': 'vendor@example.com', 'cc': '', 'received_at': '2026-08-01T10:00:00Z',
            'body_preview': 'preview',
            'attachments': [{'id': 'att-1', 'name': 'quote.pdf',
                              'content_type': 'application/pdf', 'size': 10}],
        }
        with patch('projects.graph_mail.get_message_summary', return_value=summary):
            response = self.client.get(self.url, {'picked_email': 'msg-9'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(self.PipelineEmail.objects.count(), 0)

        picked = json.loads(response.context['form'].initial['picked_email_json'])
        self.assertEqual(picked['id'], 'msg-9')
        self.assertEqual(picked['attachments'][0]['document_type'], 'vendor_quotation')

        msgs = [str(m) for m in response.context['messages']]
        self.assertFalse(any('created successfully' in m.lower() for m in msgs))

    def test_get_with_picked_email_graph_failure_degrades_gracefully(self):
        with patch('projects.graph_mail.get_message_summary',
                   side_effect=graph_mail.GraphMailError('mailbox unreachable')):
            response = self.client.get(self.url, {'picked_email': 'msg-9'})
        self.assertEqual(response.status_code, 200)  # never a 500
        self.assertEqual(Project.objects.count(), 0)
        msgs = [str(m) for m in response.context['messages']]
        self.assertTrue(any('could not load that email' in m.lower() for m in msgs))

    # --- a real Create POST is unaffected when no email was picked ---

    def test_post_real_create_without_picked_email_is_unaffected(self):
        with patch('projects.graph_mail.fetch_raw_message_bytes') as mock_fetch:
            response = self.client.post(self.url, self._post_data('TST-NOEMAIL-1'))
        mock_fetch.assert_not_called()
        self.assertEqual(response.status_code, 302)
        project = Project.objects.get(project_name='New Pipeline')
        self.assertTrue(CostingSheet.objects.filter(project=project).exists())
        self.assertEqual(self.PipelineEmail.objects.filter(project=project).count(), 0)

        response = self.client.get(response.url)
        msgs = [str(m) for m in response.context['messages']]
        self.assertEqual(sum('created successfully' in m.lower() for m in msgs), 1)

    # --- POST with a picked email materializes it, on top of the existing behavior ---

    def test_post_create_with_picked_email_materializes_email_and_document(self):
        raw = _make_eml_bytes(subject='Vendor Quote Msg', attachments=[
            ('quote.pdf', b'%PDF-fake', 'application', 'pdf')])
        data = self._post_data('TST-WITHEMAIL-1',
                                picked_email_json=self._picked_email_json())
        with patch('projects.graph_mail.fetch_raw_message_bytes', return_value=raw) as mock_fetch:
            response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args[0][1], 'msg-9')  # picked['id'] passed through

        project = Project.objects.get(project_name='New Pipeline')
        # Existing, unconditional create side effects: unaffected.
        self.assertTrue(CostingSheet.objects.filter(project=project).exists())
        # New: the picked email is now a real row, with the chosen type applied.
        pipeline_email = self.PipelineEmail.objects.get(project=project)
        self.assertEqual(pipeline_email.subject, 'Vendor Quote Msg')
        doc = self.Document.objects.get(project=project, source_pipeline_email=pipeline_email)
        self.assertEqual(doc.name, 'quote.pdf')
        self.assertEqual(doc.document_type, 'po_document')

        response = self.client.get(response.url)
        msgs = [str(m) for m in response.context['messages']]
        self.assertEqual(sum('created successfully' in m.lower() for m in msgs), 1)
        self.assertFalse(any('could not be attached' in m.lower() for m in msgs))

    def test_post_create_graph_failure_still_creates_project_and_warns(self):
        """The project/costing-sheet/notification are already durably
        committed by the time the picked email is materialized — a Graph
        failure at that point must degrade to a warning, never roll back
        or 500 on top of an otherwise-successful create."""
        data = self._post_data('TST-GRAPHFAIL-1', picked_email_json=self._picked_email_json())
        before = Project.objects.count()
        with patch('projects.graph_mail.fetch_raw_message_bytes',
                   side_effect=graph_mail.GraphMailError('mailbox unreachable')):
            response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Project.objects.count(), before + 1)
        project = Project.objects.get(project_name='New Pipeline')
        self.assertTrue(CostingSheet.objects.filter(project=project).exists())
        self.assertEqual(self.PipelineEmail.objects.filter(project=project).count(), 0)

        response = self.client.get(response.url)
        msgs = [str(m) for m in response.context['messages']]
        self.assertTrue(any('could not be attached' in m.lower() for m in msgs))

    def test_post_create_with_malformed_picked_email_json_does_not_crash(self):
        data = self._post_data('TST-MALFORMED-1', picked_email_json='not-valid-json{{{')
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)  # never a 500
        project = Project.objects.get(project_name='New Pipeline')
        self.assertTrue(CostingSheet.objects.filter(project=project).exists())

    def test_post_create_with_picked_email_missing_id_does_not_crash(self):
        data = self._post_data(
            'TST-NOID-1', picked_email_json=json.dumps({'attachments': []}))
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)  # never a 500
        project = Project.objects.get(project_name='New Pipeline')
        self.assertTrue(CostingSheet.objects.filter(project=project).exists())
        self.assertEqual(self.PipelineEmail.objects.filter(project=project).count(), 0)

    def test_unauthorized_role_get_does_not_call_graph(self):
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        other = User.objects.create_user('finance5', password='x', role=finance_role,
                                          region=self.region)
        self.client.force_login(other)
        with patch('projects.graph_mail.get_message_summary') as mock_summary:
            response = self.client.get(self.url, {'picked_email': 'msg-1'})
        mock_summary.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('picked_email_json', response.context['form'].initial)

    def test_unauthorized_role_forged_picked_email_json_is_ignored_on_create(self):
        # Even if a non-authorized user directly forges the hidden field
        # (bypassing the picker UI, which they can't reach anyway), the
        # project must still be created normally with no email attached —
        # defense in depth on top of the picker/attach endpoints themselves
        # being blocked.
        finance_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        other = User.objects.create_user('finance6', password='x', role=finance_role,
                                          region=self.region)
        self.client.force_login(other)
        data = self._post_data('TST-FORGED-1', picked_email_json=self._picked_email_json())
        with patch('projects.graph_mail.fetch_raw_message_bytes') as mock_fetch:
            response = self.client.post(self.url, data)
        mock_fetch.assert_not_called()
        self.assertEqual(response.status_code, 302)
        project = Project.objects.get(project_name='New Pipeline')
        self.assertTrue(CostingSheet.objects.filter(project=project).exists())
        self.assertEqual(self.PipelineEmail.objects.filter(project=project).count(), 0)


class PipelineEmailFeatureAccessTests(TestCase):
    """_can_use_pipeline_email_feature() is the single gate behind every
    Add Emails/attach/delink entry point — unit-tested directly against
    every role so the view-level tests don't need to repeat this matrix."""

    def _user(self, role_name):
        from projects.views import _can_use_pipeline_email_feature
        role, _ = Role.objects.get_or_create(name=role_name)
        user = User.objects.create_user(f'u_{role_name}', password='x', role=role)
        return _can_use_pipeline_email_feature(user)

    def test_super_admin_allowed(self):
        self.assertTrue(self._user(Role.SUPER_ADMIN))

    def test_admin_allowed(self):
        self.assertTrue(self._user(Role.ADMIN))

    def test_sales_rep_allowed(self):
        self.assertTrue(self._user(Role.SALES_REP))

    def test_manager_denied(self):
        self.assertFalse(self._user(Role.MANAGER))

    def test_proposal_rep_denied(self):
        self.assertFalse(self._user(Role.PROPOSAL_REP))

    def test_finance_rep_denied(self):
        self.assertFalse(self._user(Role.FINANCE_REP))

    def test_procurement_officer_denied(self):
        self.assertFalse(self._user(Role.PROCUREMENT_OFF))

    def test_user_with_no_role_denied(self):
        from projects.views import _can_use_pipeline_email_feature
        user = User.objects.create_user('noroleuser', password='x')
        self.assertFalse(_can_use_pipeline_email_feature(user))


class GraphMailUrlEncodingTests(TestCase):
    """message_id/attachment_id trace back to user input (a POST field, or
    JSON riding through the create-form draft) with no format validation
    before graph_mail builds a Graph API request URL from them. A crafted
    id containing '/' or '..' segments must be percent-encoded, not placed
    literally in the URL path — otherwise it could redirect this server-
    side, app-only-authenticated request to a completely different Graph
    resource (e.g. another mailbox) than PIPELINE_EMAIL_MAILBOX restricts
    this feature to."""

    def setUp(self):
        patcher = patch('projects.graph_mail._get_access_token', return_value='tok')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.traversal_id = '../../../users/someone-else@leap-arabia.com/messages/x'

    def test_fetch_raw_message_bytes_encodes_traversal_attempt(self):
        with patch('projects.graph_mail.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.content = b'raw-bytes'
            graph_mail.fetch_raw_message_bytes('shared@leap-arabia.com', self.traversal_id)
        called_url = mock_get.call_args[0][0]
        self.assertNotIn('/../', called_url)
        self.assertTrue(called_url.startswith(
            'https://graph.microsoft.com/v1.0/users/shared%40leap-arabia.com/messages/'))
        # The mailbox segment must stay exactly one segment — the encoded id
        # can't reintroduce '/' as a literal path separator.
        path_after_messages = called_url.split('/messages/', 1)[1]
        self.assertNotIn('/', path_after_messages.rstrip('/$value'))

    def test_get_message_summary_encodes_traversal_attempt(self):
        with patch('projects.graph_mail.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                'id': 'x', 'subject': 's', 'from': {}, 'value': []}
            graph_mail.get_message_summary('shared@leap-arabia.com', self.traversal_id)
        first_call_url = mock_get.call_args_list[0][0][0]
        self.assertNotIn('/../', first_call_url)

    def test_fetch_attachment_bytes_encodes_message_and_attachment_id(self):
        import base64
        with patch('projects.graph_mail.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                'name': 'a.pdf', 'contentType': 'application/pdf',
                'contentBytes': base64.b64encode(b'x').decode(),
            }
            graph_mail.fetch_attachment_bytes(
                'shared@leap-arabia.com', self.traversal_id, '../also/../elsewhere')
        called_url = mock_get.call_args[0][0]
        self.assertNotIn('/../', called_url)
