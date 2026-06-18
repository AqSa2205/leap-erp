import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, RolePermission, User
from accounts.permissions import seed_default_permissions
from django.utils import timezone
from projects.models import Region, ProjectStatus, Project, ProjectHistory
from costing.models import CostingSheet, CostingSection, CostingLineItem
from procurement.models import PurchaseOrder, PurchaseOrderItem, POSummaryEntry

from .models import KPIEntry
from .periods import period_bounds, label_for
from .registry import (
    KPI_DEFINITIONS, KPI_BY_KEY, make_context, evaluate, achievement_pct,
    kpis_for_department, SALES, PROPOSAL, PROCUREMENT,
)
from .services import build_dashboard, build_person_scorecard, format_value


def _val(kpi_key, period, **ctx_kwargs):
    """Compute a KPI's value for a period (shortcut for tests)."""
    return KPI_BY_KEY[kpi_key].compute(make_context(period, **ctx_kwargs)).value


class PeriodTests(TestCase):
    def test_month_bounds(self):
        self.assertEqual(period_bounds('2026-06'),
                         (datetime.date(2026, 6, 1), datetime.date(2026, 7, 1)))

    def test_quarter_bounds(self):
        self.assertEqual(period_bounds('2026-Q2'),
                         (datetime.date(2026, 4, 1), datetime.date(2026, 7, 1)))

    def test_year_bounds(self):
        self.assertEqual(period_bounds('2026'),
                         (datetime.date(2026, 1, 1), datetime.date(2027, 1, 1)))

    def test_bad_period_raises(self):
        with self.assertRaises(ValueError):
            period_bounds('2026-Q9')

    def test_labels(self):
        self.assertEqual(label_for('2026-06'), 'June 2026')
        self.assertEqual(label_for('2026-Q2'), 'Q2 2026')
        self.assertEqual(label_for('2026'), 'FY 2026')


class RegistryTests(TestCase):
    def test_counts_per_department(self):
        self.assertEqual(len(kpis_for_department(SALES)), 5)
        self.assertEqual(len(kpis_for_department(PROPOSAL)), 6)
        self.assertEqual(len(kpis_for_department(PROCUREMENT)), 10)
        self.assertEqual(len(KPI_DEFINITIONS), 21)

    def test_eleven_auto_ten_manual(self):
        auto = [k for k in KPI_DEFINITIONS if k.is_auto]
        self.assertEqual(len(auto), 11)
        self.assertEqual(len(KPI_DEFINITIONS) - len(auto), 10)

    def test_evaluate_higher(self):
        kpi = KPI_BY_KEY['sales_win_rate']
        self.assertEqual(evaluate(kpi, Decimal('40'), Decimal('32')), 'on')
        self.assertEqual(evaluate(kpi, Decimal('30'), Decimal('32')), 'near')
        self.assertEqual(evaluate(kpi, Decimal('10'), Decimal('32')), 'off')
        self.assertEqual(evaluate(kpi, None, Decimal('32')), 'na')

    def test_evaluate_lower(self):
        kpi = KPI_BY_KEY['proc_pr_to_po_cycle']
        self.assertEqual(evaluate(kpi, Decimal('4'), Decimal('5')), 'on')
        self.assertEqual(evaluate(kpi, Decimal('5.4'), Decimal('5')), 'near')
        self.assertEqual(evaluate(kpi, Decimal('9'), Decimal('5')), 'off')

    def test_achievement_pct_clamped(self):
        kpi = KPI_BY_KEY['sales_win_rate']
        self.assertEqual(achievement_pct(kpi, Decimal('1000'), Decimal('10')), 150)
        self.assertIsNone(achievement_pct(kpi, Decimal('5'), None))


class ComputeFixtureMixin:
    def setUp(self):
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.region2 = Region.objects.create(name='UK', code='LNUK', currency='GBP')
        self.won = ProjectStatus.objects.create(name='Won', category='won')
        self.lost = ProjectStatus.objects.create(name='Lost', category='lost')
        self.active = ProjectStatus.objects.create(name='Open', category='active')

    def _project(self, ref, status, est='0', actual='0', year='2026',
                 quarter='Q2', region=None, owner=None):
        return Project.objects.create(
            project_name=ref, proposal_reference=ref, status=status,
            region=region or self.region, estimated_value=Decimal(est),
            actual_sales=Decimal(actual), year=year, po_award_quarter=quarter,
            owner=owner,
        )


