"""Committed spend against the approved budget.

The arithmetic is trivial; putting the two numbers on the same footing is not.
A budgeted line price carries no VAT and a PO's `total_value` does, the two are
priced in different currencies, and "spent" means different things for a draft,
an issued order and a cancelled one. Each of those is a way to be quietly wrong
by 15%, by a factor of 3.75, or by an entire order — so each has a test.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from costing.models import CostingSheet, ExchangeRate
from procurement.budget_status import (
    approved_budgets_for, budget_status, commitment, to_base_currency,
)
from procurement.models import PurchaseOrder, PurchaseOrderItem
from projects.models import Project, ProjectStatus, Region

User = get_user_model()


class BudgetBasisTests(TestCase):
    """The denominator: what finance approved, on the basis POs are priced on."""

    def setUp(self):
        ExchangeRate.objects.update_or_create(
            currency_code='USD', defaults={'currency_name': 'US Dollar',
                                           'rate_to_usd': Decimal('1')})
        ExchangeRate.objects.update_or_create(
            currency_code='SAR', defaults={'currency_name': 'Saudi Riyal',
                                           'rate_to_usd': Decimal('3.75')})
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = self._project('Ghazlan', 'LNA-2026-1001')

    def _project(self, name, ref):
        return Project.objects.create(
            project_name=name, proposal_reference=ref, status=self.status,
            region=self.region, estimated_value=Decimal('1'),
            actual_sales=Decimal('0'), year='2026', po_award_quarter='Q2')

    def _sheet(self, project, stage='finance_approved', title='Budget'):
        return CostingSheet.objects.create(title=title, project=project,
                                           workflow_stage=stage)

    def _line(self, sheet, budget_price, *, optional=False, section=None):
        if section is None:
            section = sheet.sections.create(title='A.1 Supply', order=1,
                                            is_optional=optional)
        return section.line_items.create(
            description='Camera', quantity=Decimal('1'), order=1,
            budget_price=budget_price), section

    def test_the_budget_is_the_sum_of_its_budgeted_line_prices(self):
        sheet = self._sheet(self.project)
        _item, section = self._line(sheet, Decimal('1000'))
        self._line(sheet, Decimal('500'), section=section)
        budgets = approved_budgets_for([self.project])
        self.assertEqual(budgets[self.project.pk], Decimal('1500'))

    def test_a_sheet_that_finance_has_not_approved_is_not_budget(self):
        """Only an approved budget is money anyone may spend against."""
        sheet = self._sheet(self.project, stage='finance_review')
        self._line(sheet, Decimal('1000'))
        self.assertEqual(approved_budgets_for([self.project]), {})

    def test_optional_sections_are_not_budget(self):
        """They are quotable extras the client has not bought. po_from_budget
        skips them too, and the two must agree or a PO seeded from a budget
        would not reconcile against it."""
        sheet = self._sheet(self.project)
        self._line(sheet, Decimal('1000'))
        self._line(sheet, Decimal('9999'), optional=True)
        self.assertEqual(approved_budgets_for([self.project])[self.project.pk],
                         Decimal('1000'))

    def test_several_approved_sheets_on_one_project_add_up(self):
        """Separate phases or scopes are all budget for the same project."""
        for title, price in (('Phase 1', '1000'), ('Phase 2', '250')):
            self._line(self._sheet(self.project, title=title), Decimal(price))
        self.assertEqual(approved_budgets_for([self.project])[self.project.pk],
                         Decimal('1250'))

    def test_a_project_with_no_approved_sheet_is_absent_not_zero(self):
        """Absent and zero mean different things downstream: one has no
        percentage, the other has a percentage of nothing."""
        self.assertNotIn(self.project.pk, approved_budgets_for([self.project]))

    def test_budgets_are_looked_up_for_the_whole_board_at_once(self):
        """A per-project walk would issue queries in proportion to every
        project procurement tracks.

        Asserts the count is the same at two sizes rather than pinning a
        number: a fixed expectation breaks on any unrelated query change and
        invites being updated to whatever the code happens to do, which is how
        an N+1 gets accepted rather than fixed.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries_for(projects):
            with CaptureQueriesContext(connection) as ctx:
                approved_budgets_for(projects)
            return len(ctx.captured_queries)

        projects = [self.project]
        self._line(self._sheet(self.project), Decimal('100'))
        small = queries_for(list(projects))

        for i in range(8):
            project = self._project(f'Extra {i}', f'LNA-2026-20{i:02d}')
            self._line(self._sheet(project), Decimal('100'))
            projects.append(project)
        self.assertEqual(small, queries_for(projects))


