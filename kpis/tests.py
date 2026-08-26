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
    kpis_for_department, SALES, PROPOSAL, PROCUREMENT, _outcome_projects,
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
        self.assertEqual(len(kpis_for_department(SALES)), 8)
        self.assertEqual(len(kpis_for_department(PROPOSAL)), 10)
        self.assertEqual(len(kpis_for_department(PROCUREMENT)), 10)
        self.assertEqual(len(KPI_DEFINITIONS), 29)

    def test_auto_manual_split(self):
        auto = [k for k in KPI_DEFINITIONS if k.is_auto]
        self.assertEqual(len(auto), 19)
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
        self.assertEqual(len(keys), 18)
        self.assertNotIn('proc_supplier_performance', keys)   # manual excluded
        self.assertNotIn('sales_pipeline_coverage', keys)     # dept-only excluded


class ServiceTests(ComputeFixtureMixin, TestCase):
    def test_build_dashboard_shape(self):
        data = build_dashboard('2026-Q2')
        self.assertEqual(len(data['departments']), 4)
        counts = {d['key']: len(d['cards']) for d in data['departments']}
        self.assertEqual(counts, {SALES: 8, PROPOSAL: 10, PROCUREMENT: 10, 'hr': 1})

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


class ActivityRegistryTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name=Role.SALES_REP)
        self.u1 = User.objects.create_user('act1', password='pw', role=self.role)
        self.u2 = User.objects.create_user('act2', password='pw', role=self.role)
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')

    def _make_project(self, ref, creator, when):
        from django.utils import timezone
        p = Project.objects.create(
            project_name=ref, proposal_reference=ref, status=self.status,
            region=self.region, created_by=creator)
        Project.objects.filter(pk=p.pk).update(
            created_at=timezone.make_aware(datetime.datetime(when.year, when.month, when.day, 12)))
        return p

    def test_projects_created_counts_by_user_and_period(self):
        from kpis.activity import ACTIVITY_METRICS
        metric = next(m for m in ACTIVITY_METRICS if m.key == 'projects_created')
        self._make_project('P1', self.u1, datetime.date(2026, 5, 1))
        self._make_project('P2', self.u1, datetime.date(2026, 5, 2))
        self._make_project('P3', self.u2, datetime.date(2026, 5, 3))
        self._make_project('P4', self.u1, datetime.date(2025, 1, 1))   # prior year
        counts = metric.counts(None, None)
        self.assertEqual(counts[self.u1.id], 3)
        self.assertEqual(counts[self.u2.id], 1)
        q2 = metric.counts(datetime.date(2026, 4, 1), datetime.date(2026, 7, 1))
        self.assertEqual(q2[self.u1.id], 2)
        self.assertEqual(q2.get(self.u2.id), 1)
        self.assertEqual(metric.count_for(None, None, self.u1.id), 3)

    def test_registry_has_21_metrics_and_headlines(self):
        from kpis.activity import ACTIVITY_METRICS, headline_metrics
        self.assertEqual(len(ACTIVITY_METRICS), 21)
        self.assertEqual({m.key for m in headline_metrics()}, {
            'projects_created', 'boms_created', 'sales_finalised',
            'handed_to_finance', 'pos_created', 'tech_proposals', 'tasks_completed'})

    def test_tasks_completed_excludes_incomplete_all_time(self):
        from devtracking.models import DevTask
        from kpis.activity import ACTIVITY_METRICS
        from django.utils import timezone
        # Assigned-but-not-completed: developer set, completed_at NULL.
        DevTask.objects.create(developer=self.u1, title='open', status='in_progress')
        # Completed: developer set + completed_at populated.
        DevTask.objects.create(
            developer=self.u1, title='done', status='done',
            completed_at=timezone.make_aware(datetime.datetime(2026, 5, 5, 12)))
        metric = next(m for m in ACTIVITY_METRICS if m.key == 'tasks_completed')
        # All-time must count ONLY the completed one (not the merely-assigned one).
        self.assertEqual(metric.counts(None, None).get(self.u1.id), 1)
        self.assertEqual(metric.count_for(None, None, self.u1.id), 1)


class ActivityPermissionSeedTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _allowed(self, role_name):
        role = Role.objects.get(name=role_name)
        return RolePermission.objects.get(role=role, codename='kpis.activity').allowed

    def test_activity_cap_super_admin_only(self):
        self.assertTrue(self._allowed(Role.SUPER_ADMIN))
        for r in (Role.ADMIN, Role.MANAGER, Role.SALES_REP, Role.AI_HEAD):
            self.assertFalse(self._allowed(r), msg=r)


class ActivityServiceTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name=Role.SALES_REP)
        self.active1 = User.objects.create_user('a1', password='pw', role=self.role)
        self.active2 = User.objects.create_user('a2', password='pw', role=self.role)
        self.inactive = User.objects.create_user('z', password='pw', role=self.role, is_active=False)
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        Project.objects.create(project_name='P1', proposal_reference='P1',
                               status=self.status, region=self.region, created_by=self.active1)
        Project.objects.create(project_name='P2', proposal_reference='P2',
                               status=self.status, region=self.region, created_by=self.active1)

    def test_overview_groups_by_region_then_department(self):
        from kpis.activity_service import build_activity_overview
        self.active1.region = self.region
        self.active1.save()
        self.active2.region = self.region
        self.active2.save()
        data = build_activity_overview('all')
        # Inactive users never appear.
        all_ids = {r['user'].id for reg in data['regions']
                   for d in reg['departments'] for r in d['rows']}
        self.assertNotIn(self.inactive.id, all_ids)
        # Sales reps land under the Sales department within their region.
        ksa = next(reg for reg in data['regions'] if reg['name'] == 'KSA')
        sales = next(d for d in ksa['departments'] if d['key'] == 'sales')
        self.assertEqual({r['user'].id for r in sales['rows']},
                         {self.active1.id, self.active2.id})
        # Sales shows only its own actions — no pipeline/BOM columns.
        col_keys = {c['key'] for c in sales['columns']}
        self.assertIn('sales_finalised', col_keys)
        self.assertNotIn('projects_created', col_keys)
        self.assertNotIn('boms_created', col_keys)

    def test_department_total_counts_only_own_metrics(self):
        from kpis.activity_service import build_activity_overview
        admin_role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        boss = User.objects.create_user('boss', password='pw', role=admin_role)
        Project.objects.create(project_name='P3', proposal_reference='P3',
                               status=self.status, region=self.region, created_by=boss)
        data = build_activity_overview('all')
        noreg = next(reg for reg in data['regions'] if reg['name'] == 'No region')
        mgmt = next(d for d in noreg['departments'] if d['key'] == 'management')
        boss_row = next(r for r in mgmt['rows'] if r['user'].id == boss.id)
        # Management owns projects_created → total reflects it.
        self.assertEqual(boss_row['total'], 1)

    def test_cross_department_work_credited_to_that_department(self):
        # A manager (Management) who STARTS COSTING should surface under Sales,
        # flagged cross-dept — bias-free crediting of the action, not the role.
        from kpis.activity_service import build_activity_overview
        mgr_role, _ = Role.objects.get_or_create(name=Role.MANAGER)
        mgr = User.objects.create_user('mgr', password='pw', role=mgr_role, region=self.region)
        proj = Project.objects.create(project_name='P5', proposal_reference='P5',
                                      status=self.status, region=self.region)
        CostingSheet.objects.create(
            title='S2', project=proj, workflow_stage='costing_in_progress',
            costing_started_at=timezone.now(), costing_started_by=mgr)
        data = build_activity_overview('all')
        ksa = next(reg for reg in data['regions'] if reg['name'] == 'KSA')
        sales = next(d for d in ksa['departments'] if d['key'] == 'sales')
        row = next(r for r in sales['rows'] if r['user'].id == mgr.id)
        self.assertFalse(row['is_home'])          # flagged as cross-department
        self.assertEqual(row['total'], 1)          # costing_started counted here
        # And they still appear in their own Management department.
        mgmt = next(d for d in ksa['departments'] if d['key'] == 'management')
        self.assertIn(mgr.id, {r['user'].id for r in mgmt['rows']})

    def test_finance_budgeting_cycle_time(self):
        from kpis.activity_service import build_activity_overview
        fin_role, _ = Role.objects.get_or_create(name=Role.FINANCE_HEAD)
        fin = User.objects.create_user('fin', password='pw', role=fin_role, region=self.region)
        proj = Project.objects.create(project_name='P4', proposal_reference='P4',
                                      status=self.status, region=self.region)
        review = timezone.make_aware(datetime.datetime(2026, 1, 1, 9, 0))
        approved = timezone.make_aware(datetime.datetime(2026, 1, 4, 9, 0))  # 3 days
        CostingSheet.objects.create(
            title='S', project=proj, workflow_stage='finance_approved',
            finance_review_at=review, finance_review_by=fin,
            finance_approved_at=approved, finance_approved_by=fin)
        data = build_activity_overview('all')
        ksa = next(reg for reg in data['regions'] if reg['name'] == 'KSA')
        finance = next(d for d in ksa['departments'] if d['key'] == 'finance')
        row = next(r for r in finance['rows'] if r['user'].id == fin.id)
        cyc = next(c for c in row['cells'] if c['kind'] == 'cycle')
        self.assertEqual(cyc['display'], '3.0d')

    def test_user_detail_grouped_by_module(self):
        from kpis.activity_service import build_user_activity
        data = build_user_activity('all', self.active1)
        self.assertEqual(data['total'], 2)
        modules = [m['module'] for m in data['modules']]
        self.assertEqual(modules, ['Pipeline', 'Costing', 'Procurement', 'Proposals', 'Dev Tracking'])
        pipeline = next(m for m in data['modules'] if m['module'] == 'Pipeline')
        created = next(i for i in pipeline['items'] if i['label'] == 'Pipelines created')
        self.assertEqual(created['count'], 2)

    def test_activity_window_all_time(self):
        from kpis.activity_service import activity_window
        self.assertEqual(activity_window('all'), (None, None))
        self.assertEqual(activity_window('2026-Q2'),
                         (datetime.date(2026, 4, 1), datetime.date(2026, 7, 1)))


class ActivityViewTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _user(self, role_name):
        return User.objects.create_user(
            username=f'av_{role_name}', password='pw',
            role=Role.objects.get(name=role_name))

    def test_super_admin_sees_overview(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:activity')).status_code, 200)

    def test_admin_denied_overview(self):
        self.client.force_login(self._user(Role.ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:activity')).status_code, 403)

    def test_super_admin_sees_detail(self):
        admin = self._user(Role.SUPER_ADMIN)
        self.client.force_login(admin)
        url = reverse('kpis:activity_detail', args=[admin.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_overview_renders_user_and_total(self):
        from projects.models import Region, ProjectStatus, Project
        admin = self._user(Role.SUPER_ADMIN)
        region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='active')
        Project.objects.create(project_name='P1', proposal_reference='P1',
                               status=status, region=region, created_by=admin)
        self.client.force_login(admin)
        resp = self.client.get(reverse('kpis:activity'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pipelines created')
        self.assertContains(resp, 'Total')



class KpiNewAccessTests(TestCase):
    """The sandbox tab renders the same build_dashboard() output the real
    dashboard does — company revenue, cost savings, on-time delivery — so it
    has to be gated the same way. It shipped without decorators, and because
    nothing covered the view, nothing caught it: `/kpis/new/` answered 200 to
    anonymous requests while `/kpis/` redirected to login.

    Each case is pinned against the equivalent on kpis:dashboard, so the two
    can't drift apart as the sandbox grows."""

    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _user(self, role_name):
        role = Role.objects.get(name=role_name)
        return User.objects.create_user(
            username=f'kn_{role_name}', password='pw', role=role)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse('kpis:kpi_new'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_sales_rep_denied(self):
        self.client.force_login(self._user(Role.SALES_REP))
        self.assertEqual(
            self.client.get(reverse('kpis:kpi_new')).status_code, 403)

    def test_manager_denied(self):
        self.client.force_login(self._user(Role.MANAGER))
        self.assertEqual(
            self.client.get(reverse('kpis:kpi_new')).status_code, 403)

    def test_super_admin_allowed(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN))
        self.assertEqual(
            self.client.get(reverse('kpis:kpi_new')).status_code, 200)

    def test_gated_identically_to_the_real_dashboard(self):
        """Whatever the answer is for kpis:dashboard, it must be the same
        here — this is the invariant worth keeping as the sandbox grows."""
        for role_name in (Role.SUPER_ADMIN, Role.ADMIN, Role.MANAGER, Role.SALES_REP):
            with self.subTest(role=role_name):
                client = self.client_class()
                client.force_login(self._user(role_name))
                self.assertEqual(
                    client.get(reverse('kpis:kpi_new')).status_code,
                    client.get(reverse('kpis:dashboard')).status_code)

    def test_internal_notes_do_not_reach_the_page_source(self):
        """The shell's ownership notes are a Django comment, not an HTML one,
        so they must not appear in the rendered output."""
        self.client.force_login(self._user(Role.SUPER_ADMIN))
        body = self.client.get(reverse('kpis:kpi_new')).content.decode()
        self.assertNotIn('owner: this dev', body)
        self.assertNotIn('Working sandbox tab', body)


class MonthBucketingTests(ComputeFixtureMixin, TestCase):
    """A month period must mean that month.

    `po_award_quarter` cannot express a month, so `_period_year_quarter()`
    widens June to Q2. Bucketing a month on the tags therefore returned April,
    May and June under a tile labelled "June" -- and worse, the old
    tag-primary/history-fallback pair applied *both* rules inside one number:
    a quarter-tagged deal counted in all three months while an untagged one
    counted only in its own, with nothing on the tile revealing which deal got
    which treatment.

    Month periods now bucket purely on the transition date. Year and quarter
    periods keep the tag basis, which is what those fields are for."""

    def _won_on(self, ref, when, year='2026', quarter='Q2'):
        """A won deal tagged for `quarter`, whose transition happened on `when`."""
        p = self._project(ref, self.won, est='100000', year=year, quarter=quarter)
        h = ProjectHistory.objects.create(project=p, new_status=self.won)
        ProjectHistory.objects.filter(pk=h.pk).update(
            changed_at=timezone.make_aware(when))
        return p

    def test_month_counts_only_that_month(self):
        self._won_on('APR', datetime.datetime(2026, 4, 10, 9, 0))
        self._won_on('MAY', datetime.datetime(2026, 5, 10, 9, 0))
        self._won_on('JUN', datetime.datetime(2026, 6, 10, 9, 0))
        # All three are tagged Q2. Before the fix, each month returned all three.
        for period, expected in (('2026-04', 'APR'), ('2026-05', 'MAY'),
                                 ('2026-06', 'JUN')):
            with self.subTest(period=period):
                qs = _outcome_projects(make_context(period))
                self.assertEqual([p.project_name for p in qs], [expected])

    def test_quarter_still_uses_the_tags(self):
        """The tag basis is deliberate at quarter granularity -- a deal tagged
        Q2 belongs to Q2 whether or not a transition was ever logged."""
        self._project('TAGGED', self.won, est='100000', quarter='Q2')
        qs = _outcome_projects(make_context('2026-Q2'))
        self.assertEqual([p.project_name for p in qs], ['TAGGED'])

    def test_month_ignores_a_tag_with_no_transition(self):
        """A deal tagged Q2 but with no recorded transition cannot be placed in
        a month -- there is no date to place it by. It must not silently land
        in every month of the quarter."""
        self._project('TAGGED_ONLY', self.won, est='100000', quarter='Q2')
        for period in ('2026-04', '2026-05', '2026-06'):
            with self.subTest(period=period):
                self.assertEqual(list(_outcome_projects(make_context(period))), [])
        # Still counted at quarter granularity, where the tag is the basis.
        self.assertEqual(len(_outcome_projects(make_context('2026-Q2'))), 1)

    def test_one_rule_per_period_tagged_and_untagged_alike(self):
        """The mixed-basis bug: a tagged and an untagged deal, both won in May,
        must both appear in May and neither in April."""
        self._won_on('TAGGED', datetime.datetime(2026, 5, 5, 9, 0), quarter='Q2')
        self._won_on('UNTAGGED', datetime.datetime(2026, 5, 6, 9, 0),
                     year='', quarter='')
        may = sorted(p.project_name for p in _outcome_projects(make_context('2026-05')))
        self.assertEqual(may, ['TAGGED', 'UNTAGGED'])
        self.assertEqual(list(_outcome_projects(make_context('2026-04'))), [])


class ValueLadderTests(ComputeFixtureMixin, TestCase):
    """Revenue and pipeline value must resolve a deal the same way the home
    dashboard's Won tile does -- costing sheet, then actual_sales, then
    estimated_value.

    The KPI used to sum `actual_sales` alone while the tile resolved through
    costing, so the two pages reported different revenue for the same deals and
    the KPI ignored every costing sheet on record."""

    def _priced_sheet(self, project, unit_cost, currency='SAR'):
        """A sheet whose contract_total is non-zero, via one line item."""
        sheet = CostingSheet.objects.create(
            title=f'S-{project.project_name}', project=project,
            margin=Decimal('0'), workflow_stage='finalized',
            output_currency=currency)
        section = CostingSection.objects.create(
            costing_sheet=sheet, section_number='1', title='Works', order=0)
        CostingLineItem.objects.create(
            section=section, item_number='1', description='Item',
            quantity=Decimal('1'), base_unit_cost=Decimal(unit_cost),
            supplier_currency='SAR', margin=Decimal('0'))
        return sheet

    def test_costing_outranks_actual_sales(self):
        """The rule chosen for the whole ERP: a live costing sheet wins."""
        p = self._project('W1', self.won, actual='500000')
        self._priced_sheet(p, '900000')
        res = KPI_BY_KEY['sales_revenue_achievement'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('900000.00'))

    def test_falls_back_to_actual_then_estimate(self):
        self._project('A', self.won, actual='500000', est='111')
        self._project('B', self.won, actual='0', est='250000')
        res = KPI_BY_KEY['sales_revenue_achievement'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('750000'))

    def test_agrees_with_the_home_dashboard_resolver(self):
        """The invariant worth keeping: whatever the tile resolves per deal is
        what the KPI totals. Pinned against the resolver itself, so the two
        cannot drift apart again."""
        from projects.views import _resolve_project_sales_value
        p1 = self._project('A', self.won, actual='500000')
        self._priced_sheet(p1, '900000')
        p2 = self._project('B', self.won, est='250000')
        expected = sum(
            (_resolve_project_sales_value(p, list(p.costing_sheets.all()))['amount']
             or Decimal('0')) for p in (p1, p2))
        res = KPI_BY_KEY['sales_revenue_achievement'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, expected)

    def test_coverage_reports_deals_with_no_value(self):
        self._project('A', self.won, actual='500000')
        self._project('B', self.won, actual='0', est='0')
        res = KPI_BY_KEY['sales_revenue_achievement'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('500000'))
        self.assertIn('1 with no value recorded', res.coverage)

    def test_coverage_flags_a_sheet_in_another_currency(self):
        """Region-scoped totals assume every sheet is raised in the region's
        currency. If one is not, the tile says so instead of adding GBP to SAR
        in silence."""
        p = self._project('W1', self.won)
        self._priced_sheet(p, '1000', currency='GBP')
        res = KPI_BY_KEY['sales_revenue_achievement'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertIn('mixed currency', res.coverage)
        self.assertIn('GBP', res.coverage)

    def test_pipeline_coverage_counts_a_costed_open_deal_at_its_costing(self):
        """Open pipeline read `estimated_value`, so a fully costed deal counted
        at the estimate it was raised with -- the pipeline understated itself
        precisely on the deals it knew most about."""
        p = self._project('OPEN', self.active, est='100000')
        self._priced_sheet(p, '400000')
        res = KPI_BY_KEY['sales_pipeline_coverage'].compute(
            make_context('2026-Q2', region=self.region,
                         targets={'sales_revenue_achievement': Decimal('200000')}))
        self.assertEqual(res.value, Decimal('2.00'))   # 400k costed / 200k goal
        self.assertIn('1 costed', res.coverage)


class NewOpportunityTests(ComputeFixtureMixin, TestCase):
    """Deals created during the period.

    `created_at` is set automatically on every row, so unlike the outcome KPIs
    this needs no tagging discipline and no status history. That makes it the
    one Sales metric whose answer does not depend on how well the pipeline is
    maintained -- which is why it is the first one built."""

    def _created_on(self, ref, when, est='100000', region=None, owner=None):
        p = self._project(ref, self.active, est=est, region=region, owner=owner)
        Project.objects.filter(pk=p.pk).update(
            created_at=timezone.make_aware(when))
        return p

    def test_counts_only_deals_created_in_the_window(self):
        self._created_on('MAY', datetime.datetime(2026, 5, 20, 9, 0))
        self._created_on('JUN', datetime.datetime(2026, 6, 2, 9, 0))
        self.assertEqual(_val('sales_new_opportunities', '2026-05'), Decimal('1'))
        self.assertEqual(_val('sales_new_opportunities', '2026-06'), Decimal('1'))
        self.assertEqual(_val('sales_new_opportunities', '2026-Q2'), Decimal('2'))

    def test_value_rides_in_the_coverage_line(self):
        """The count is the headline; ten small enquiries and one large tender
        are not the same month, so both numbers have to be visible."""
        self._created_on('A', datetime.datetime(2026, 5, 1, 9, 0), est='400000')
        self._created_on('B', datetime.datetime(2026, 5, 2, 9, 0), est='600000')
        res = KPI_BY_KEY['sales_new_opportunities'].compute(
            make_context('2026-05', region=self.region))
        self.assertEqual(res.value, Decimal('2'))
        self.assertIn('1,000,000', res.coverage)
        self.assertIn('SAR', res.coverage)

    def test_flags_deals_carrying_no_value(self):
        self._created_on('A', datetime.datetime(2026, 5, 1, 9, 0), est='400000')
        self._created_on('B', datetime.datetime(2026, 5, 2, 9, 0), est='0')
        res = KPI_BY_KEY['sales_new_opportunities'].compute(
            make_context('2026-05', region=self.region))
        self.assertIn('1 with no value', res.coverage)

    def test_empty_period_says_so_rather_than_looking_broken(self):
        res = KPI_BY_KEY['sales_new_opportunities'].compute(make_context('2026-05'))
        self.assertEqual(res.value, Decimal('0'))
        self.assertIn('nothing added', res.coverage)

    def test_scoped_by_region_and_owner(self):
        other = User.objects.create_user(username='rep_no', password='pw')
        mine = User.objects.create_user(username='rep_me', password='pw')
        self._created_on('MINE', datetime.datetime(2026, 5, 1, 9, 0), owner=mine)
        self._created_on('THEIRS', datetime.datetime(2026, 5, 2, 9, 0), owner=other)
        self._created_on('OTHERREGION', datetime.datetime(2026, 5, 3, 9, 0),
                         region=self.region2)
        self.assertEqual(
            _val('sales_new_opportunities', '2026-05', region=self.region),
            Decimal('2'))
        self.assertEqual(
            _val('sales_new_opportunities', '2026-05', user=mine), Decimal('1'))


class LostOpportunityTests(ComputeFixtureMixin, TestCase):
    """Deals lost during the period, with the leading reason."""

    def _lost(self, ref, est, reason):
        p = self._project(ref, self.lost, est=est)
        Project.objects.filter(pk=p.pk).update(lost_reason=reason)
        return p

    def test_counts_lost_and_reports_the_leading_reason(self):
        self._lost('L1', '100000', 'price')
        self._lost('L2', '200000', 'price')
        self._lost('L3', '50000', 'competitor')
        res = KPI_BY_KEY['sales_lost_opportunities'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('3'))
        self.assertIn('350,000', res.coverage)
        self.assertIn('mostly', res.coverage)
        self.assertIn('too expensive', res.coverage)

    def test_no_bid_still_counts_as_leaving_the_pipeline(self):
        """A tender we declined did leave the pipeline, so it belongs in this
        count. The distinction matters for WIN RATE -- a measure of competitive
        performance that should not move when workload changes -- not here."""
        self._lost('NB', '100000', 'no_bid')
        res = KPI_BY_KEY['sales_lost_opportunities'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('1'))
        self.assertIn('did not bid', res.coverage.lower())

    def test_says_when_no_reasons_were_recorded(self):
        self._project('L1', self.lost, est='100000')
        res = KPI_BY_KEY['sales_lost_opportunities'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertIn('no reasons recorded', res.coverage)

    def test_direction_is_lower_is_better(self):
        self.assertEqual(KPI_BY_KEY['sales_lost_opportunities'].direction, 'lower')

    def test_empty_period(self):
        res = KPI_BY_KEY['sales_lost_opportunities'].compute(make_context('2026-Q2'))
        self.assertEqual(res.value, Decimal('0'))
        self.assertIn('none lost', res.coverage)


class PipelineValueTests(ComputeFixtureMixin, TestCase):
    """Total value of everything still open -- the companion to pipeline
    coverage. Same deals, same ladder, so the ratio and the amount can never
    tell different stories."""

    def test_sums_open_deals_only(self):
        self._project('OPEN1', self.active, est='300000')
        self._project('OPEN2', self.active, est='200000')
        self._project('WON', self.won, est='999999')
        self._project('LOST', self.lost, est='888888')
        res = KPI_BY_KEY['sales_pipeline_value'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('500000'))
        self.assertIn('2 open deals', res.coverage)

    def test_is_a_snapshot_not_a_period_total(self):
        """Pipeline is what is open right now. Changing the period must not
        change it -- a deal does not leave the pipeline because the month did."""
        self._project('OPEN', self.active, est='300000')
        first = _val('sales_pipeline_value', '2026-05', region=self.region)
        for period in ('2026-06', '2026-Q1', '2026-Q2', '2026'):
            with self.subTest(period=period):
                self.assertEqual(
                    _val('sales_pipeline_value', period, region=self.region), first)

    def test_agrees_with_pipeline_coverage(self):
        """value / goal must equal the coverage ratio, or the two tiles are
        telling different stories about the same deals."""
        self._project('OPEN1', self.active, est='300000')
        self._project('OPEN2', self.active, est='300000')
        goal = Decimal('200000')
        value = _val('sales_pipeline_value', '2026-Q2', region=self.region)
        ratio = _val('sales_pipeline_coverage', '2026-Q2', region=self.region,
                     targets={'sales_revenue_achievement': goal})
        self.assertEqual((value / goal).quantize(Decimal('0.01')), ratio)

    def test_empty_pipeline(self):
        res = KPI_BY_KEY['sales_pipeline_value'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('0'))
        self.assertIn('no open deals', res.coverage)


class SalesSectionRenderTests(ComputeFixtureMixin, TestCase):
    """The Sales partial renders real figures on the page.

    Worth having as a view test rather than only compute tests: a template
    typo raises at render time, not import time, so a broken partial 500s the
    whole dashboard and no amount of registry testing notices."""

    def setUp(self):
        super().setUp()
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.user = User.objects.create_user(
            username='gm', password='pw', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.client.force_login(self.user)

    def _get(self, **params):
        url = reverse('kpis:kpi_new')
        if params:
            url += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(url)

    def test_page_renders_with_the_sales_section(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Sales &amp; Pipeline', body)
        self.assertIn('Sales Performance', body)
        self.assertIn('Pipeline Overview', body)

    def test_every_sales_tile_is_present(self):
        body = self._get().content.decode()
        for label in ('Revenue secured', 'YTD revenue', 'Revenue forecast',
                      'Total pipeline value', 'Pipeline coverage',
                      'New opportunities', 'Lost opportunities'):
            with self.subTest(tile=label):
                self.assertIn(label, body)

    def test_figures_reach_the_page(self):
        """Not just that the tiles exist -- that a real number lands in one."""
        self._project('OPEN1', self.active, est='750000')
        body = self._get(region=self.region.code).content.decode()
        self.assertIn('750,000', body)
        self.assertIn('1 open deal', body)

    def test_ytd_is_computed_against_the_year_not_the_selected_period(self):
        """A deal won in Q1 must not appear in a Q2 revenue tile, but must
        still be inside YTD -- which is the whole reason YTD is computed
        separately from the dashboard's single period."""
        self._project('Q1WIN', self.won, est='400000', quarter='Q1')
        resp = self._get(period='2026-Q2', region=self.region.code)
        self.assertEqual(resp.status_code, 200)
        ytd = resp.context['ytd']
        self.assertEqual(ytd['value'], Decimal('400000'))
        self.assertEqual(ytd['period_label'], 'FY 2026')
        # ...while the selected-period tile for Q2 saw nothing.
        q2 = resp.context['cards']['sales']['sales_revenue_achievement']
        self.assertEqual(q2['value'], Decimal('0'))

    def test_cards_lookup_covers_every_sales_kpi(self):
        """The partial addresses cards by key. A key that stops existing would
        silently render an empty tile, so pin the lookup itself."""
        resp = self._get()
        sales = resp.context['cards']['sales']
        for key in ('sales_revenue_achievement', 'sales_pipeline_value',
                    'sales_pipeline_coverage', 'sales_new_opportunities',
                    'sales_lost_opportunities'):
            with self.subTest(key=key):
                self.assertIn(key, sales)

    def test_malformed_period_does_not_break_the_ytd_figure(self):
        """_resolve_period falls back to the current quarter on garbage, so the
        page must still render rather than propagating the bad string."""
        resp = self._get(period='not-a-period')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context['ytd'])


class RFQActivityTests(ComputeFixtureMixin, TestCase):
    """RFQ Activity: received, BOMs sent to sales, overdue, pending.

    "Submitted" is the INTERNAL handoff -- CostingSheet.handed_over_at, stamped
    when the proposal team marks a BOM ready for sales -- measured against
    Project.handed_over_deadline. It is NOT the client-facing technical
    proposal, which is what an earlier version of these computes wrongly read.
    Overdue therefore means the BOM has not reached sales yet; pending means
    the RFQ is still at the BOM stage.

    Every pipeline project counts as an RFQ. The accepted trade-off is that a
    project with no handed_over_deadline can never be overdue; the tile
    reports how many it cannot see rather than quietly dropping them."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    def _rfq(self, ref, deadline_offset=None, status=None, created=None,
             owner=None, region=None):
        """An RFQ. `deadline_offset` = days from today for the CLIENT
        submission deadline; None = no deadline set. The BOM is due with sales
        two WORKING days before that, so an offset of +1 or +2 can already be
        overdue depending on the weekday."""
        p = self._project(ref, status or self.active, est='100000',
                          owner=owner, region=region)
        fields = {}
        if deadline_offset is not None:
            fields['submission_deadline'] = (
                self.today + datetime.timedelta(days=deadline_offset))
        if created is not None:
            fields['created_at'] = timezone.make_aware(created)
        if fields:
            Project.objects.filter(pk=p.pk).update(**fields)
        return p

    def _hand_over(self, project, days_ago=1, title=None):
        """Mark a BOM as sent to sales `days_ago` days back."""
        sheet = CostingSheet.objects.create(
            title=title or f'S-{project.project_name}', project=project)
        CostingSheet.objects.filter(pk=sheet.pk).update(
            handed_over_at=timezone.make_aware(datetime.datetime.combine(
                self.today - datetime.timedelta(days=days_ago),
                datetime.time(9, 0))))
        return sheet

    # ── received ────────────────────────────────────────────────────────────

    def test_received_counts_projects_created_in_the_window(self):
        self._rfq('MAY', created=datetime.datetime(2026, 5, 10, 9, 0))
        self._rfq('JUN', created=datetime.datetime(2026, 6, 10, 9, 0))
        self.assertEqual(_val('proposal_rfqs_received', '2026-05'), Decimal('1'))
        self.assertEqual(_val('proposal_rfqs_received', '2026-Q2'), Decimal('2'))

    def test_received_flags_rfqs_with_no_submission_deadline(self):
        self._rfq('A', deadline_offset=10, created=datetime.datetime(2026, 5, 1, 9, 0))
        self._rfq('B', created=datetime.datetime(2026, 5, 2, 9, 0))
        res = KPI_BY_KEY['proposal_rfqs_received'].compute(
            make_context('2026-05', region=self.region))
        self.assertEqual(res.value, Decimal('2'))
        self.assertIn('1 with no submission deadline set', res.coverage)

    def test_received_matches_the_sales_new_opportunities_tile(self):
        """Same underlying event seen by two audiences. They must agree -- if
        they ever diverge, one has quietly changed its definition."""
        for i in range(3):
            self._rfq(f'R{i}', created=datetime.datetime(2026, 5, i + 1, 9, 0))
        self.assertEqual(
            _val('proposal_rfqs_received', '2026-05', region=self.region),
            _val('sales_new_opportunities', '2026-05', region=self.region))

    # ── BOMs sent to sales ──────────────────────────────────────────────────

    def test_counts_boms_handed_over_in_the_window(self):
        p1 = self._rfq('SENT', deadline_offset=5)
        self._hand_over(p1, days_ago=1)
        self._rfq('NOT_SENT', deadline_offset=5)
        period = f'{self.today.year}-{self.today.month:02d}'
        res = KPI_BY_KEY['proposal_rfqs_submitted'].compute(
            make_context(period, region=self.region))
        self.assertEqual(res.value, Decimal('1'))
        self.assertIn('sent to sales', res.coverage)

    def test_a_revised_sheet_does_not_count_the_rfq_twice(self):
        """Counted once per project on the EARLIEST handover -- the RFQ left
        the proposal desk the first time a BOM went across."""
        p = self._rfq('MULTI', deadline_offset=5)
        self._hand_over(p, days_ago=3, title='rev A')
        self._hand_over(p, days_ago=1, title='rev B')
        period = f'{self.today.year}-{self.today.month:02d}'
        self.assertEqual(
            _val('proposal_rfqs_submitted', period, region=self.region), Decimal('1'))

    def test_a_sheet_never_handed_over_does_not_count(self):
        p = self._rfq('BOM_ONLY', deadline_offset=5)
        CostingSheet.objects.create(title='draft bom', project=p)
        period = f'{self.today.year}-{self.today.month:02d}'
        res = KPI_BY_KEY['proposal_rfqs_submitted'].compute(
            make_context(period, region=self.region))
        self.assertEqual(res.value, Decimal('0'))
        self.assertIn('no BOMs sent to sales', res.coverage)

    # ── overdue ─────────────────────────────────────────────────────────────

    def test_overdue_means_the_bom_has_not_reached_sales(self):
        self._rfq('LATE', deadline_offset=-5)
        self._rfq('FUTURE', deadline_offset=5)
        res = KPI_BY_KEY['proposal_rfqs_overdue'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('1'))
        self.assertIn('past due to sales', res.coverage)

    def test_a_handed_over_bom_is_not_overdue_however_late_the_deadline(self):
        """Once the BOM has gone across, the RFQ has left the proposal desk --
        it must not resurface as overdue afterwards."""
        p = self._rfq('SENT_LATE', deadline_offset=-30)
        self._hand_over(p, days_ago=2)
        self.assertEqual(
            _val('proposal_rfqs_overdue', '2026-Q2', region=self.region), Decimal('0'))

    def test_won_and_lost_rfqs_drop_out(self):
        """A decided deal is no longer waiting on a handoff."""
        self._rfq('WON_LATE', deadline_offset=-10, status=self.won)
        self._rfq('LOST_LATE', deadline_offset=-10, status=self.lost)
        self.assertEqual(
            _val('proposal_rfqs_overdue', '2026-Q2', region=self.region), Decimal('0'))

    def test_overdue_reports_what_it_cannot_see(self):
        """The accepted blind spot: no handover deadline means nothing to be
        late against. It must be visible, not silently dropped."""
        self._rfq('LATE', deadline_offset=-5)
        self._rfq('NO_DEADLINE')
        res = KPI_BY_KEY['proposal_rfqs_overdue'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('1'))
        self.assertIn('no submission deadline', res.coverage)

    def test_overdue_is_a_snapshot_not_a_period_total(self):
        self._rfq('LATE', deadline_offset=-5)
        first = _val('proposal_rfqs_overdue', '2026-05', region=self.region)
        for period in ('2026-06', '2026-Q1', '2026-Q2', '2026'):
            with self.subTest(period=period):
                self.assertEqual(
                    _val('proposal_rfqs_overdue', period, region=self.region), first)

    # ── pending ─────────────────────────────────────────────────────────────

    def test_pending_means_still_at_bom_stage(self):
        self._rfq('SOON', deadline_offset=8)           # BOM due in a few days
        self._rfq('LATER', deadline_offset=60)
        self._rfq('LATE', deadline_offset=-5)          # overdue, not pending
        res = KPI_BY_KEY['proposal_rfqs_pending'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('2'))
        self.assertIn('still at BOM stage', res.coverage)

    def test_a_project_with_no_costing_sheet_is_pending(self):
        """No sheet means no BOM started, let alone handed over."""
        self._rfq('NO_SHEET', deadline_offset=30)
        self.assertEqual(
            _val('proposal_rfqs_pending', '2026-Q2', region=self.region), Decimal('1'))

    def test_an_rfq_with_no_deadline_is_pending_not_overdue(self):
        self._rfq('NO_DEADLINE')
        self.assertEqual(
            _val('proposal_rfqs_pending', '2026-Q2', region=self.region), Decimal('1'))
        self.assertEqual(
            _val('proposal_rfqs_overdue', '2026-Q2', region=self.region), Decimal('0'))

    def test_overdue_and_pending_reconcile(self):
        """The invariant that makes the two tiles trustworthy together: they
        are mutually exclusive and between them cover every open RFQ whose BOM
        has not gone to sales."""
        self._rfq('LATE1', deadline_offset=-9)
        self._rfq('LATE2', deadline_offset=-1)
        self._rfq('SOON', deadline_offset=20)
        self._rfq('NO_DEADLINE')
        sent = self._rfq('SENT', deadline_offset=-3)
        self._hand_over(sent)

        overdue = _val('proposal_rfqs_overdue', '2026-Q2', region=self.region)
        pending = _val('proposal_rfqs_pending', '2026-Q2', region=self.region)
        not_sent = Project.objects.filter(
            status__category__in=['active', 'hot_lead'], region=self.region
        ).exclude(pk=sent.pk).count()
        self.assertEqual(overdue + pending, Decimal(not_sent))

    def test_scoped_by_region(self):
        self._rfq('MINE', deadline_offset=-5, region=self.region)
        self._rfq('THEIRS', deadline_offset=-5, region=self.region2)
        self.assertEqual(
            _val('proposal_rfqs_overdue', '2026-Q2', region=self.region), Decimal('1'))
        self.assertEqual(
            _val('proposal_rfqs_overdue', '2026-Q2', region=self.region2), Decimal('1'))


class ProposalSectionRenderTests(ComputeFixtureMixin, TestCase):
    """The RFQ / Proposal partial renders. A template typo raises at render
    time, so a broken partial 500s the whole dashboard and no amount of
    registry testing notices."""

    def setUp(self):
        super().setUp()
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.user = User.objects.create_user(
            username='gm_prop', password='pw',
            role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.client.force_login(self.user)

    def test_every_rfq_tile_is_present(self):
        resp = self.client.get(reverse('kpis:kpi_new'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        for label in ('RFQ / Proposal Activity', 'RFQs received',
                      'BOMs sent to sales', 'RFQs overdue', 'RFQs pending'):
            with self.subTest(tile=label):
                self.assertIn(label, body)

    def test_cards_lookup_covers_every_rfq_kpi(self):
        resp = self.client.get(reverse('kpis:kpi_new'))
        proposal = resp.context['cards']['proposal']
        for key in ('proposal_rfqs_received', 'proposal_rfqs_submitted',
                    'proposal_rfqs_overdue', 'proposal_rfqs_pending'):
            with self.subTest(key=key):
                self.assertIn(key, proposal)

    def test_a_real_figure_reaches_the_page(self):
        p = self._project('LATE', self.active, est='100000')
        Project.objects.filter(pk=p.pk).update(
            submission_deadline=timezone.localdate() - datetime.timedelta(days=3))
        body = self.client.get(
            reverse('kpis:kpi_new') + f'?region={self.region.code}').content.decode()
        self.assertIn('past due to sales', body)


class BomDueDateTests(TestCase):
    """The BOM is due with sales two WORKING days before the client submission
    deadline. Sales cannot cost and submit in no time, so a BOM arriving
    inside that window is already late even though the client deadline has not
    passed.

    Working days, not calendar days, and on the KSA Fri/Sat weekend -- the same
    weekend costing.models.working_days_between() counts forward over, so the
    dashboard measures a working day the same way everywhere. A calendar
    subtraction would put the whole buffer on the weekend whenever a deadline
    falls early in the week, leaving sales no usable time at all."""

    def test_buffer_skips_the_ksa_weekend(self):
        """A Sunday deadline loses Fri and Sat, so the BOM is due the
        Wednesday before -- four calendar days, two working ones."""
        from kpis.registry import _bom_due_date
        due = _bom_due_date(datetime.date(2026, 6, 14))     # Sunday
        self.assertEqual(due, datetime.date(2026, 6, 10))   # Wednesday
        self.assertEqual(due.weekday(), 2)

    def test_a_midweek_deadline_loses_no_weekend(self):
        """Thursday deadline -> Tuesday, a plain two calendar days, because
        nothing in between is a weekend."""
        from kpis.registry import _bom_due_date
        self.assertEqual(_bom_due_date(datetime.date(2026, 6, 11)),
                         datetime.date(2026, 6, 9))

    def test_the_due_date_is_never_itself_a_weekend_day(self):
        from kpis.registry import _bom_due_date
        for offset in range(21):
            day = datetime.date(2026, 6, 1) + datetime.timedelta(days=offset)
            with self.subTest(deadline=day.isoformat()):
                self.assertNotIn(_bom_due_date(day).weekday(), (4, 5))

    def test_no_submission_deadline_means_no_due_date(self):
        """Nothing to be late against -- the tiles count these separately
        rather than treating them as on time."""
        from kpis.registry import _bom_due_date
        self.assertIsNone(_bom_due_date(None))


class BomBufferOverdueTests(ComputeFixtureMixin, TestCase):
    """The buffer applied through the overdue tile: a deadline that has not
    passed can still be overdue, because the BOM was due before it."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    def _rfq_due_in(self, ref, days):
        p = self._project(ref, self.active, est='100000')
        Project.objects.filter(pk=p.pk).update(
            submission_deadline=self.today + datetime.timedelta(days=days))
        return p

    def test_a_deadline_still_ahead_can_already_be_overdue(self):
        """The point of the buffer. The client deadline is tomorrow, so the
        BOM was due with sales days ago and has not been sent."""
        self._rfq_due_in('TOMORROW', 1)
        res = KPI_BY_KEY['proposal_rfqs_overdue'].compute(
            make_context('2026-Q2', region=self.region))
        self.assertEqual(res.value, Decimal('1'))
        self.assertIn('past due to sales', res.coverage)

    def test_a_distant_deadline_is_pending_not_overdue(self):
        self._rfq_due_in('FAR', 60)
        self.assertEqual(
            _val('proposal_rfqs_overdue', '2026-Q2', region=self.region), Decimal('0'))
        self.assertEqual(
            _val('proposal_rfqs_pending', '2026-Q2', region=self.region), Decimal('1'))


class PeriodPickerTests(TestCase):
    """The filter bar chooses granularity first, then value, so only one
    granularity's options are on screen at a time. The old flat dropdown put
    12 months, 4 quarters and 2 years in one list, where "June 2026" and
    "Q2 2026" looked like the same kind of choice."""

    TODAY = datetime.date(2026, 6, 17)      # a Wednesday in Q2

    def test_kind_is_read_from_the_period_string(self):
        from kpis.views import _period_kind
        self.assertEqual(_period_kind('2026-06'), 'month')
        self.assertEqual(_period_kind('2026-Q2'), 'quarter')
        self.assertEqual(_period_kind('2026'), 'year')

    def test_switching_granularity_keeps_the_reader_in_place(self):
        """June -> Quarter must give Q2 of the SAME year, not whichever
        quarter it happens to be today. Jumping to now would silently move
        the reader off the period they were studying."""
        from kpis.views import _switch_period_kind
        other_year = datetime.date(2027, 11, 3)
        self.assertEqual(
            _switch_period_kind('2026-06', 'quarter', other_year), '2026-Q2')
        self.assertEqual(
            _switch_period_kind('2026-06', 'year', other_year), '2026')
        self.assertEqual(
            _switch_period_kind('2026-Q2', 'year', other_year), '2026')

    def test_going_to_a_finer_grain_lands_on_today_when_it_fits(self):
        from kpis.views import _switch_period_kind
        self.assertEqual(
            _switch_period_kind('2026-Q2', 'month', self.TODAY), '2026-06')
        self.assertEqual(
            _switch_period_kind('2026', 'quarter', self.TODAY), '2026-Q2')

    def test_going_to_a_finer_grain_falls_back_when_today_is_outside(self):
        """Q1 2026 viewed in June: there is no single right month, so it
        lands on the quarter's first rather than one outside it."""
        from kpis.views import _switch_period_kind
        self.assertEqual(
            _switch_period_kind('2026-Q1', 'month', self.TODAY), '2026-01')
        self.assertEqual(
            _switch_period_kind('2025', 'quarter', self.TODAY), '2025-Q1')

    def test_switching_to_the_same_kind_is_a_no_op(self):
        from kpis.views import _switch_period_kind
        for period, kind in (('2026-06', 'month'), ('2026-Q2', 'quarter'),
                             ('2026', 'year')):
            with self.subTest(period=period):
                self.assertEqual(
                    _switch_period_kind(period, kind, self.TODAY), period)

    def test_picker_offers_only_the_active_granularity(self):
        from kpis.views import _period_picker
        month = _period_picker('2026-06', self.TODAY)
        self.assertEqual(month['kind'], 'month')
        self.assertEqual(len(month['values']), 12)
        self.assertTrue(all(len(v['period']) == 7 and 'Q' not in v['period']
                            for v in month['values']))

        quarter = _period_picker('2026-Q2', self.TODAY)
        self.assertEqual([v['period'] for v in quarter['values']],
                         ['2026-Q1', '2026-Q2', '2026-Q3', '2026-Q4'])

        year = _period_picker('2026', self.TODAY)
        self.assertEqual([v['period'] for v in year['values']],
                         ['2026', '2025', '2024'])

    def test_the_active_value_and_the_current_one_are_both_marked(self):
        """Active drives the highlight; is_current drives the dot that makes
        "this month" findable in a row of twelve without reading every label.
        They are different things - a reader on March still needs to see where
        June is."""
        from kpis.views import _period_picker
        picker = _period_picker('2026-03', self.TODAY)
        active = [v for v in picker['values'] if v['active']]
        current = [v for v in picker['values'] if v['is_current']]
        self.assertEqual([v['period'] for v in active], ['2026-03'])
        self.assertEqual([v['period'] for v in current], ['2026-06'])

    def test_quarter_values_follow_the_period_year_not_today(self):
        from kpis.views import _period_picker
        picker = _period_picker('2024-Q3', self.TODAY)
        self.assertTrue(all(v['period'].startswith('2024')
                            for v in picker['values']))


class FilterBarRenderTests(ComputeFixtureMixin, TestCase):
    """The bar is plain links, so every state is a real URL and it works with
    JavaScript disabled. The old version submitted a <select> on change, which
    meant no state was addressable."""

    def setUp(self):
        super().setUp()
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.user = User.objects.create_user(
            username='gm_filter', password='pw',
            role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.client.force_login(self.user)

    def test_bar_renders_regions_and_granularities(self):
        body = self.client.get(reverse('kpis:kpi_new')).content.decode()
        self.assertIn('kpi-filter', body)
        for label in ('All regions', 'Month', 'Quarter', 'Year'):
            with self.subTest(label=label):
                self.assertIn(label, body)
        self.assertIn(self.region.name, body)

    def test_every_control_is_a_link_not_a_select(self):
        """No <select> and no onchange submit — each state must be a URL."""
        body = self.client.get(reverse('kpis:kpi_new')).content.decode()
        bar = body[body.index('kpi-filter mb-4'):body.index('kpi_new_sales')]             if 'kpi_new_sales' in body else body[body.index('kpi-filter mb-4'):]
        self.assertNotIn('<select', bar)
        self.assertNotIn('onchange', bar)

    def test_region_links_preserve_the_period(self):
        resp = self.client.get(reverse('kpis:kpi_new') + '?period=2026-Q2')
        body = resp.content.decode()
        self.assertIn(f'?period=2026-Q2&amp;region={self.region.code}', body)

    def test_granularity_links_preserve_the_region(self):
        resp = self.client.get(
            reverse('kpis:kpi_new') + f'?period=2026-06&region={self.region.code}')
        body = resp.content.decode()
        # Month -> Quarter must keep both the region and the containing quarter.
        self.assertIn(f'?period=2026-Q2&amp;region={self.region.code}', body)


class ActivityTabTests(ComputeFixtureMixin, TestCase):
    """Team Activity as a tab on the KPI dashboard, rendered from the same
    builder and the same table partial as the standalone page -- so one
    person's figures cannot differ depending on which page you opened."""

    def setUp(self):
        super().setUp()
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.user = User.objects.create_user(
            username='gm_tab', password='pw',
            role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.client.force_login(self.user)

    def test_default_tab_shows_the_kpi_tiles(self):
        body = self.client.get(reverse('kpis:kpi_new')).content.decode()
        self.assertIn('Sales &amp; Pipeline', body)
        self.assertIn('Team Activity', body)          # the tab is offered

    def test_activity_tab_shows_the_table_instead_of_the_tiles(self):
        body = self.client.get(
            reverse('kpis:kpi_new') + '?view=activity').content.decode()
        self.assertNotIn('Sales &amp; Pipeline', body)
        self.assertIn('Person', body)

    def test_tabs_carry_the_region(self):
        """Switching tab must not silently reset the filters."""
        body = self.client.get(
            reverse('kpis:kpi_new') + f'?region={self.region.code}').content.decode()
        self.assertIn(f'?view=activity&amp;region={self.region.code}', body)

    def test_the_filter_bar_keeps_you_on_the_activity_tab(self):
        """Picking a region from the activity tab must not bounce you back to
        the KPI tiles."""
        body = self.client.get(
            reverse('kpis:kpi_new') + '?view=activity').content.decode()
        self.assertIn(f'region={self.region.code}&amp;view=activity', body)

    def test_only_the_activity_tab_offers_all_time(self):
        """A KPI is a rate over a window: an all-time revenue figure would
        only ever grow, so the tiles deliberately do not offer it."""
        kpis_body = self.client.get(reverse('kpis:kpi_new')).content.decode()
        act_body = self.client.get(
            reverse('kpis:kpi_new') + '?view=activity').content.decode()
        bar_of = lambda b: b[b.index('kpi-filter mb-4'):b.index('kpi-filter mb-4') + 4000]
        self.assertNotIn('All time', bar_of(kpis_body))
        self.assertIn('All time', bar_of(act_body))

    def test_an_unknown_view_falls_back_to_the_tiles(self):
        body = self.client.get(
            reverse('kpis:kpi_new') + '?view=nonsense').content.decode()
        self.assertIn('Sales &amp; Pipeline', body)


class ActivityPageFilterTests(ComputeFixtureMixin, TestCase):
    """The standalone Team Activity page now uses the shared filter bar, and
    scopes by region CODE like every other page rather than a raw Region pk."""

    def setUp(self):
        super().setUp()
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.user = User.objects.create_user(
            username='gm_act', password='pw',
            role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.client.force_login(self.user)

    def test_page_renders_with_the_shared_bar(self):
        resp = self.client.get(reverse('kpis:activity'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('kpi-filter', body)
        self.assertIn('All time', body)

    def test_region_is_scoped_by_code(self):
        resp = self.client.get(
            reverse('kpis:activity') + f'?region={self.region.code}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['selected_region'], self.region)

    def test_an_unknown_region_code_falls_back_to_all_regions(self):
        resp = self.client.get(reverse('kpis:activity') + '?region=NOPE')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['selected_region'])

    def test_the_old_select_controls_are_gone(self):
        body = self.client.get(reverse('kpis:activity')).content.decode()
        self.assertNotIn('onchange="this.form.submit()"', body)

    def test_period_defaults_to_all_time(self):
        """Activity is a lifetime review by default; that behaviour predates
        the new bar and must survive it."""
        resp = self.client.get(reverse('kpis:activity'))
        self.assertEqual(resp.context['period'], 'all')
        self.assertEqual(resp.context['period_picker']['kind'], 'all')

    def test_leaving_all_time_lands_on_the_current_window(self):
        """There is nowhere to "stay" when leaving the lifetime view, so each
        granularity tab points at the current one."""
        from kpis.views import _switch_period_kind
        from kpis.periods import current_period
        today = datetime.date(2026, 6, 17)
        for kind in ('month', 'quarter', 'year'):
            with self.subTest(kind=kind):
                self.assertEqual(_switch_period_kind('all', kind, today),
                                 current_period(kind, today))


class DealsWonTests(ComputeFixtureMixin, TestCase):
    """Deals Won: which deal, when it flipped to Won, and who flipped it.

    Read from ProjectHistory, because the question is *when* and *by whom* and
    the project's year/quarter tags carry neither."""

    def setUp(self):
        super().setUp()
        self.closer = User.objects.create_user(username='closer', password='pw')
        self.other = User.objects.create_user(username='other', password='pw')

    def _win(self, ref, when, by=None, est='100000', from_status=None, region=None):
        p = self._project(ref, self.won, est=est, region=region)
        h = ProjectHistory.objects.create(
            project=p, old_status=from_status or self.active,
            new_status=self.won, changed_by=by or self.closer)
        ProjectHistory.objects.filter(pk=h.pk).update(
            changed_at=timezone.make_aware(when))
        return p

    def test_rows_carry_the_deal_the_moment_and_the_person(self):
        from kpis.services import build_deals_won
        self._win('ALPHA', datetime.datetime(2026, 5, 12, 10, 0), est='500000')
        won = build_deals_won('2026-Q2', region=self.region)
        self.assertEqual(won['count'], 1)
        row = won['rows'][0]
        self.assertEqual(row['project'].project_name, 'ALPHA')
        self.assertEqual(row['won_at'].date(), datetime.date(2026, 5, 12))
        self.assertEqual(row['won_by'], self.closer)
        self.assertEqual(row['amount'], Decimal('500000'))

    def test_only_transitions_inside_the_window_count(self):
        from kpis.services import build_deals_won
        self._win('APR', datetime.datetime(2026, 4, 3, 9, 0))
        self._win('JUL', datetime.datetime(2026, 7, 3, 9, 0))
        self.assertEqual(build_deals_won('2026-Q2', region=self.region)['count'], 1)
        self.assertEqual(build_deals_won('2026-Q3', region=self.region)['count'], 1)
        self.assertEqual(build_deals_won('2026', region=self.region)['count'], 2)

    def test_a_deal_won_twice_counts_once_on_its_first_win(self):
        """Won, reopened, won again is one deal won. Counting the correction
        would inflate both the count and the value."""
        from kpis.services import build_deals_won
        p = self._win('REDO', datetime.datetime(2026, 5, 1, 9, 0), est='300000')
        h = ProjectHistory.objects.create(
            project=p, old_status=self.active, new_status=self.won,
            changed_by=self.other)
        ProjectHistory.objects.filter(pk=h.pk).update(
            changed_at=timezone.make_aware(datetime.datetime(2026, 5, 20, 9, 0)))
        won = build_deals_won('2026-Q2', region=self.region)
        self.assertEqual(won['count'], 1)
        self.assertEqual(won['total'], Decimal('300000'))
        self.assertEqual(won['rows'][0]['won_at'].date(), datetime.date(2026, 5, 1))
        self.assertEqual(won['rows'][0]['won_by'], self.closer)

    def test_people_rollup_groups_by_who_marked_it(self):
        from kpis.services import build_deals_won
        self._win('A', datetime.datetime(2026, 5, 1, 9, 0), by=self.closer, est='100000')
        self._win('B', datetime.datetime(2026, 5, 2, 9, 0), by=self.closer, est='200000')
        self._win('C', datetime.datetime(2026, 5, 3, 9, 0), by=self.other, est='50000')
        people = build_deals_won('2026-Q2', region=self.region)['people']
        self.assertEqual(people[0]['user'], self.closer)
        self.assertEqual(people[0]['count'], 2)
        self.assertEqual(people[0]['amount'], Decimal('300000'))
        self.assertEqual(people[1]['user'], self.other)

    def test_untracked_counts_won_deals_with_no_transition(self):
        """The health figure. ProjectHistory is written by one view only, so an
        empty table has to be distinguishable from "nobody recorded it"."""
        from kpis.services import build_deals_won
        self._win('TRACKED', datetime.datetime(2026, 5, 1, 9, 0))
        self._project('SILENT', self.won, est='400000')      # no history row
        won = build_deals_won('2026-Q2', region=self.region)
        self.assertEqual(won['count'], 1)
        self.assertEqual(won['untracked'], 1)
        self.assertEqual(won['won_now'], 2)

    def test_a_deal_won_in_another_period_is_not_untracked(self):
        """Untracked means "no transition ever recorded", not "not won in this
        window" - otherwise every past win would look like a data gap."""
        from kpis.services import build_deals_won
        self._win('LASTYEAR', datetime.datetime(2025, 3, 1, 9, 0))
        won = build_deals_won('2026-Q2', region=self.region)
        self.assertEqual(won['count'], 0)
        self.assertEqual(won['untracked'], 0)

    def test_scoped_by_region(self):
        from kpis.services import build_deals_won
        self._win('MINE', datetime.datetime(2026, 5, 1, 9, 0), region=self.region)
        self._win('THEIRS', datetime.datetime(2026, 5, 2, 9, 0), region=self.region2)
        self.assertEqual(build_deals_won('2026-Q2', region=self.region)['count'], 1)
        self.assertEqual(build_deals_won('2026-Q2')['count'], 2)

    def test_total_agrees_with_the_shared_value_ladder(self):
        """The rows must total what the KPI tiles would say for the same
        deals, or the tab and the dashboard disagree about one number."""
        from kpis.services import build_deals_won
        from projects.views import _resolve_project_sales_value
        a = self._win('A', datetime.datetime(2026, 5, 1, 9, 0), est='120000')
        b = self._win('B', datetime.datetime(2026, 5, 2, 9, 0), est='340000')
        expected = sum(
            (_resolve_project_sales_value(p, list(p.costing_sheets.all()))['amount']
             or Decimal('0')) for p in (a, b))
        self.assertEqual(build_deals_won('2026-Q2', region=self.region)['total'],
                         expected)


class DealsWonTabTests(ComputeFixtureMixin, TestCase):
    """The Deals Won tab on the KPI dashboard."""

    def setUp(self):
        super().setUp()
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.user = User.objects.create_user(
            username='gm_won', password='pw',
            role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.client.force_login(self.user)

    def test_tab_renders_the_deal_rows(self):
        p = self._project('ALPHA', self.won, est='500000')
        h = ProjectHistory.objects.create(
            project=p, old_status=self.active, new_status=self.won,
            changed_by=self.user)
        ProjectHistory.objects.filter(pk=h.pk).update(
            changed_at=timezone.make_aware(datetime.datetime(2026, 5, 12, 10, 0)))
        body = self.client.get(
            reverse('kpis:kpi_new')
            + '?view=won&period=2026-Q2&region=' + self.region.code).content.decode()
        self.assertIn('ALPHA', body)
        self.assertIn('Marked won by', body)
        self.assertIn('Who closed them', body)

    def test_an_empty_period_explains_itself(self):
        """An empty table must not be mistaken for "we won nothing"."""
        self._project('SILENT', self.won, est='400000')     # won, never logged
        body = self.client.get(
            reverse('kpis:kpi_new')
            + '?view=won&period=2026-Q2&region=' + self.region.code).content.decode()
        self.assertIn('No deals were marked won', body)
        self.assertIn('no recorded transition', body)

    def test_the_tab_is_offered_and_keeps_the_filters(self):
        body = self.client.get(
            reverse('kpis:kpi_new') + '?region=' + self.region.code).content.decode()
        self.assertIn('Deals Won', body)
        self.assertIn('?view=won&amp;period=', body)

    def test_filter_bar_keeps_you_on_the_deals_won_tab(self):
        body = self.client.get(
            reverse('kpis:kpi_new') + '?view=won').content.decode()
        self.assertIn('&amp;view=won', body)