class ComputeTests(ComputeFixtureMixin, TestCase):
    def test_win_rate_by_year_quarter(self):
        self._project('W1', self.won)
        self._project('W2', self.won)
        self._project('L1', self.lost)
        res = KPI_BY_KEY['sales_win_rate'].compute(make_context('2026-Q2'))
        self.assertEqual(res.value, Decimal('66.7'))      # 2 won / 3 decided
        self.assertIn('2 won of 3', res.coverage)

    def test_win_rate_excludes_other_quarter(self):
        self._project('W1', self.won, quarter='Q1')
        self.assertIsNone(_val('sales_win_rate', '2026-Q2'))

    def test_win_rate_by_region(self):
        self._project('A', self.won, region=self.region)
        self._project('B', self.lost, region=self.region2)
        self.assertEqual(_val('sales_win_rate', '2026-Q2', region=self.region), Decimal('100.0'))
        self.assertEqual(_val('sales_win_rate', '2026-Q2', region=self.region2), Decimal('0.0'))

    def test_untagged_deal_falls_back_to_history(self):
        # year blank -> not tagged; but a won transition in-window still counts.
        p = self._project('U1', self.won, year='', quarter='')
        h = ProjectHistory.objects.create(project=p, new_status=self.won)
        when = timezone.make_aware(datetime.datetime(2026, 5, 10, 12, 0))
        ProjectHistory.objects.filter(pk=h.pk).update(changed_at=when)
        self.assertEqual(_val('sales_win_rate', '2026-Q2'), Decimal('100.0'))
        # Out of window -> uncounted.
        self.assertIsNone(_val('sales_win_rate', '2026-Q1'))

    def test_revenue_won(self):
        self._project('W1', self.won, actual='500000')
        self._project('W2', self.won, actual='300000')
        self.assertEqual(_val('sales_revenue_achievement', '2026-Q2'), Decimal('800000'))

    def test_forecast_accuracy_ignores_deals_without_actuals(self):
        self._project('W1', self.won, est='100000', actual='110000')   # 90% accurate
        self._project('W2', self.won, est='100000', actual='0')        # excluded
        res = KPI_BY_KEY['sales_forecast_accuracy'].compute(make_context('2026-Q2'))
        self.assertEqual(res.value, Decimal('90.0'))
        self.assertIn('1 of 2', res.coverage)

    def test_forecast_accuracy_none_when_no_actuals(self):
        self._project('W1', self.won, est='100000', actual='0')
        self.assertIsNone(_val('sales_forecast_accuracy', '2026-Q2'))

    def test_pipeline_coverage(self):
        self._project('O1', self.active, est='900000')
        kpi = KPI_BY_KEY['sales_pipeline_coverage']
        self.assertIsNone(kpi.compute(make_context('2026-Q2', region=self.region)).value)
        res = kpi.compute(make_context('2026-Q2', region=self.region,
                                       targets={'sales_revenue_achievement': Decimal('300000')}))
        self.assertEqual(res.value, Decimal('3.00'))

    def test_cost_savings_and_ppv(self):
        project = self._project('P1', self.active)
        sheet = CostingSheet.objects.create(title='S1', project=project)
        section = CostingSection.objects.create(costing_sheet=sheet, title='Main')
        bom = CostingLineItem.objects.create(
            section=section, item_number='1', description='Camera',
            quantity=Decimal('10'), base_unit_cost=Decimal('100'), supplier_currency='SAR')
        po = PurchaseOrder.objects.create(
            po_date=datetime.date(2026, 5, 15), po_number='PO-1',
            vendor_name='Acme', po_issued_by='Tester', project=project)
        PurchaseOrderItem.objects.create(
            purchase_order=po, description='Camera', quantity=Decimal('10'),
            rate_per_unit=Decimal('80'), source_bom_item=bom)
        self.assertEqual(_val('proc_cost_savings', '2026-Q2'), Decimal('200.00'))
        self.assertEqual(_val('proc_ppv', '2026-Q2'), Decimal('-20.0'))

    def test_ontime_delivery(self):
        po = PurchaseOrder.objects.create(
            po_date=datetime.date(2026, 5, 1), po_number='PO-2',
            vendor_name='Acme', po_issued_by='Tester')
        it1 = PurchaseOrderItem.objects.create(
            purchase_order=po, description='A', quantity=Decimal('1'), rate_per_unit=Decimal('1'))
        it2 = PurchaseOrderItem.objects.create(
            purchase_order=po, description='B', quantity=Decimal('1'), rate_per_unit=Decimal('1'))
        POSummaryEntry.objects.create(
            purchase_order_item=it1, delivery_plan=datetime.date(2026, 5, 20),
            delivery_actual=datetime.date(2026, 5, 18))
        POSummaryEntry.objects.create(
            purchase_order_item=it2, delivery_plan=datetime.date(2026, 5, 10),
            delivery_actual=datetime.date(2026, 5, 25))
        self.assertEqual(_val('proc_ontime_delivery', '2026-Q2'), Decimal('50.0'))


