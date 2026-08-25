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
        self.assertEqual(len(kpis_for_department(SALES)), 5)
        self.assertEqual(len(kpis_for_department(PROPOSAL)), 6)
        self.assertEqual(len(kpis_for_department(PROCUREMENT)), 10)
        self.assertEqual(len(KPI_DEFINITIONS), 22)

    def test_eleven_auto_ten_manual(self):
        auto = [k for k in KPI_DEFINITIONS if k.is_auto]
        self.assertEqual(len(auto), 12)
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
        self.assertEqual(len(keys), 11)
        self.assertNotIn('proc_supplier_performance', keys)   # manual excluded
        self.assertNotIn('sales_pipeline_coverage', keys)     # dept-only excluded


class ServiceTests(ComputeFixtureMixin, TestCase):
    def test_build_dashboard_shape(self):
        data = build_dashboard('2026-Q2')
        self.assertEqual(len(data['departments']), 4)
        counts = {d['key']: len(d['cards']) for d in data['departments']}
        self.assertEqual(counts, {SALES: 5, PROPOSAL: 6, PROCUREMENT: 10, 'hr': 1})

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
