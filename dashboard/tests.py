import tempfile
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from costing.models import CostingSheet, ExchangeRate
from projects.models import Project, ProjectStatus, Region


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StorageReportTests(TestCase):
    """The Storage admin page and the orphan-preview endpoint are super-admin
    only; preview redirects to an existing object and 404s otherwise."""

    def setUp(self):
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.super = User.objects.create_user('sa', password='x')
        self.super.role = sa
        self.super.save()
        self.plain = User.objects.create_user('plain', password='x')

    def test_report_page_super_admin_only(self):
        self.client.force_login(self.plain)
        self.assertEqual(
            self.client.get(reverse('dashboard:storage_report')).status_code, 403)
        self.client.force_login(self.super)
        self.assertEqual(
            self.client.get(reverse('dashboard:storage_report')).status_code, 200)

    def test_preview_requires_super_admin(self):
        self.client.force_login(self.plain)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': 'x.txt'})
        self.assertEqual(r.status_code, 403)

    def test_preview_missing_key_404(self):
        self.client.force_login(self.super)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': 'nope/missing.txt'})
        self.assertEqual(r.status_code, 404)

    def test_preview_existing_file_redirects(self):
        name = default_storage.save('orphan-preview.txt', ContentFile(b'hello'))
        self.client.force_login(self.super)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': name})
        self.assertEqual(r.status_code, 302)


class CostingValuedTileTests(TestCase):
    """Won and Hot Leads tiles report the ACTUAL costing-sheet contract total.

    The other tiles still sum estimated_value; only these two switch to the
    real priced number, resolved by the same rule the Commercial Pipeline list
    uses (costing sheet → actual_sales → estimate).
    """

    def setUp(self):
        self.lna = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
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

    def _project(self, ref, status, **kwargs):
        return Project.objects.create(project_name=ref, proposal_reference=ref,
                                      status=status, region=self.lna, **kwargs)

    def _stats(self, currency='SAR'):
        from dashboard.views import _resolve_sales_values, get_region_stats
        qs = Project.objects.all()
        sales_values, rates = _resolve_sales_values(qs)
        return get_region_stats(qs, ['LNA'], sales_values, currency, rates)

    def test_won_uses_costing_total_not_estimate(self):
        proj = self._project('W-1', self.won, estimated_value=Decimal('900000'))
        # scope_of_work_total feeds contract_total without building line items.
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('12500'))
        stats = self._stats()
        self.assertEqual(stats['won']['count'], 1)
        self.assertEqual(stats['won']['value'], Decimal('12500.00'))

    def test_won_falls_back_to_estimate_without_costing(self):
        self._project('W-2', self.won, estimated_value=Decimal('4000'))
        self.assertEqual(self._stats()['won']['value'], Decimal('4000.00'))

    def test_costing_total_converted_into_tile_currency(self):
        proj = self._project('W-3', self.won)
        # Sheet priced in GBP; an AED tile must convert: 800/0.80*3.67 = 3670.
        ExchangeRate.objects.update_or_create(
            currency_code='AED', defaults={'rate_to_usd': Decimal('3.67')})
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='GBP',
                                    scope_of_work_total=Decimal('800'))
        self.assertEqual(self._stats('AED')['won']['value'], Decimal('3670.00'))

    def test_hot_leads_uses_costing_total_not_estimate(self):
        proj = self._project('H-1', self.hot, estimated_value=Decimal('650000'))
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('8250'))
        stats = self._stats()
        self.assertEqual(stats['hot_leads']['count'], 1)
        self.assertEqual(stats['hot_leads']['value'], Decimal('8250.00'))

    def test_hot_leads_falls_back_to_estimate_without_costing(self):
        self._project('H-2', self.hot, estimated_value=Decimal('3200'))
        self.assertEqual(self._stats()['hot_leads']['value'], Decimal('3200.00'))

    def test_won_and_hot_leads_are_summed_separately(self):
        won_p = self._project('W-5', self.won)
        CostingSheet.objects.create(title='SW', project=won_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('1000'))
        hot_p = self._project('H-3', self.hot)
        CostingSheet.objects.create(title='SH', project=hot_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('2000'))
        stats = self._stats()
        self.assertEqual(stats['won']['value'], Decimal('1000.00'))
        self.assertEqual(stats['hot_leads']['value'], Decimal('2000.00'))

    def test_other_tiles_still_use_estimated_value(self):
        proj = self._project('A-1', self.active, estimated_value=Decimal('7000'))
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('99'))
        self.assertEqual(self._stats()['active']['value'], Decimal('7000'))

    def test_dashboard_page_renders_costing_values(self):
        won_p = self._project('W-4', self.won, estimated_value=Decimal('900000'))
        CostingSheet.objects.create(title='SW', project=won_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('12500'))
        hot_p = self._project('H-4', self.hot, estimated_value=Decimal('500000'))
        CostingSheet.objects.create(title='SH', project=hot_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('6400'))
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'SAR 12,500.00')
        self.assertContains(resp, 'SAR 6,400.00')
        # Won/Hot Leads show the costing total; the Total tile is untouched and
        # still reports the estimate, so assert on the tiles, not the whole page.
        lna = resp.context['lna']
        self.assertEqual(lna['won']['value'], Decimal('12500.00'))
        self.assertEqual(lna['hot_leads']['value'], Decimal('6400.00'))
        self.assertEqual(lna['total']['value'], Decimal('1400000'))
        # Charts must agree with the tiles they sit beside.
        self.assertEqual(resp.context['chart_data']['lna']['won_value'], 12500.0)
        self.assertEqual(resp.context['chart_data']['lna']['hot_leads_value'], 6400.0)