class PerUserComputeTests(ComputeFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.role = Role.objects.create(name=Role.SALES_REP)
        self.alice = User.objects.create_user('alice', password='pw', role=self.role)
        self.bob = User.objects.create_user('bob', password='pw', role=self.role)

    def test_win_rate_per_user(self):
        self._project('A1', self.won, owner=self.alice)
        self._project('A2', self.lost, owner=self.alice)
        self._project('B1', self.won, owner=self.bob)
        self.assertEqual(_val('sales_win_rate', '2026-Q2', user=self.alice), Decimal('50.0'))
        self.assertEqual(_val('sales_win_rate', '2026-Q2', user=self.bob), Decimal('100.0'))
        self.assertEqual(_val('sales_win_rate', '2026-Q2'), Decimal('66.7'))

    def test_revenue_per_user(self):
        self._project('A1', self.won, owner=self.alice, actual='400000')
        self._project('B1', self.won, owner=self.bob, actual='100000')
        self.assertEqual(_val('sales_revenue_achievement', '2026-Q2', user=self.alice), Decimal('400000'))
        self.assertEqual(_val('sales_revenue_achievement', '2026-Q2', user=self.bob), Decimal('100000'))

    def test_pipeline_coverage_department_only(self):
        self.assertIsNone(_val('sales_pipeline_coverage', '2026-Q2', user=self.alice,
                               targets={'sales_revenue_achievement': Decimal('1')}))

    def test_scorecard_excludes_manual_and_dept_only(self):
        card = build_person_scorecard('2026-Q2', self.alice)
        keys = [c['key'] for d in card['departments'] for c in d['cards']]
        self.assertEqual(len(keys), 10)
        self.assertNotIn('proc_supplier_performance', keys)   # manual excluded
        self.assertNotIn('sales_pipeline_coverage', keys)     # dept-only excluded


class ServiceTests(ComputeFixtureMixin, TestCase):
    def test_build_dashboard_shape(self):
        data = build_dashboard('2026-Q2')
        self.assertEqual(len(data['departments']), 3)
        counts = {d['key']: len(d['cards']) for d in data['departments']}
        self.assertEqual(counts, {SALES: 5, PROPOSAL: 6, PROCUREMENT: 10})

    def test_manual_value_flows_into_card(self):
        KPIEntry.objects.create(
            period='2026-Q2', kpi_key='proc_supplier_performance',
            manual_value=Decimal('88'), target=Decimal('90'))
        data = build_dashboard('2026-Q2')
        proc = next(d for d in data['departments'] if d['key'] == PROCUREMENT)
        card = next(c for c in proc['cards'] if c['key'] == 'proc_supplier_performance')
        self.assertEqual(card['value'], Decimal('88'))
        self.assertEqual(card['status'], 'near')

    def test_coverage_present_on_auto_card(self):
        self._project('W1', self.won)
        data = build_dashboard('2026-Q2')
        sales = next(d for d in data['departments'] if d['key'] == SALES)
        wr = next(c for c in sales['cards'] if c['key'] == 'sales_win_rate')
        self.assertIn('won', wr['coverage'])

    def test_data_readiness_flags_gaps(self):
        from kpis.services import data_readiness
        self._project('W1', self.won, year='', actual='0')   # untagged + no actuals
        self._project('W2', self.won, year='2026', actual='500000')
        r = data_readiness()
        self.assertEqual(r['won_lost_total'], 2)
        self.assertEqual(r['untagged_year'], 1)
        self.assertEqual(r['won_missing_actuals'], 1)
        self.assertTrue(r['has_gaps'])

    def test_format_value_currency(self):
        self.assertEqual(format_value('percent', Decimal('32')), '32.0%')
        self.assertEqual(format_value('currency', Decimal('1234567'), 'SAR'), 'SAR 1,234,567')
        self.assertEqual(format_value('currency', Decimal('1000'), 'mixed'), '1,000 (mixed cur.)')
        self.assertEqual(format_value('currency', None), '—')


class PermissionTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _user(self, role_name):
        role = Role.objects.get(name=role_name)
        return User.objects.create_user(
            username=f'u_{role_name}', password='pw', role=role)

    def test_super_admin_sees_dashboard(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:dashboard')).status_code, 200)

    def test_admin_denied_dashboard(self):
        self.client.force_login(self._user(Role.ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:dashboard')).status_code, 403)

    def test_manager_denied_dashboard(self):
        self.client.force_login(self._user(Role.MANAGER))
        self.assertEqual(self.client.get(reverse('kpis:dashboard')).status_code, 403)

    def test_sales_rep_denied_dashboard(self):
        self.client.force_login(self._user(Role.SALES_REP))
        self.assertEqual(self.client.get(reverse('kpis:dashboard')).status_code, 403)

    def test_super_admin_can_manage(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:manage')).status_code, 200)

    def test_admin_denied_manage(self):
        self.client.force_login(self._user(Role.ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:manage')).status_code, 403)

    def test_super_admin_people_view(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:people')).status_code, 200)

    def test_people_view_denied_for_admin(self):
        self.client.force_login(self._user(Role.ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:people')).status_code, 403)

    def test_seeded_caps_super_admin_only(self):
        def allowed(role_name, code):
            role = Role.objects.get(name=role_name)
            return RolePermission.objects.get(role=role, codename=code).allowed
        self.assertTrue(allowed(Role.SUPER_ADMIN, 'kpis.access'))
        self.assertTrue(allowed(Role.SUPER_ADMIN, 'kpis.manage'))
        for r in (Role.ADMIN, Role.MANAGER, Role.PROPOSAL_HEAD, Role.PROCUREMENT_MGR,
                  Role.FINANCE_HEAD, Role.SALES_REP, Role.AI_HEAD):
            self.assertFalse(allowed(r, 'kpis.access'), msg=r)
            self.assertFalse(allowed(r, 'kpis.manage'), msg=r)


class ManagePostTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.admin = User.objects.create_user(
            username='admin1', password='pw', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.client.force_login(self.admin)

    def test_post_creates_entries(self):
        resp = self.client.post(reverse('kpis:manage') + '?period=2026-Q2', {
            'period': '2026-Q2',
            'target_sales_win_rate': '35',
            'value_proc_supplier_performance': '92',
            'target_proc_supplier_performance': '90',
            'note_proc_supplier_performance': 'strong quarter',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(KPIEntry.objects.get(period='2026-Q2', kpi_key='sales_win_rate').target, Decimal('35'))
        sup = KPIEntry.objects.get(period='2026-Q2', kpi_key='proc_supplier_performance')
        self.assertEqual(sup.manual_value, Decimal('92'))
        self.assertEqual(sup.note, 'strong quarter')
        self.assertEqual(sup.updated_by, self.admin)

    def test_auto_kpi_ignores_manual_value(self):
        self.client.post(reverse('kpis:manage') + '?period=2026-Q2', {
            'period': '2026-Q2',
            'value_sales_win_rate': '99',
            'target_sales_win_rate': '30',
        })
        win = KPIEntry.objects.get(period='2026-Q2', kpi_key='sales_win_rate')
        self.assertIsNone(win.manual_value)
        self.assertEqual(win.target, Decimal('30'))

    def test_empty_rows_not_created(self):
        self.client.post(reverse('kpis:manage') + '?period=2026-Q2', {'period': '2026-Q2'})
        self.assertEqual(KPIEntry.objects.count(), 0)