class CommitmentTests(TestCase):
    """The numerator: what the purchase orders actually commit."""

    def setUp(self):
        ExchangeRate.objects.update_or_create(
            currency_code='USD', defaults={'currency_name': 'US Dollar',
                                           'rate_to_usd': Decimal('1')})
        ExchangeRate.objects.update_or_create(
            currency_code='SAR', defaults={'currency_name': 'Saudi Riyal',
                                           'rate_to_usd': Decimal('3.75')})
        region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='Ghazlan', proposal_reference='LNA-2026-1001',
            status=status, region=region, estimated_value=Decimal('1'),
            actual_sales=Decimal('0'), year='2026', po_award_quarter='Q2')
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.user = User.objects.create_user(
            'bs-super', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))

    def _po(self, number, amount, *, status='issued', currency='SAR', vat='15'):
        po = PurchaseOrder.objects.create(
            po_date='2026-01-01', po_number=number, vendor_name='ACME',
            po_issued_by='T', project=self.project, created_by=self.user,
            currency=currency, status=status, vat_rate=Decimal(vat))
        PurchaseOrderItem.objects.create(
            purchase_order=po, description='Thing', quantity=Decimal('1'),
            rate_per_unit=Decimal(amount))
        return po

    def test_vat_is_excluded_from_committed_spend(self):
        """total_value carries VAT and a budgeted price does not. Comparing
        those reports every project ~15% further through its budget than it
        is — consistently, and invisibly."""
        po = self._po('PO-1', '1000', vat='15')
        self.assertEqual(po.total_value, Decimal('1150.00'))
        self.assertEqual(commitment([po])['committed'], Decimal('1000'))

    def test_a_cancelled_order_commits_nothing(self):
        po = self._po('PO-2', '1000', status='cancelled')
        self.assertEqual(commitment([po])['committed'], Decimal('0'))
        self.assertEqual(commitment([po])['draft'], Decimal('0'))

    def test_a_draft_is_reported_separately_from_committed(self):
        """It is not a commitment — but it is about to become one, and
        somebody deciding whether to raise another needs to see it."""
        spend = commitment([self._po('PO-3', '400', status='draft'),
                            self._po('PO-4', '600', status='issued')])
        self.assertEqual(spend['committed'], Decimal('600'))
        self.assertEqual(spend['draft'], Decimal('400'))

    def test_an_acknowledged_or_completed_order_still_counts(self):
        """Money committed does not become uncommitted by progressing."""
        for i, status in enumerate(('client_acknowledged', 'completed')):
            self._po(f'PO-S{i}', '100', status=status)
        self.assertEqual(
            commitment(PurchaseOrder.objects.all())['committed'], Decimal('200'))

    def test_a_foreign_currency_order_is_converted_not_added_raw(self):
        """1000 USD is 3750 SAR, not 1000 SAR. Adding it raw would understate
        commitment by nearly a factor of four."""
        po = self._po('PO-5', '1000', currency='USD')
        self.assertEqual(commitment([po])['committed'], Decimal('3750.00'))

    def test_a_conversion_is_flagged_so_the_number_can_be_read_honestly(self):
        self.assertTrue(commitment([self._po('PO-6', '10', currency='USD')])['converted'])
        self.assertFalse(commitment([self._po('PO-7', '10')])['converted'])

    def test_an_unknown_currency_is_kept_rather_than_dropped(self):
        """Losing committed spend from the figure is worse than a rate that
        could not be applied."""
        rates = {'SAR': Decimal('3.75')}
        self.assertEqual(to_base_currency(Decimal('100'), 'JPY', rates),
                         Decimal('100'))


class BudgetStatusTests(TestCase):
    """The percentage, and the cases where a number would be a lie."""

    def _status(self, budget, committed=Decimal('0'), draft=Decimal('0')):
        class FakePO:
            def __init__(self, amount, status):
                self.gross_value = amount
                self.status = status
                self.currency = 'SAR'
        orders = []
        if committed:
            orders.append(FakePO(committed, 'issued'))
        if draft:
            orders.append(FakePO(draft, 'draft'))
        return budget_status(budget, orders, rates={'SAR': Decimal('3.75')})

    def test_the_percentage_is_committed_over_budget(self):
        self.assertEqual(self._status(Decimal('1000'), Decimal('250'))['percent'],
                         Decimal('25.0'))

    def test_no_budget_has_no_percentage_rather_than_zero(self):
        """0% reads as 'nothing spent'. The truth is 'nothing to compare
        against', and rendering a bar would invent a fact."""
        status = self._status(None, Decimal('500'))
        self.assertIsNone(status['percent'])
        self.assertFalse(status['has_budget'])

    def test_a_zero_budget_is_treated_the_same_as_none(self):
        """Otherwise it divides by zero, and 'x% of nothing' is not a number
        anyone can act on."""
        self.assertIsNone(self._status(Decimal('0'), Decimal('500'))['percent'])

    def test_going_over_budget_is_reported_not_capped(self):
        """The single most important thing this can say. Clamping it to 100%
        would render a full bar that looks like success."""
        status = self._status(Decimal('1000'), Decimal('1400'))
        self.assertEqual(status['percent'], Decimal('140.0'))
        self.assertTrue(status['over_budget'])

    def test_drafts_that_would_break_the_budget_are_flagged_before_issue(self):
        """The moment to act is before they are issued, not after."""
        status = self._status(Decimal('1000'), Decimal('800'), Decimal('400'))
        self.assertFalse(status['over_budget'])
        self.assertTrue(status['draft_would_exceed'])
        self.assertEqual(status['percent'], Decimal('80.0'))
        self.assertEqual(status['percent_with_draft'], Decimal('120.0'))

    def test_a_draft_within_budget_raises_no_flag(self):
        status = self._status(Decimal('1000'), Decimal('300'), Decimal('200'))
        self.assertFalse(status['draft_would_exceed'])
        self.assertFalse(status['over_budget'])

    def test_remaining_is_reported_against_committed_not_draft(self):
        self.assertEqual(
            self._status(Decimal('1000'), Decimal('300'), Decimal('200'))['remaining'],
            Decimal('700'))


