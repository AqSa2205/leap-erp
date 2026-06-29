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

    def test_recompute_dates_from_kickoff(self):
        pf = ProjectFinance.objects.create(
            project=self.project, kickoff_date=date(2026, 6, 1),
            cert_approval_gap=2, invoice_gap=30, payment_gap=30)
        m = PaymentMilestone.objects.create(
            project_finance=pf, name='M', from_kickoff_days=30)
        pf.recompute_dates()
        m.refresh_from_db()
        self.assertEqual(m.work_cert_prep_date, date(2026, 7, 1))
        self.assertEqual(m.work_cert_approval_date, date(2026, 7, 3))
        self.assertEqual(m.invoice_submission_date, date(2026, 8, 2))
        self.assertEqual(m.payment_receive_date, date(2026, 9, 1))

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
