import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, RolePermission, User
from accounts.permissions import seed_default_permissions
from projects.models import Region, ProjectStatus, Project, ProjectHistory
from costing.models import CostingSheet, CostingSection, CostingLineItem
from procurement.models import PurchaseOrder, PurchaseOrderItem, POSummaryEntry

from .models import KPIEntry
from .periods import period_bounds, label_for, current_period
from .registry import (
    KPI_DEFINITIONS, KPI_BY_KEY, evaluate, achievement_pct, kpis_for_department,
    SALES, PROPOSAL, PROCUREMENT,
)
from .services import build_dashboard, format_value


def _aware(y, m, d):
    return timezone.make_aware(datetime.datetime(y, m, d, 12, 0))


class PeriodTests(TestCase):
    def test_month_bounds(self):
        start, end = period_bounds('2026-06')
        self.assertEqual(start, datetime.date(2026, 6, 1))
        self.assertEqual(end, datetime.date(2026, 7, 1))

    def test_quarter_bounds(self):
        start, end = period_bounds('2026-Q2')
        self.assertEqual(start, datetime.date(2026, 4, 1))
        self.assertEqual(end, datetime.date(2026, 7, 1))

    def test_year_bounds(self):
        start, end = period_bounds('2026')
        self.assertEqual(start, datetime.date(2026, 1, 1))
        self.assertEqual(end, datetime.date(2027, 1, 1))

    def test_december_rolls_year(self):
        start, end = period_bounds('2026-12')
        self.assertEqual(end, datetime.date(2027, 1, 1))

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
        manual = [k for k in KPI_DEFINITIONS if not k.is_auto]
        self.assertEqual(len(auto), 11)
        self.assertEqual(len(manual), 10)

    def test_evaluate_higher(self):
        kpi = KPI_BY_KEY['sales_win_rate']  # higher is better, default 32
        self.assertEqual(evaluate(kpi, Decimal('40'), Decimal('32')), 'on')
        self.assertEqual(evaluate(kpi, Decimal('30'), Decimal('32')), 'near')   # >= 90%
        self.assertEqual(evaluate(kpi, Decimal('10'), Decimal('32')), 'off')
        self.assertEqual(evaluate(kpi, None, Decimal('32')), 'na')

    def test_evaluate_lower(self):
        kpi = KPI_BY_KEY['proc_pr_to_po_cycle']  # lower is better, target 5
        self.assertEqual(evaluate(kpi, Decimal('4'), Decimal('5')), 'on')
        self.assertEqual(evaluate(kpi, Decimal('5.4'), Decimal('5')), 'near')   # <= 110%
        self.assertEqual(evaluate(kpi, Decimal('9'), Decimal('5')), 'off')

    def test_achievement_pct_clamped(self):
        kpi = KPI_BY_KEY['sales_win_rate']
        self.assertEqual(achievement_pct(kpi, Decimal('1000'), Decimal('10')), 150)
        self.assertIsNone(achievement_pct(kpi, Decimal('5'), None))


class ComputeFixtureMixin:
    def setUp(self):
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.won = ProjectStatus.objects.create(name='Won', category='won')
        self.lost = ProjectStatus.objects.create(name='Lost', category='lost')
        self.active = ProjectStatus.objects.create(name='Open', category='active')

    def _project(self, ref, status, est='0', actual='0'):
        return Project.objects.create(
            project_name=ref, proposal_reference=ref, status=status,
            region=self.region, estimated_value=Decimal(est),
            actual_sales=Decimal(actual),
        )

    def _transition(self, project, status, when):
        h = ProjectHistory.objects.create(project=project, new_status=status)
        ProjectHistory.objects.filter(pk=h.pk).update(changed_at=when)
        return h


