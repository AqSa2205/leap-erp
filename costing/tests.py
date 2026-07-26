import tempfile
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Region, ProjectStatus, Project
from costing.models import CostingSheet
from django.test import TestCase, Client
from accounts.models import User


class CostingExcelExportLinkTests(TestCase):
    """BUG-005: the Excel export link must not use target="_blank",
    since a file download response leaves an empty tab open."""

    def setUp(self):
        self.client = Client()
        from accounts.models import Role
        from accounts.permissions import seed_default_permissions
        sales_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        seed_default_permissions()
        self.user = User.objects.create_user(
            username='exportuser', password='testpass123', role=sales_role,
        )
        self.client.login(username='exportuser', password='testpass123')

    def test_costing_list_export_link_has_no_target_blank(self):
        response = self.client.get('/costing/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # crude but effective: the export link shouldn't pair href with target="_blank"
        self.assertNotIn('Export Excel', content.split('target="_blank"')[0][-200:]
                          if 'target="_blank"' in content else 'Export Excel')

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

    def test_bom_stage_proposal_and_sales(self):
        # BOM stage is now open to proposal, sales and admins.
        s = self._sheet('bom_in_progress')
        self.assertTrue(self._can(self.proposal, s))
        self.assertTrue(self._can(self.sales, s))
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

    def test_sales_allowed_during_bom(self):
        # BOM stage opened to sales/admins — sales can now edit a BOM sheet.
        self.assertEqual(self._mutate(self.sales, self._sheet('bom_in_progress')).status_code, 200)

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
        # BOM stage is open to proposal AND sales/admins now.
        s = self._sheet('bom_in_progress')
        self.client.force_login(self.sales)
        resp = self.client.get(reverse('costing:detail', kwargs={'pk': s.pk}))
        self.assertTrue(resp.context['can_edit'])
        self.client.force_login(self.proposal)
        resp = self.client.get(reverse('costing:detail', kwargs={'pk': s.pk}))
        self.assertTrue(resp.context['can_edit'])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RevisionDedupTests(TestCase):
    """Re-exporting an unchanged sheet must not create a duplicate revision
    (R2 file + DB row). A genuine content edit — including header/T&C/per-item
    text that the compact diff summary ignores — must still create a new one.
    De-dupe is scoped per format, so one PDF and one Excel archive both survive.
    """

    def setUp(self):
        from costing.models import (
            CostingSection, CostingLineItem, ExchangeRate,
        )
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='P', proposal_reference='REF-REV',
            status=self.status, region=self.region)
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('rev', password='x')
        self.user.role = role
        self.user.region = self.region
        self.user.save()
        ExchangeRate.objects.get_or_create(
            currency_code='SAR',
            defaults={'currency_name': 'Saudi Riyal', 'rate_to_usd': Decimal('3.75')})
        self.sheet = CostingSheet.objects.create(
            title='S', project=self.project, created_by=self.user,
            customer_name='Acme')
        self.section = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='A.1', title='Supply')
        self.item = CostingLineItem.objects.create(
            section=self.section, item_number='1', description='Camera',
            base_unit_cost=Decimal('100'))

    def _save(self, fmt='pdf'):
        from costing.views import _save_costing_revision
        return _save_costing_revision(
            self.sheet, b'%PDF-1.4 fake-bytes', f'offer.{fmt}',
            export_format=fmt, user=self.user)

    def _count(self):
        from costing.models import CostingSheetRevision
        return CostingSheetRevision.objects.filter(sheet=self.sheet).count()

    def test_unchanged_reexport_is_noop(self):
        r1 = self._save('pdf')
        r2 = self._save('pdf')
        self.assertEqual(self._count(), 1)
        self.assertEqual(r1.pk, r2.pk)  # same row returned, no new file

    def test_priced_edit_creates_new_revision(self):
        self._save('pdf')
        self.item.base_unit_cost = Decimal('250')  # moves the subtotal/totals
        self.item.save()
        self._save('pdf')
        self.assertEqual(self._count(), 2)

    def test_item_text_only_edit_creates_new_revision(self):
        # Description doesn't change any total — the expanded per-item fingerprint
        # both triggers the new revision AND reports the edit in the details.
        self._save('pdf')
        self.item.description = 'Camera (revised model)'
        self.item.save()
        rev = self._save('pdf')
        self.assertEqual(self._count(), 2)
        self.assertIn('Item 1 description', rev.change_summary)
        self.assertTrue(rev.change_details)  # detail rows, not "no changes"

    def test_header_only_edit_creates_new_revision(self):
        # customer_name isn't a total, but it must show up in the change details.
        self._save('pdf')
        self.sheet.customer_name = 'Globex'
        self.sheet.save()
        rev = self._save('pdf')
        self.assertEqual(self._count(), 2)
        self.assertIn('Globex', rev.change_summary)
        self.assertTrue(any(d['field'] == 'header.customer_name'
                            for d in rev.change_details))

    def test_same_content_different_format_both_kept(self):
        self._save('pdf')
        self._save('excel')  # identical content, different format -> kept
        self.assertEqual(self._count(), 2)
        # re-exporting each format again with no change is a no-op
        self._save('pdf')
        self._save('excel')
        self.assertEqual(self._count(), 2)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RevisionCleanupTests(TestCase):
    """'Clean up old exports' keeps the latest revision of each format and
    deletes the rest (row + file), reclaiming storage."""

    def setUp(self):
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='P', proposal_reference='REF-CLEAN',
            status=self.status, region=self.region)
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('clean', password='x')
        self.user.role = role
        self.user.region = self.region
        self.user.save()
        self.sheet = CostingSheet.objects.create(
            title='S', project=self.project, created_by=self.user)
        self.client.force_login(self.user)

    def _mk(self, label, fmt):
        from django.core.files.base import ContentFile
        from costing.models import CostingSheetRevision
        rev = CostingSheetRevision(sheet=self.sheet, revision_label=label,
                                   export_format=fmt)
        rev.file.save(f'{label}.{fmt}', ContentFile(b'payload-bytes'), save=True)
        return rev

    def _cleanup(self):
        return self.client.post(
            reverse('costing:cleanup_revisions', kwargs={'pk': self.sheet.pk}))

    def test_keeps_latest_of_each_format(self):
        from costing.models import CostingSheetRevision
        for lbl in ('R00', 'R01', 'R02'):
            self._mk(lbl, 'pdf')
        for lbl in ('R03', 'R04'):
            self._mk(lbl, 'excel')
        self.assertEqual(self._cleanup().status_code, 302)
        remaining = CostingSheetRevision.objects.filter(sheet=self.sheet)
        self.assertEqual(remaining.count(), 2)
        self.assertEqual(remaining.filter(export_format='pdf').count(), 1)
        self.assertEqual(remaining.filter(export_format='excel').count(), 1)

    def test_deleted_file_removed_from_storage(self):
        from datetime import timedelta
        from django.utils import timezone
        from django.core.files.storage import default_storage
        from costing.models import CostingSheetRevision
        old = self._mk('R00', 'pdf')
        new = self._mk('R01', 'pdf')  # newer pdf -> kept
        CostingSheetRevision.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=1))
        CostingSheetRevision.objects.filter(pk=new.pk).update(
            created_at=timezone.now())
        old_name = old.file.name
        self.assertTrue(default_storage.exists(old_name))
        self._cleanup()
        self.assertFalse(default_storage.exists(old_name))  # storage reclaimed

    def test_nothing_to_clean_keeps_both(self):
        from costing.models import CostingSheetRevision
        self._mk('R00', 'pdf')
        self._mk('R01', 'excel')
        self._cleanup()
        self.assertEqual(
            CostingSheetRevision.objects.filter(sheet=self.sheet).count(), 2)

    def test_non_editor_cannot_clean_up(self):
        from costing.models import CostingSheetRevision
        self._mk('R00', 'pdf')
        self._mk('R01', 'pdf')
        outsider = User.objects.create_user('outsider', password='x')  # no role
        self.client.force_login(outsider)
        self._cleanup()
        self.assertEqual(
            CostingSheetRevision.objects.filter(sheet=self.sheet).count(), 2)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FileCleanupSignalTests(TestCase):
    """The central post_delete / pre_save signals delete the stored file on
    cascade delete, bulk delete, and field replacement — the paths the
    per-view cleanup never covered."""

    def setUp(self):
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='P', proposal_reference='REF-SIG',
            status=self.status, region=self.region)
        self.user = User.objects.create_user('sig', password='x')
        self.sheet = CostingSheet.objects.create(
            title='S', project=self.project, created_by=self.user)

    def _mk(self, label, fmt='pdf'):
        from django.core.files.base import ContentFile
        from costing.models import CostingSheetRevision
        rev = CostingSheetRevision(sheet=self.sheet, revision_label=label,
                                   export_format=fmt)
        rev.file.save(f'{label}.{fmt}', ContentFile(b'payload'), save=True)
        return rev

    def test_cascade_delete_removes_files(self):
        from django.core.files.storage import default_storage
        r0, r1 = self._mk('R00'), self._mk('R01')
        n0, n1 = r0.file.name, r1.file.name
        self.assertTrue(default_storage.exists(n0) and default_storage.exists(n1))
        self.sheet.delete()  # cascades to revisions; no per-view cleanup involved
        self.assertFalse(default_storage.exists(n0))
        self.assertFalse(default_storage.exists(n1))

    def test_bulk_queryset_delete_removes_files(self):
        from django.core.files.storage import default_storage
        from costing.models import CostingSheetRevision
        r0, r1 = self._mk('R00'), self._mk('R01')
        n0, n1 = r0.file.name, r1.file.name
        CostingSheetRevision.objects.filter(sheet=self.sheet).delete()  # bulk
        self.assertFalse(default_storage.exists(n0))
        self.assertFalse(default_storage.exists(n1))

    def test_replacing_file_deletes_old(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        rev = self._mk('R00')
        old_name = rev.file.name
        rev.file.save('R00_new.pdf', ContentFile(b'new payload'), save=True)
        self.assertNotEqual(rev.file.name, old_name)
        self.assertFalse(default_storage.exists(old_name))   # old reclaimed
        self.assertTrue(default_storage.exists(rev.file.name))  # new kept


class MarginScenarioTests(TestCase):
    """Finance margin analysis: Cost/Price/Profit per system for M1–M4."""

    def setUp(self):
        from decimal import Decimal
        from accounts.models import User, Role
        from costing.models import CostingSheet, CostingSection, CostingLineItem
        self.sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('fin', password='pw', role=self.sa)
        self.sheet = CostingSheet.objects.create(
            title='S', created_by=self.user, margin=Decimal('40'),
            margin_high=Decimal('50'), margin_medium=Decimal('25'),
            margin_low=Decimal('20'))
        sec = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='1', title='CCTV SYSTEM', order=0)
        CostingLineItem.objects.create(
            section=sec, description='Cam', quantity=Decimal('2'),
            base_unit_cost=Decimal('100'), supplier_currency='SAR',
            margin=Decimal('40'))

    def test_grand_total_includes_services_without_margin(self):
        from decimal import Decimal
        from costing.models import ScopeOfWorkItem
        # A.2 Services billed at cost (no margin) — adds equally to grand
        # cost and price, contributing zero profit.
        ScopeOfWorkItem.objects.create(
            costing_sheet=self.sheet, serial_number=1, description='Install',
            quantity=Decimal('1'), uom='LOT', total_price=Decimal('500'))
        sc = {s['key']: s for s in self.sheet.margin_scenarios()}
        # M2 high 50%: supply price 400 + services 500 = grand 900
        self.assertEqual(sc['M2']['services'], Decimal('500.00'))
        self.assertEqual(sc['M2']['grand_price'], Decimal('900.00'))
        self.assertEqual(sc['M2']['grand_cost'], Decimal('700.00'))   # 200 + 500
        self.assertEqual(sc['M2']['grand_profit'], Decimal('200.00'))  # services add 0

    def test_scenarios_cost_price_profit(self):
        from decimal import Decimal
        sc = {s['key']: s for s in self.sheet.margin_scenarios()}
        # Cost is constant: unit_cost_sar(100) * qty(2) = 200
        self.assertEqual(sc['M1']['total_cost'], Decimal('200.00'))
        # M2 high 50%: price = 200/(1-0.5) = 400, profit = 200
        self.assertEqual(sc['M2']['total_price'], Decimal('400.00'))
        self.assertEqual(sc['M2']['total_profit'], Decimal('200.00'))
        # M3 medium 25%: 200/0.75 = 266.67
        self.assertEqual(sc['M3']['total_price'], Decimal('266.67'))
        # M1 current (40%): 200/0.6 = 333.33
        self.assertEqual(sc['M1']['total_price'], Decimal('333.34'))  # per-unit rounding
        # one consolidated system row
        self.assertEqual(len(sc['M1']['rows']), 1)
        self.assertEqual(sc['M1']['rows'][0]['system'], 'CCTV SYSTEM')

    def test_unset_scenario_marked_not_configured(self):
        self.sheet.margin_high = None
        self.sheet.save()
        sc = {s['key']: s for s in self.sheet.margin_scenarios()}
        self.assertFalse(sc['M2']['configured'])
        self.assertTrue(sc['M1']['configured'])

    def test_view_blocks_procurement_role(self):
        from django.urls import reverse
        from accounts.models import User, Role
        proc_role, _ = Role.objects.get_or_create(name=Role.PROCUREMENT_OFF)
        proc = User.objects.create_user('p', password='pw', role=proc_role)
        self.client.force_login(proc)
        r = self.client.get(reverse('costing:margin_analysis', kwargs={'pk': self.sheet.pk}))
        self.assertEqual(r.status_code, 302)  # redirected away

    def test_view_allows_super_admin(self):
        from django.urls import reverse
        self.client.force_login(self.user)  # super admin
        r = self.client.get(reverse('costing:margin_analysis', kwargs={'pk': self.sheet.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'CCTV SYSTEM', r.content)

    def test_view_allows_finance(self):
        from django.urls import reverse
        from accounts.models import User, Role
        fin_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        fin = User.objects.create_user('finrep', password='pw', role=fin_role)
        self.client.force_login(fin)
        r = self.client.get(reverse('costing:margin_analysis', kwargs={'pk': self.sheet.pk}))
        self.assertEqual(r.status_code, 200)

    def test_view_blocks_non_finance_creator(self):
        # A sales rep who created their own sheet is no longer allowed —
        # access is finance + super admin only.
        from django.urls import reverse
        from accounts.models import User, Role
        from costing.models import CostingSheet
        sales_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        sales = User.objects.create_user('salesrep', password='pw', role=sales_role)
        own_sheet = CostingSheet.objects.create(title='Own', created_by=sales)
        self.client.force_login(sales)
        r = self.client.get(reverse('costing:margin_analysis', kwargs={'pk': own_sheet.pk}))
        self.assertEqual(r.status_code, 302)  # blocked

    def test_post_updates_scenario_margins(self):
        from decimal import Decimal
        from django.urls import reverse
        self.client.force_login(self.user)
        r = self.client.post(
            reverse('costing:margin_analysis', kwargs={'pk': self.sheet.pk}),
            {'margin_high': '60', 'margin_medium': '40', 'margin_low': ''})
        self.assertEqual(r.status_code, 302)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.margin_high, Decimal('60'))
        self.assertEqual(self.sheet.margin_medium, Decimal('40'))
        self.assertIsNone(self.sheet.margin_low)  # blank clears it


class RenumberOnDeleteTests(TestCase):
    """Deleting a middle line item re-sequences the section's item numbers
    (gap-free), preserving sub-items and named rows."""

    def setUp(self):
        from costing.models import CostingSection, CostingLineItem
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('sa2', password='x', role=role)
        self.client.force_login(self.user)
        self.sheet = CostingSheet.objects.create(title='S', created_by=self.user, margin=Decimal('30'))
        self.sec = CostingSection.objects.create(costing_sheet=self.sheet, section_number='1', title='A', order=0)
        self.items = []
        for o, n in enumerate(['1.1', '1.2', '1.3', '1.4']):
            self.items.append(CostingLineItem.objects.create(
                section=self.sec, item_number=n, description='x', quantity=Decimal('1'),
                base_unit_cost=Decimal('1'), supplier_currency='SAR', order=o))

    def _nums(self):
        return [i.item_number for i in self.sec.line_items.all().order_by('order', 'item_number')]

    def test_delete_middle_row_renumbers(self):
        # Delete "1.2" via the delete view — remaining rows close the gap.
        r = self.client.post(reverse('costing:item_delete', kwargs={'pk': self.items[1].pk}))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._nums(), ['1.1', '1.2', '1.3'])

    def test_named_row_preserved_on_renumber(self):
        from costing.models import CostingLineItem
        CostingLineItem.objects.create(
            section=self.sec, item_number='Services', description='svc', quantity=Decimal('1'),
            base_unit_cost=Decimal('0'), supplier_currency='SAR', order=1)
        # order now: 1.1(0), Services(1), 1.2(1→resorts), ... delete 1.1
        self.client.post(reverse('costing:item_delete', kwargs={'pk': self.items[0].pk}))
        self.assertIn('Services', self._nums())          # named row untouched
        numeric = [n for n in self._nums() if n[0].isdigit()]
        self.assertEqual(numeric, ['1.1', '1.2', '1.3'])  # gap-free