class BudgetStatusOnTheBoardTests(TestCase):
    """The figure where procurement actually reads it."""

    def setUp(self):
        from accounts.permissions import seed_default_permissions
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        ExchangeRate.objects.update_or_create(
            currency_code='SAR', defaults={'currency_name': 'Saudi Riyal',
                                           'rate_to_usd': Decimal('3.75')})
        region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='active')
        self.user = User.objects.create_user(
            'bs-board', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.project = Project.objects.create(
            project_name='Ghazlan', proposal_reference='LNA-2026-1001',
            status=status, region=region, estimated_value=Decimal('1'),
            actual_sales=Decimal('0'), year='2026', po_award_quarter='Q2')
        sheet = CostingSheet.objects.create(title='Budget', project=self.project,
                                            workflow_stage='finance_approved')
        section = sheet.sections.create(title='A.1 Supply', order=1)
        section.line_items.create(description='Camera', quantity=Decimal('1'),
                                  order=1, budget_price=Decimal('1000'))

    def _po(self, number, amount, status='issued'):
        po = PurchaseOrder.objects.create(
            po_date='2026-01-01', po_number=number, vendor_name='ACME',
            po_issued_by='T', project=self.project, created_by=self.user,
            status=status, vat_rate=Decimal('15'))
        PurchaseOrderItem.objects.create(
            purchase_order=po, description='Thing', quantity=Decimal('1'),
            rate_per_unit=Decimal(amount))
        return po

    def _group(self):
        from procurement.views import _po_project_groups
        return next(g for g in _po_project_groups(self.user)
                    if g['project'] == self.project)

    def test_the_group_carries_the_percentage(self):
        self._po('PO-B1', '250')
        self.assertEqual(self._group()['budget_status']['percent'], Decimal('25.0'))

    def test_the_percentage_ignores_vat_on_the_orders(self):
        """End to end: a 1000 SAR order against a 1000 SAR budget is 100%,
        not 115%."""
        self._po('PO-B2', '1000')
        self.assertEqual(self._group()['budget_status']['percent'], Decimal('100.0'))

    def test_the_page_shows_the_percentage(self):
        self._po('PO-B3', '250')
        self.client.force_login(self.user)
        body = self.client.get(reverse('procurement:po_by_project')).content.decode()
        self.assertIn('25.0%', body)

    def test_the_page_says_so_when_there_is_no_budget(self):
        other = Project.objects.create(
            project_name='No Budget Job', proposal_reference='LNA-2026-9999',
            status=self.project.status, region=self.project.region,
            estimated_value=Decimal('1'), actual_sales=Decimal('0'),
            year='2026', po_award_quarter='Q2')
        PurchaseOrder.objects.create(
            po_date='2026-01-01', po_number='PO-B4', vendor_name='ACME',
            po_issued_by='T', project=other, created_by=self.user)
        self.client.force_login(self.user)
        body = self.client.get(reverse('procurement:po_by_project')).content.decode()
        self.assertIn('no budget set', body)

    def test_the_query_count_does_not_grow_with_the_projects(self):
        """The budget walk touches sections and line items, so a per-project
        lookup degrades quietly as the board fills up."""
        self._po('PO-B5', '250')

        def queries():
            from django.db import connection
            from django.test.utils import CaptureQueriesContext
            with CaptureQueriesContext(connection) as ctx:
                self._group()
            return len(ctx.captured_queries)

        small = queries()
        for i in range(6):
            project = Project.objects.create(
                project_name=f'Bulk {i}', proposal_reference=f'LNA-2026-30{i:02d}',
                status=self.project.status, region=self.project.region,
                estimated_value=Decimal('1'), actual_sales=Decimal('0'),
                year='2026', po_award_quarter='Q2')
            sheet = CostingSheet.objects.create(
                title=f'B{i}', project=project, workflow_stage='finance_approved')
            section = sheet.sections.create(title='A.1', order=1)
            section.line_items.create(description='X', quantity=Decimal('1'),
                                      order=1, budget_price=Decimal('100'))
        self.assertEqual(small, queries())