class ComputeTests(ComputeFixtureMixin, TestCase):
    def test_win_rate_from_history(self):
        p1 = self._project('W1', self.won)
        p2 = self._project('W2', self.won)
        p3 = self._project('L1', self.lost)
        self._transition(p1, self.won, _aware(2026, 5, 3))
        self._transition(p2, self.won, _aware(2026, 5, 10))
        self._transition(p3, self.lost, _aware(2026, 5, 20))
        # 2 won / 3 decided = 66.7%
        val = KPI_BY_KEY['sales_win_rate'].compute(*period_bounds('2026-Q2'), {})
        self.assertEqual(val, Decimal('66.7'))

    def test_win_rate_ignores_out_of_period(self):
        p = self._project('W1', self.won)
        self._transition(p, self.won, _aware(2025, 1, 1))  # prior year
        val = KPI_BY_KEY['sales_win_rate'].compute(*period_bounds('2026-Q2'), {})
        self.assertIsNone(val)

    def test_revenue_won(self):
        p1 = self._project('W1', self.won, actual='500000')
        p2 = self._project('W2', self.won, actual='300000')
        self._transition(p1, self.won, _aware(2026, 5, 3))
        self._transition(p2, self.won, _aware(2026, 5, 4))
        val = KPI_BY_KEY['sales_revenue_achievement'].compute(*period_bounds('2026-Q2'), {})
        self.assertEqual(val, Decimal('800000'))

    def test_pipeline_coverage_needs_goal(self):
        self._project('O1', self.active, est='900000')
        bounds = period_bounds('2026-Q2')
        kpi = KPI_BY_KEY['sales_pipeline_coverage']
        self.assertIsNone(kpi.compute(*bounds, {}))                       # no goal
        val = kpi.compute(*bounds, {'sales_revenue_achievement': Decimal('300000')})
        self.assertEqual(val, Decimal('3.00'))                           # 900k / 300k

    def test_cost_savings_and_ppv(self):
        project = self._project('P1', self.active)
        sheet = CostingSheet.objects.create(title='S1', project=project)
        section = CostingSection.objects.create(costing_sheet=sheet, title='Main')
        bom = CostingLineItem.objects.create(
            section=section, item_number='1', description='Camera',
            quantity=Decimal('10'), base_unit_cost=Decimal('100'),
            supplier_currency='SAR',
        )
        po = PurchaseOrder.objects.create(
            po_date=datetime.date(2026, 5, 15), po_number='PO-1',
            vendor_name='Acme', po_issued_by='Tester', project=project,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po, description='Camera', quantity=Decimal('10'),
            rate_per_unit=Decimal('80'), source_bom_item=bom,
        )
        bounds = period_bounds('2026-Q2')
        # planned 1000, actual 800 -> savings 200, ppv -20%
        self.assertEqual(KPI_BY_KEY['proc_cost_savings'].compute(*bounds, {}), Decimal('200.00'))
        self.assertEqual(KPI_BY_KEY['proc_ppv'].compute(*bounds, {}), Decimal('-20.0'))

    def test_ontime_delivery(self):
        po = PurchaseOrder.objects.create(
            po_date=datetime.date(2026, 5, 1), po_number='PO-2',
            vendor_name='Acme', po_issued_by='Tester',
        )
        it1 = PurchaseOrderItem.objects.create(
            purchase_order=po, description='A', quantity=Decimal('1'), rate_per_unit=Decimal('1'))
        it2 = PurchaseOrderItem.objects.create(
            purchase_order=po, description='B', quantity=Decimal('1'), rate_per_unit=Decimal('1'))
        POSummaryEntry.objects.create(
            purchase_order_item=it1, delivery_plan=datetime.date(2026, 5, 20),
            delivery_actual=datetime.date(2026, 5, 18))   # on time
        POSummaryEntry.objects.create(
            purchase_order_item=it2, delivery_plan=datetime.date(2026, 5, 10),
            delivery_actual=datetime.date(2026, 5, 25))   # late
        val = KPI_BY_KEY['proc_ontime_delivery'].compute(*period_bounds('2026-Q2'), {})
        self.assertEqual(val, Decimal('50.0'))


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
        self.assertEqual(card['status'], 'near')   # 88 vs 90, within 10%

    def test_format_value(self):
        self.assertEqual(format_value('percent', Decimal('32')), '32.0%')
        self.assertEqual(format_value('ratio', Decimal('3.2')), '3.20x')
        self.assertEqual(format_value('currency', Decimal('1234567')), 'SAR 1,234,567')
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

    def test_manager_sees_dashboard(self):
        self.client.force_login(self._user(Role.MANAGER))
        self.assertEqual(self.client.get(reverse('kpis:dashboard')).status_code, 200)

    def test_sales_rep_denied_dashboard(self):
        self.client.force_login(self._user(Role.SALES_REP))
        self.assertEqual(self.client.get(reverse('kpis:dashboard')).status_code, 403)

    def test_proposal_head_views_but_cannot_manage(self):
        self.client.force_login(self._user(Role.PROPOSAL_HEAD))
        self.assertEqual(self.client.get(reverse('kpis:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('kpis:manage')).status_code, 403)

    def test_admin_can_manage(self):
        self.client.force_login(self._user(Role.ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:manage')).status_code, 200)

    def test_seeded_caps(self):
        def allowed(role_name, code):
            role = Role.objects.get(name=role_name)
            return RolePermission.objects.get(role=role, codename=code).allowed
        self.assertTrue(allowed(Role.MANAGER, 'kpis.access'))
        self.assertTrue(allowed(Role.MANAGER, 'kpis.manage'))
        self.assertTrue(allowed(Role.PROPOSAL_HEAD, 'kpis.access'))
        self.assertFalse(allowed(Role.PROPOSAL_HEAD, 'kpis.manage'))
        self.assertFalse(allowed(Role.SALES_REP, 'kpis.access'))
        # AI roles stay siloed.
        self.assertFalse(allowed(Role.AI_HEAD, 'kpis.access'))


class ManagePostTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.admin = User.objects.create_user(
            username='admin1', password='pw', role=Role.objects.get(name=Role.ADMIN))
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
        win = KPIEntry.objects.get(period='2026-Q2', kpi_key='sales_win_rate')
        self.assertEqual(win.target, Decimal('35'))
        sup = KPIEntry.objects.get(period='2026-Q2', kpi_key='proc_supplier_performance')
        self.assertEqual(sup.manual_value, Decimal('92'))
        self.assertEqual(sup.note, 'strong quarter')
        self.assertEqual(sup.updated_by, self.admin)

    def test_auto_kpi_ignores_manual_value(self):
        self.client.post(reverse('kpis:manage') + '?period=2026-Q2', {
            'period': '2026-Q2',
            'value_sales_win_rate': '99',   # should be ignored (auto KPI)
            'target_sales_win_rate': '30',
        })
        win = KPIEntry.objects.get(period='2026-Q2', kpi_key='sales_win_rate')
        self.assertIsNone(win.manual_value)
        self.assertEqual(win.target, Decimal('30'))

    def test_empty_rows_not_created(self):
        self.client.post(reverse('kpis:manage') + '?period=2026-Q2', {'period': '2026-Q2'})
        self.assertEqual(KPIEntry.objects.count(), 0)