class CommercialPipelineTests(TestCase):
    """The commercial-pipeline additions: the Start-BOM transition off the new
    'BOM not started' first stage, the stage-count tabs on the costing list, and
    the pipeline PDF export."""

    def setUp(self):
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='Pipeline P', proposal_reference='REF-PIPE',
            status=self.status, region=self.region)

        def mkuser(username, role_name):
            role, _ = Role.objects.get_or_create(name=role_name)
            u = User.objects.create_user(username, password='x')
            u.role = role; u.region = self.region; u.save()
            return u
        self.superadmin = mkuser('sa', Role.SUPER_ADMIN)
        self.proposal = mkuser('pr', Role.PROPOSAL_REP)
        self.sales = mkuser('sr', Role.SALES_REP)

    def _sheet(self, stage='bom_not_started'):
        return CostingSheet.objects.create(
            title='S', project=self.project, created_by=self.proposal,
            workflow_stage=stage)

    def _transition(self, user, sheet, action):
        self.client.force_login(user)
        return self.client.post(
            reverse('costing:workflow_transition', kwargs={'pk': sheet.pk}),
            {'action': action})

    def test_proposal_starts_bom(self):
        sheet = self._sheet('bom_not_started')
        self._transition(self.proposal, sheet, 'start_bom')
        sheet.refresh_from_db()
        self.assertEqual(sheet.workflow_stage, 'bom_in_progress')
        self.assertIsNotNone(sheet.bom_started_at)
        self.assertEqual(sheet.bom_started_by, self.proposal)

    def test_sales_can_start_bom(self):
        # Start BOM is now open to sales/admins as well as proposal.
        sheet = self._sheet('bom_not_started')
        self._transition(self.sales, sheet, 'start_bom')
        sheet.refresh_from_db()
        self.assertEqual(sheet.workflow_stage, 'bom_in_progress')

    def test_default_stage_is_bom_not_started(self):
        sheet = CostingSheet.objects.create(
            title='D', project=self.project, created_by=self.proposal)
        self.assertEqual(sheet.workflow_stage, 'bom_not_started')

    def test_list_shows_stage_tabs_and_progress(self):
        self._sheet('bom_not_started')
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse('costing:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'BOM not started')      # tab + row badge
        self.assertContains(resp, 'Workflow Progress')    # renamed column header
        self.assertContains(resp, 'Customer Name')        # renamed column header
        self.assertContains(resp, 'Export PDF')

    def test_list_renders_sheet_without_project(self):
        # A sheet with no linked project must not crash the Customer Name cell.
        CostingSheet.objects.create(
            title='Orphan', project=None, created_by=self.proposal,
            workflow_stage='bom_in_progress')
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse('costing:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Orphan')

    def test_list_search_matches_customer_and_reference(self):
        match = CostingSheet.objects.create(
            title='ZZ', project=self.project, created_by=self.proposal,
            customer_name='Aramco Digital', workflow_stage='bom_in_progress')
        other = CostingSheet.objects.create(
            title='Unrelated', project=self.project, created_by=self.proposal,
            customer_name='Someone Else', workflow_stage='bom_in_progress')
        self.client.force_login(self.superadmin)
        # Search by customer name finds the sheet…
        ids = [s.pk for s in self.client.get(
            reverse('costing:list'), {'search': 'Aramco'}).context['sheets']]
        self.assertIn(match.pk, ids)
        self.assertNotIn(other.pk, ids)
        # …and searching by the project reference finds both (same project).
        ids2 = [s.pk for s in self.client.get(
            reverse('costing:list'), {'search': 'REF-PIPE'}).context['sheets']]
        self.assertIn(match.pk, ids2)
        self.assertIn(other.pk, ids2)

    def test_pipeline_pdf_export(self):
        self._sheet('bom_in_progress')
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse('costing:pipeline_pdf'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))

    def test_pipeline_excel_export(self):
        self._sheet('bom_in_progress')
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse('costing:pipeline_excel'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])
        self.assertTrue(resp.content.startswith(b'PK'))   # .xlsx is a zip

    def test_stage_multiselect_filter(self):
        a = self._sheet('bom_in_progress')
        b = self._sheet('ready_for_costing')
        c = self._sheet('finalized')
        self.client.force_login(self.superadmin)
        ids = [s.pk for s in self.client.get(
            reverse('costing:list'),
            {'stage': ['bom_in_progress', 'ready_for_costing']}).context['sheets']]
        self.assertIn(a.pk, ids)
        self.assertIn(b.pk, ids)
        self.assertNotIn(c.pk, ids)   # finalized excluded

    def test_timeline_period_filter(self):
        from django.utils import timezone
        from datetime import timedelta
        old = self._sheet('bom_in_progress')
        CostingSheet.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=60))
        recent = self._sheet('bom_in_progress')  # created just now
        self.client.force_login(self.superadmin)
        ids = [s.pk for s in self.client.get(
            reverse('costing:list'), {'period': '1m'}).context['sheets']]
        self.assertIn(recent.pk, ids)
        self.assertNotIn(old.pk, ids)   # older than 1 month → filtered out

    def test_timeline_date_range_filter(self):
        from django.utils import timezone
        from datetime import timedelta
        old = self._sheet('bom_in_progress')
        CostingSheet.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=60))
        recent = self._sheet('bom_in_progress')
        frm = (timezone.now() - timedelta(days=90)).date().isoformat()
        to = (timezone.now() - timedelta(days=30)).date().isoformat()
        self.client.force_login(self.superadmin)
        ids = [s.pk for s in self.client.get(
            reverse('costing:list'), {'date_from': frm, 'date_to': to}).context['sheets']]
        self.assertIn(old.pk, ids)      # inside the 90–30 days-ago window
        self.assertNotIn(recent.pk, ids)

    def test_pipeline_pdf_with_project_fields(self):
        # The PDF pulls Sr #, revision (from the reference), end user, priority,
        # estimated value and submission date off the project; make sure a fully
        # populated project renders (guards the date-vs-datetime + display paths).
        from datetime import date
        from decimal import Decimal
        self.project.serial_number = 42
        self.project.proposal_reference = 'LNA 2817 (R03)'
        self.project.end_user = 'Acme Power'
        self.project.priority = 'high'
        self.project.estimated_value = Decimal('1234567.00')
        self.project.submission_deadline = date(2026, 5, 20)
        self.project.save()
        self._sheet('ready_for_costing')
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse('costing:pipeline_pdf'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b'%PDF'))

    # ─── Cycle-time (working days per stage) ───
    def _aware(self, y, m, d):
        from datetime import datetime
        from django.utils import timezone
        return timezone.make_aware(datetime(y, m, d, 10, 0))

    def test_working_days_skips_weekend(self):
        from costing.models import working_days_between
        # 2026-07-09 Thu → 2026-07-12 Sun: Fri+Sat skipped, only Sun counts = 1.
        self.assertEqual(working_days_between(self._aware(2026, 7, 9),
                                              self._aware(2026, 7, 12)), 1)
        # Same day = 0; missing bound = None.
        self.assertEqual(working_days_between(self._aware(2026, 7, 9),
                                              self._aware(2026, 7, 9)), 0)
        self.assertIsNone(working_days_between(None, self._aware(2026, 7, 9)))

    def test_cycle_rows_days_and_person(self):
        sheet = self._sheet('finalized')
        CostingSheet.objects.filter(pk=sheet.pk).update(
            bom_started_at=self._aware(2026, 7, 5),      # Sun
            handed_over_at=self._aware(2026, 7, 7),      # Tue
            costing_started_at=self._aware(2026, 7, 7),  # Tue
            finalized_at=self._aware(2026, 7, 9),        # Thu
            bom_started_by=self.proposal, finalized_by=self.sales)
        sheet.refresh_from_db()
        rows = sheet.cycle_rows()
        self.assertEqual(len(rows), 3)         # BOM, Pickup, Finalise (no Finance)
        self.assertEqual(rows[0]['days'], 2)   # BOM: Sun→Tue = Mon,Tue
        self.assertEqual(rows[1]['days'], 0)   # Pickup: Tue→Tue same day
        self.assertEqual(rows[2]['days'], 2)   # Finalise: Tue→Thu = Wed,Thu
        self.assertEqual(rows[0]['person'], self.proposal)
        self.assertEqual(rows[2]['person'], self.sales)

    def test_list_shows_cycle_columns(self):
        self._sheet('costing_in_progress')
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse('costing:list'))
        self.assertContains(resp, 'BOM started')     # milestone-date header
        self.assertContains(resp, 'Duration')

    def test_projects_list_shows_cycle_columns(self):
        self._sheet('costing_in_progress')
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse('projects:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Duration')

    def test_milestone_rows_show_dates_and_person(self):
        sheet = self._sheet('finalized')
        CostingSheet.objects.filter(pk=sheet.pk).update(
            bom_started_at=self._aware(2026, 7, 5),
            handed_over_at=self._aware(2026, 7, 7),
            finalized_at=self._aware(2026, 7, 9),
            bom_started_by=self.proposal, finalized_by=self.sales)
        sheet.refresh_from_db()
        mr = sheet.milestone_rows()
        self.assertEqual(len(mr), 4)                 # BOM, Handed, Costing, Finalised
        self.assertIn('Jul 26', mr[0]['date'])       # a real date, not days
        self.assertEqual(mr[2]['date'], '—')         # costing_started never set
        self.assertEqual(mr[0]['person'], self.proposal)
        # Duration = working days created → finalised (weekends excluded).
        self.assertIsNotNone(sheet.total_cycle_days)
