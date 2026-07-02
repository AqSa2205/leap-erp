from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User, Role
from projects.models import Project, ProjectStatus, Region
from costing.models import CostingSheet, CostingSection, CostingLineItem
from finance.models import ProjectFinance, PaymentMilestone


class FinanceScheduleTests(TestCase):
    def setUp(self):
        self.sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.region = Region.objects.create(name='Arabia')
        self.user = User.objects.create_user('fin', password='pw', role=self.sa, region=self.region)
        self.client.force_login(self.user)
        self.won = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='P1', status=self.won, region=self.region)

    def test_schedule_seeds_standard_milestones(self):
        r = self.client.get(reverse('finance:schedule', kwargs={'project_pk': self.project.pk}))
        self.assertEqual(r.status_code, 200)
        pf = ProjectFinance.objects.get(project=self.project)
        self.assertEqual(pf.milestones.count(), 9)
        self.assertEqual(pf.milestones.first().name, 'Upon Design Submission')
        self.assertEqual(pf.milestones.last().name, 'Project Sign Off')
        self.assertTrue(pf.milestones.filter(is_subrow=True).exists())

    def test_amount_is_submitted_pct_of_po_value(self):
        pf = ProjectFinance.objects.create(
            project=self.project, po_value=Decimal('12661873.31'))
        m = PaymentMilestone.objects.create(
            project_finance=pf, name='M', submitted_pct=Decimal('10'))
        self.assertEqual(m.amount, Decimal('1266187.33'))

    def test_derived_dates_from_manual_prep(self):
        # Prep is manual; approval = prep + cert gap; submission = approval +
        # invoice gap; payment-receive = estimated start + payment gap.
        pf = ProjectFinance.objects.create(
            project=self.project, estimated_start_date=date(2026, 1, 1),
            cert_approval_gap=2, invoice_gap=30, payment_gap=30)
        m = PaymentMilestone.objects.create(
            project_finance=pf, name='M', work_cert_prep_date=date(2026, 3, 10))
        pf.recompute_dates()
        m.refresh_from_db()
        self.assertEqual(m.work_cert_prep_date, date(2026, 3, 10))   # untouched
        self.assertEqual(m.work_cert_approval_date, date(2026, 3, 12))
        self.assertEqual(m.invoice_submission_date, date(2026, 4, 11))
        self.assertEqual(m.payment_receive_date, date(2026, 1, 31))  # est start + 30

    def test_timeline_periods_step_by_payment_gap(self):
        pf = ProjectFinance.objects.create(
            project=self.project, estimated_start_date=date(2026, 1, 1),
            estimated_end_date=date(2026, 6, 1))
        pf.payment_gap = 30
        self.assertEqual([p['label'] for p in pf.timeline_periods()],
                         ['Jan 2026', 'Feb 2026', 'Mar 2026', 'Apr 2026', 'May 2026', 'Jun 2026'])
        pf.payment_gap = 60
        self.assertEqual([p['label'] for p in pf.timeline_periods()],
                         ['Jan 2026', 'Mar 2026', 'May 2026'])
        pf.estimated_start_date = None
        self.assertEqual(pf.timeline_periods(), [])

    def test_non_finance_blocked(self):
        sales_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        sales = User.objects.create_user('s', password='pw', role=sales_role, region=self.region)
        self.client.force_login(sales)
        self.assertEqual(self.client.get(reverse('finance:home')).status_code, 302)
        self.assertEqual(self.client.get(
            reverse('finance:schedule', kwargs={'project_pk': self.project.pk})).status_code, 302)

    def test_finance_team_allowed(self):
        fin_role, _ = Role.objects.get_or_create(name=Role.FINANCE_REP)
        fin = User.objects.create_user('f2', password='pw', role=fin_role, region=self.region)
        self.client.force_login(fin)
        self.assertEqual(self.client.get(reverse('finance:home')).status_code, 200)

    def test_po_value_fetched_from_final_margin_on_open(self):
        sheet = CostingSheet.objects.create(
            title='S', created_by=self.user, project=self.project,
            margin=Decimal('40'), margin_final=Decimal('30'))
        sec = CostingSection.objects.create(
            costing_sheet=sheet, section_number='1', title='CCTV', order=0)
        CostingLineItem.objects.create(
            section=sec, description='Cam', quantity=Decimal('1'),
            base_unit_cost=Decimal('100'), supplier_currency='SAR', margin=Decimal('40'))
        # Opening the schedule fetches the P.O Value from M5 (final) grand price:
        # 100 / (1 - 0.30) = 142.86
        r = self.client.get(reverse('finance:schedule', kwargs={'project_pk': self.project.pk}))
        pf = ProjectFinance.objects.get(project=self.project)
        self.assertEqual(pf.po_value, Decimal('142.86'))
        self.assertEqual(pf.approved_margin, 'M5')
        self.assertIn(b'From M5 (Final)', r.content)

    def test_budget_screen_and_no_costing_impact(self):
        sheet = CostingSheet.objects.create(
            title='B', created_by=self.user, project=self.project,
            margin=Decimal('30'), discount_rate=Decimal('5'),
            workflow_stage='finance_review')
        sec = CostingSection.objects.create(
            costing_sheet=sheet, section_number='1', title='CCTV', order=0)
        item = CostingLineItem.objects.create(
            section=sec, item_number='1', description='Cam', quantity=Decimal('1'),
            base_unit_cost=Decimal('42000'), supplier_currency='SAR')

        url = reverse('finance:sheet_budget', kwargs={'sheet_pk': sheet.pk})
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        # Budget preset boxes reflect the costing sheet's margin/discount.
        self.assertContains(r, 'Budget defaults')

        # Change the budget margin to 0 and override the line — save.
        self.client.post(url, {
            'budget_margin': '0', 'budget_discount_rate': '5',
            f'line-{item.pk}-budget': '42000',
            f'line-{item.pk}-disc': '', f'line-{item.pk}-margin': '',
            f'line-{item.pk}-remarks': 'tight',
        })
        sheet.refresh_from_db()
        item.refresh_from_db()
        # Budget fields stored; the costing sheet's own margin/discount UNCHANGED.
        self.assertEqual(sheet.budget_margin, Decimal('0'))
        self.assertEqual(sheet.margin, Decimal('30'))
        self.assertEqual(sheet.discount_rate, Decimal('5'))
        self.assertEqual(item.budgeted_cost, Decimal('42000'))
        self.assertEqual(item.budget_remarks, 'tight')
        self.assertIsNone(item.margin)  # costing line margin untouched

    def test_approve_margin_sets_po_value(self):
        sheet = CostingSheet.objects.create(
            title='S', created_by=self.user, project=self.project, margin=Decimal('40'))
        sec = CostingSection.objects.create(
            costing_sheet=sheet, section_number='1', title='CCTV', order=0)
        CostingLineItem.objects.create(
            section=sec, description='Cam', quantity=Decimal('1'),
            base_unit_cost=Decimal('100'), supplier_currency='SAR', margin=Decimal('40'))
        # M1 (current) price = 100/(1-0.40) = 166.67
        r = self.client.post(reverse('finance:approve_margin',
                                     kwargs={'sheet_pk': sheet.pk, 'key': 'M1'}))
        pf = ProjectFinance.objects.get(project=self.project)
        self.assertEqual(pf.approved_margin, 'M1')
        self.assertEqual(pf.po_value, Decimal('166.67'))
        self.assertRedirects(r, reverse('finance:schedule', kwargs={'project_pk': self.project.pk}))
