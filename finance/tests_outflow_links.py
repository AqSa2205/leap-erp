"""Stamping real PO numbers onto the cash-outflow schedule.

The value here is small and the damage is not: this writes into a financial
schedule finance maintains by hand. So most of these tests are about what it
must refuse to do — overwrite somebody's entry, write a generated placeholder,
or credit an outflow to an order that has not actually been placed.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Role
from costing.models import CostingSheet, ExchangeRate
from finance.models import CashOutflowRow
from finance.outflow_links import backfill, fill_po_numbers, is_placeholder
from procurement.models import PurchaseOrder, PurchaseOrderItem
from projects.models import Project, ProjectStatus, Region

User = get_user_model()


class OutflowPoNumberTests(TestCase):

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        ExchangeRate.objects.update_or_create(
            currency_code='SAR', defaults={'currency_name': 'Saudi Riyal',
                                           'rate_to_usd': Decimal('3.75')})
        self.user = User.objects.create_user(
            'of-super', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='Ghazlan', proposal_reference='LNA-2026-1001',
            status=status, region=region, estimated_value=Decimal('1'),
            actual_sales=Decimal('0'), year='2026', po_award_quarter='Q2')

        self.sheet = CostingSheet.objects.create(
            title='Budget', project=self.project, workflow_stage='finance_approved')
        section = self.sheet.sections.create(title='A.1 Supply', order=1)
        self.line = section.line_items.create(
            description='Camera', quantity=Decimal('2'), order=1,
            budget_price=Decimal('1000'))
        self.row = CashOutflowRow.objects.create(
            project=self.project, part='A1', order=0, description='Camera',
            amount=Decimal('1000'), vat=Decimal('150'),
            total_amount=Decimal('1150'), source_ref=f'line:{self.line.pk}')

    def _po(self, number, *, status='issued', project=None, line=None):
        po = PurchaseOrder.objects.create(
            po_date='2026-01-01', po_number=number, vendor_name='ACME',
            po_issued_by='T', project=project if project is not None else self.project,
            created_by=self.user, status=status)
        PurchaseOrderItem.objects.create(
            purchase_order=po, serial_number=1, description='Camera',
            quantity=Decimal('2'), rate_per_unit=Decimal('500'),
            source_bom_item=self.line if line is None else line)
        return po

    def _row(self):
        self.row.refresh_from_db()
        return self.row.po_number

    # ── the happy path ──────────────────────────────────────────────────────

    def test_an_issued_po_fills_the_row_it_covers(self):
        fill_po_numbers(self._po('PO2308-001'))
        self.assertEqual(self._row(), 'PO2308-001')

    def test_issuing_through_the_status_change_fills_it_without_being_asked(self):
        """The point of hooking the one method every status change goes
        through — no call site has to remember."""
        po = self._po('PO2308-002', status='draft')
        self.assertEqual(self._row(), '')
        po.record_status_change(to_status='issued', changed_by=self.user)
        self.assertEqual(self._row(), 'PO2308-002')

    def test_only_the_row_for_the_line_that_po_covers_is_touched(self):
        other_line = self.sheet.sections.first().line_items.create(
            description='Lens', quantity=Decimal('1'), order=2,
            budget_price=Decimal('50'))
        other_row = CashOutflowRow.objects.create(
            project=self.project, part='A1', order=1, description='Lens',
            amount=Decimal('50'), vat=Decimal('7.5'), total_amount=Decimal('57.5'),
            source_ref=f'line:{other_line.pk}')
        fill_po_numbers(self._po('PO2308-003'))
        other_row.refresh_from_db()
        self.assertEqual(other_row.po_number, '')

    # ── what it must refuse ─────────────────────────────────────────────────

    def test_it_never_overwrites_what_finance_typed(self):
        """The field is finance's. A number is added, never substituted."""
        self.row.po_number = 'agreed verbally, PO to follow'
        self.row.save()
        fill_po_numbers(self._po('PO2308-004'))
        self.assertTrue(self._row().startswith('agreed verbally, PO to follow'))

    def test_a_placeholder_number_is_never_written(self):
        """po_from_budget generates DRAFT-S12-1787214550 purely to satisfy the
        unique constraint. On a financial schedule that looks like an answer."""
        self.assertTrue(is_placeholder('DRAFT-S12-1787214550'))
        fill_po_numbers(self._po('DRAFT-S12-1787214550'))
        self.assertEqual(self._row(), '')

    def test_a_draft_po_fills_nothing(self):
        """Nothing is ordered until it is issued, so it covers no outflow."""
        fill_po_numbers(self._po('PO2308-005', status='draft'))
        self.assertEqual(self._row(), '')

    def test_a_cancelled_po_fills_nothing(self):
        fill_po_numbers(self._po('PO2308-006', status='cancelled'))
        self.assertEqual(self._row(), '')

    def test_a_po_with_no_project_fills_nothing(self):
        """Without a project there is no schedule it could belong to, and
        matching on the costing line alone could write into another job."""
        po = self._po('PO2308-007')
        PurchaseOrder.objects.filter(pk=po.pk).update(project=None)
        po.refresh_from_db()
        self.assertEqual(fill_po_numbers(po), 0)

    def test_a_po_whose_items_have_no_costing_link_fills_nothing(self):
        """Nothing to match on — guessing by description would credit an
        outflow to an order that may not cover it."""
        po = PurchaseOrder.objects.create(
            po_date='2026-01-01', po_number='PO2308-008', vendor_name='ACME',
            po_issued_by='T', project=self.project, created_by=self.user,
            status='issued')
        PurchaseOrderItem.objects.create(
            purchase_order=po, serial_number=1, description='Camera',
            quantity=Decimal('2'), rate_per_unit=Decimal('500'))
        self.assertEqual(fill_po_numbers(po), 0)

    def test_a_row_on_another_project_is_never_touched(self):
        other = Project.objects.create(
            project_name='Elsewhere', proposal_reference='LNA-2026-2002',
            status=self.project.status, region=self.project.region,
            estimated_value=Decimal('1'), actual_sales=Decimal('0'),
            year='2026', po_award_quarter='Q2')
        stray = CashOutflowRow.objects.create(
            project=other, part='A1', order=0, description='Camera',
            amount=Decimal('1'), vat=Decimal('0'), total_amount=Decimal('1'),
            source_ref=f'line:{self.line.pk}')
        fill_po_numbers(self._po('PO2308-009'))
        stray.refresh_from_db()
        self.assertEqual(stray.po_number, '')

    # ── repeated and multiple ───────────────────────────────────────────────

    def test_running_twice_changes_nothing_the_second_time(self):
        po = self._po('PO2308-010')
        self.assertEqual(fill_po_numbers(po), 1)
        self.assertEqual(fill_po_numbers(po), 0)
        self.assertEqual(self._row(), 'PO2308-010')

    def test_a_second_po_covering_the_same_line_is_added_not_substituted(self):
        """A costing line can be split across orders, and showing only the
        first would understate what is committed against it."""
        fill_po_numbers(self._po('PO2308-011'))
        fill_po_numbers(self._po('PO2308-012'))
        self.assertEqual(self._row(), 'PO2308-011, PO2308-012')

    def test_a_number_is_matched_as_a_whole_entry_not_a_substring(self):
        """PO-1 must not be considered already present in PO-10."""
        self.row.po_number = 'PO-10'
        self.row.save()
        fill_po_numbers(self._po('PO-1'))
        self.assertEqual(self._row(), 'PO-10, PO-1')

    def test_a_number_that_would_overflow_the_field_is_left_out(self):
        """Truncating produces a PO number that does not exist, which is worse
        than showing fewer of them."""
        self.row.po_number = 'X' * 95
        self.row.save()
        fill_po_numbers(self._po('PO2308-013'))
        self.assertEqual(self._row(), 'X' * 95)

    # ── backfill ────────────────────────────────────────────────────────────

    def test_backfill_fills_orders_committed_before_the_link_existed(self):
        po = self._po('PO2308-014', status='draft')
        PurchaseOrder.objects.filter(pk=po.pk).update(status='issued')
        self.assertEqual(self._row(), '')
        self.assertEqual(backfill(), 1)
        self.assertEqual(self._row(), 'PO2308-014')

    def test_backfill_is_idempotent(self):
        self._po('PO2308-015')
        backfill()
        self.assertEqual(backfill(), 0)
        self.assertEqual(self._row(), 'PO2308-015')

    def test_backfill_respects_the_same_refusals(self):
        self.row.po_number = 'do not touch'
        self.row.save()
        self._po('PO2308-016')
        backfill()
        self.assertTrue(self._row().startswith('do not touch'))
