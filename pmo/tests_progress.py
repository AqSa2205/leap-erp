"""Completion, cash and the weight rules.

The fixture is the real MASCO sheet (LNA-2308) from the projects overview
workbook, because its total is a figure somebody has already checked by hand:
0.522655. A fixture invented here could agree with the code and still be wrong
about the business.

What is worth guarding is what the workbook got wrong quietly: totals that
summed the wrong rows, weights that never added up, and a last-updated column
that always read as today.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from finance.models import PaymentMilestone, ProjectFinance
from pmo.models import MilestoneProgressEntry, ProjectMilestone
from pmo.progress import (board_row, cash_in, cash_out, leaves,
                          project_completion, total_weightage,
                          validate_weightages)
from projects.models import Project, ProjectStatus, Region

User = get_user_model()

# (parent weight, [(child activity, child weight, completed fraction)])
MASCO = [
    ('Project Documents and Engineering', '0.10', [
        ('Engineering Documents Submission', '0.05', '1'),
        ('Complete engineering approval',    '0.05', '1'),
    ]),
    ('Purchase Order issuance', '0.25', [
        ('Progressive Invoice (Weightage Wise)', '0.25', '1'),
    ]),
    ('Material Delivery At Site', '0.35', [
        ('Material Delivery At Site', '0.35', '0.4933'),
    ]),
    ('Installation Start/Mechanical completion', '0.17', [
        ('Complete Installation Of System', '0.17', '0'),
    ]),
    ('Final Testing/Commissioning', '0.08', [
        ('Testing and Commissioning', '0.08', '0'),
    ]),
    ('Retention', '0.05', [
        ('Retention', '0.05', '0'),
    ]),
]

# Checked by hand on the sheet: 0.05 + 0.05 + 0.25 + (0.35 × 0.4933).
MASCO_COMPLETION = Decimal('0.522655')


class MilestoneFixtureMixin:

    def build_project(self, reference='LNA-2308'):
        region = Region.objects.create(
            name='KSA', code=reference[:6], currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='open')
        project = Project.objects.create(
            project_name='Amiral Project - MASCO',
            proposal_reference=reference, region=region, status=status)
        for order, (name, weight, children) in enumerate(MASCO, start=1):
            parent = ProjectMilestone.objects.create(
                project=project, order=order, activity=name,
                weightage=Decimal(weight))
            for child_order, (child_name, child_weight, done) in enumerate(children, start=1):
                ProjectMilestone.objects.create(
                    project=project, parent=parent, order=child_order,
                    activity=child_name, weightage=Decimal(child_weight),
                    completed_fraction=Decimal(done))
        return project


class CompletionTests(MilestoneFixtureMixin, TestCase):

    def setUp(self):
        self.project = self.build_project()

    def test_completion_matches_the_figure_on_the_sheet(self):
        self.assertEqual(project_completion(self.project), MASCO_COMPLETION)

    def test_only_leaves_carry_weight(self):
        """A parent's weight is its children's. Counting both would double
        every project to 2.00 and put completion at half what it is."""
        self.assertEqual(total_weightage(self.project), Decimal('1.0000'))
        self.assertEqual(len(leaves(self.project)), 7)

    def test_progress_typed_onto_a_parent_row_is_ignored(self):
        """Progress belongs to the leaves. A figure on a parent — a mistyped
        cell, or an import that filled the summary row — must not be counted
        on top of the children it summarises, or the project reads as more
        complete than it is.

        Worth its own test: every parent in the fixture sits at 0, so summing
        parents as well would give the same answer and prove nothing.
        """
        parent = self.project.milestones.get(
            activity='Purchase Order issuance', parent__isnull=True)
        parent.completed_fraction = Decimal('1')
        parent.save()
        self.assertEqual(project_completion(self.project), MASCO_COMPLETION)

    def test_a_parent_is_never_a_leaf(self):
        parents = {r.parent_id for r in self.project.milestones.all() if r.parent_id}
        self.assertFalse(parents & {row.pk for row in leaves(self.project)})

    def test_completed_and_pending_always_account_for_the_whole(self):
        """The identity the workbook's total row assumed and never checked:
        its Completed and Pending columns were computed independently, so
        nothing would have caught them disagreeing."""
        for row in leaves(self.project):
            self.assertEqual(
                row.completed_weightage + row.pending_weightage,
                row.total_weightage)

    def test_the_identity_holds_for_a_parent_too(self):
        parent = self.project.milestones.get(activity='Project Documents and Engineering')
        self.assertEqual(
            parent.completed_weightage + parent.pending_weightage,
            parent.total_weightage)

    def test_a_parent_aggregates_its_children(self):
        parent = self.project.milestones.get(activity='Project Documents and Engineering')
        self.assertEqual(parent.completed_weightage, Decimal('0.1000'))
        self.assertEqual(parent.total_weightage, Decimal('0.1000'))

    def test_a_project_with_no_milestones_is_zero_not_an_error(self):
        """The board renders every project, including ones nobody has built a
        WBS for yet."""
        empty = self.build_project(reference='LNA-9999')
        empty.milestones.all().delete()
        self.assertEqual(project_completion(empty), Decimal('0'))


class WeightageValidationTests(MilestoneFixtureMixin, TestCase):

    def setUp(self):
        self.project = self.build_project()

    def test_a_correct_sheet_reports_nothing(self):
        self.assertEqual(validate_weightages(self.project), [])

    def test_children_that_do_not_sum_to_their_parent_are_reported(self):
        """Weight is a fraction of the project, not of the parent — on this
        sheet a parent of 0.1 has two children of 0.05."""
        child = self.project.milestones.filter(
            activity='Engineering Documents Submission').get()
        child.weightage = Decimal('0.04')
        child.save()
        problems = validate_weightages(self.project)
        children = [p for p in problems if p['kind'] == 'children']
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]['expected'], Decimal('0.1000'))

    def test_top_level_activities_that_do_not_sum_to_one_are_reported(self):
        """A project whose activities sum to 0.9 can never reach 100% and the
        workbook would only ever have shown it as 'nearly done'."""
        # Parent and child share a name on this sheet ("Retention",
        # "Material Delivery At Site"), so the parent is selected by shape.
        parent = self.project.milestones.get(
            activity='Retention', parent__isnull=True)
        parent.delete()                       # cascades to its children
        problems = validate_weightages(self.project)
        self.assertTrue(any(p['kind'] == 'project' for p in problems))

    def test_a_blank_parent_is_a_convention_not_a_mistake(self):
        """The workbook uses two: on the MASCO sheet the parent carries 0.10
        and its children divide it; on the ZULF sheets the parent is left blank
        and the children carry all of it. Flagging the second would put a
        permanent warning on half the projects, which trains people to ignore
        the warning entirely.
        """
        zulf = self.build_project(reference='LNA-2255')
        zulf.milestones.all().delete()
        for order, (name, weight) in enumerate(
                [('Mobilization', '0.45'), ('Purchase Order', '0.20'),
                 ('Equipment Delivery', '0.20'), ('Site activities', '0.11'),
                 ('Commissioning', '0.04')], start=1):
            parent = ProjectMilestone.objects.create(
                project=zulf, order=order, activity=name,
                weightage=Decimal('0'))            # blank, as on the sheet
            ProjectMilestone.objects.create(
                project=zulf, parent=parent, order=1,
                activity=f'{name} detail', weightage=Decimal(weight))
        self.assertEqual(validate_weightages(zulf), [])
        self.assertEqual(total_weightage(zulf), Decimal('1.0000'))

    def test_a_project_split_into_thirds_is_not_flagged(self):
        """Three equal parts of a whole cannot be written exactly at four
        decimal places: 0.3333 × 3 is 0.9999. That last-digit gap is how
        somebody correctly enters thirds, not a mistake, and flagging it would
        train people to ignore the warning.

        These are Decimals at fixed precision, so the arithmetic is exact —
        the tolerance is for this, not for floating-point error.
        """
        thirds = self.build_project(reference='LNA-3333')
        thirds.milestones.all().delete()
        parent = ProjectMilestone.objects.create(
            project=thirds, order=1, activity='Phases', weightage=Decimal('1'))
        for i in range(3):
            ProjectMilestone.objects.create(
                project=thirds, parent=parent, order=i + 1,
                activity=f'Phase {i + 1}', weightage=Decimal('0.3333'))
        self.assertEqual(validate_weightages(thirds), [])

    def test_a_gap_bigger_than_the_last_digit_is_still_flagged(self):
        """The tolerance forgives one ten-thousandth, not a missing activity."""
        child = self.project.milestones.filter(
            activity='Engineering Documents Submission').get()
        child.weightage = Decimal('0.0490')
        child.save()
        self.assertTrue(validate_weightages(self.project))


class CashTests(MilestoneFixtureMixin, TestCase):

    def setUp(self):
        self.project = self.build_project()
        self.finance = ProjectFinance.objects.create(
            project=self.project, po_value=Decimal('13125000.00'))

    def test_cash_in_counts_only_money_actually_received(self):
        """An invoice submitted is not cash in. The workbook's Cash In column
        blurred the two, so a project looked paid the day it invoiced."""
        PaymentMilestone.objects.create(
            project_finance=self.finance, name='Advance', order=1,
            submitted_pct=Decimal('20'),
            invoice_submission_date=date(2026, 1, 1),
            actual_payment_receive_date=date(2026, 2, 1))
        PaymentMilestone.objects.create(
            project_finance=self.finance, name='On delivery', order=2,
            submitted_pct=Decimal('30'),
            invoice_submission_date=date(2026, 3, 1))     # invoiced, not paid
        self.assertEqual(cash_in(self.project), Decimal('2625000.00'))

    def test_cash_in_is_zero_when_the_project_has_no_finance_record(self):
        """Reverse one-to-one raises rather than returning None, and it
        subclasses AttributeError so getattr's default applies — worth pinning,
        because the board renders projects that never reached finance."""
        other = self.build_project(reference='LNA-8888')
        self.assertEqual(cash_in(other), Decimal('0'))

    def test_cash_out_is_zero_with_no_orders(self):
        self.assertEqual(cash_out(self.project), Decimal('0'))


class BoardRowTests(MilestoneFixtureMixin, TestCase):

    def setUp(self):
        self.project = self.build_project()
        self.user = User.objects.create_user('pm-user', password='x')

    def test_the_row_carries_the_completion_figure(self):
        row = board_row(self.project)
        self.assertEqual(row['completion'], MASCO_COMPLETION)
        self.assertEqual(row['completion_pct'], MASCO_COMPLETION * 100)

    def test_last_update_is_none_before_anybody_updates(self):
        """Rather than today. The workbook used TODAY() here, so a project
        untouched since March still reported as updated this morning."""
        self.assertIsNone(board_row(self.project)['last_update'])

    def test_last_update_is_the_most_recent_entry(self):
        row = leaves(self.project)[0]
        old = MilestoneProgressEntry.objects.create(
            milestone=row, completed_fraction=Decimal('0.2'), recorded_by=self.user)
        MilestoneProgressEntry.objects.filter(pk=old.pk).update(
            recorded_at=timezone.now() - timedelta(days=30))
        recent = MilestoneProgressEntry.objects.create(
            milestone=row, completed_fraction=Decimal('0.4'), recorded_by=self.user)
        self.assertEqual(board_row(self.project)['last_update'], recent.recorded_at)

    def test_weightage_problems_travel_with_the_row(self):
        """A board that shows a completion figure without saying the weights
        behind it are broken is the workbook's failure repeated."""
        child = self.project.milestones.filter(
            activity='Engineering Documents Submission').get()
        child.weightage = Decimal('0.04')
        child.save()
        self.assertTrue(board_row(self.project)['weightage_problems'])
