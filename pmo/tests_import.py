"""Reading the milestone sheets.

The workbook does not have one layout, it has two, and the difference is not
cosmetic: JAZAN starts at column A and carries an extra "Value (SAR)"; MASCO
starts at column B and does not. Both put something at index 9, so a parser
with hardcoded columns reads the wrong cell on one of them and produces an
import that is entirely plausible and entirely wrong.

The rows below are the real headers from both sheets.
"""
from decimal import Decimal

from django.test import TestCase

from pmo.workbook_import import (column_map, match_project, normalise_reference,
                                 parse_sheet, sheet_reference, weight_problems)
from projects.models import Project, ProjectStatus, Region

# MASCO: S.No at index 1, no Value column.
MASCO_ROWS = [
    (None, None, 'Amiral Project -MASCO'),
    (None, 'PROJECT MILESTONES 2026'),
    (None, 'S. No', 'Activity', 'Activity Weightage', 'Completed', 'Pending',
     'Completed Activity Weightage', 'Completion\nDate',
     'Weightage of Pending Activit', 'Invoice Pre'),
    (None, '1', 'Project Documents', 0.1, None, None, None, None, None, None),
    (None, '1.1', 'Engineering Submission', 0.05, 1, 0, 0.05, None, 0, 'Transmittal'),
    (None, '1.2', 'Engineering Approval', 0.05, 1, 0, 0.05, None, 0, 'Transmittal'),
    (None, '2', 'Material Delivery', 0.9, None, None, None, None, None, None),
    (None, '2.1', 'Material At Site', 0.9, 0.4933, 0.5067, 0.44397, None, 0, 'Delivery Note'),
    (None, None, 'Total', 1, None, None, 0.49397),
]

# JAZAN: S.No at index 0, "Completion Status" merged over two columns with
# Completed/Pending written beneath it, and a Value (SAR) column before the
# prerequisite.
JAZAN_ROWS = [
    (None, None, 'Saudi Aramco Jazan'),
    ('S. No', 'Activity', 'Activity Weightage', 'Completion Status', None,
     'Completed Activity Weightage', 'Completion\nDate',
     'Weightage of Pending Activit', 'Value (SAR)', 'Invice Pre-req'),
    (None, None, None, 'Completed', 'Pending'),
    ('1', 'Mobilisation', None, None, None, None, None, None, None, None),
    ('1.1', 'Mobilisation', 0.15, 1, 0, 0.15, None, 0, 0, 'Mobilization plan'),
    ('2', 'NMR 601', None, None, None, None, None, None, None, None),
    ('2.1', 'NMR 601 Submission', 0.85, 1, 0, 0.85, None, 0, 0.03, 'transmittal'),
    (None, 'Total', 1),
]


class ReferenceTests(TestCase):

    def test_the_reference_is_taken_from_the_sheet_name(self):
        self.assertEqual(sheet_reference('P02195-Milestones(JAZAN)'), 'P02195')
        self.assertEqual(sheet_reference('LNA-2308-Milestones(MASCO) '), 'LNA2308')
        self.assertEqual(sheet_reference('LNA-2484-Milestone(AMIRAL SSMS)'), 'LNA2484')

    def test_a_sheet_named_without_a_reference_is_not_a_milestone_sheet(self):
        self.assertIsNone(sheet_reference('Man Power Status'))
        self.assertIsNone(sheet_reference('Sheet3'))

    def test_the_same_reference_written_three_ways_normalises_to_one(self):
        """'LNA-2308', 'LNA 2308' and 'lna2308' are one project. All three
        spellings appear in the workbook."""
        self.assertEqual(normalise_reference('LNA-2308'), 'LNA2308')
        self.assertEqual(normalise_reference('LNA 2308'), 'LNA2308')
        self.assertEqual(normalise_reference('lna2308'), 'LNA2308')


class ColumnMapTests(TestCase):

    def test_both_layouts_are_read_from_their_own_headers(self):
        masco = column_map(MASCO_ROWS, 2)
        jazan = column_map(JAZAN_ROWS, 1)
        self.assertEqual(masco['activity'], 2)
        self.assertEqual(jazan['activity'], 1)
        self.assertEqual(masco['weightage'], 3)
        self.assertEqual(jazan['weightage'], 2)

    def test_the_completed_column_is_found_under_a_merged_header(self):
        """JAZAN writes 'Completion Status' across two columns and puts
        'Completed' and 'Pending' in the row beneath."""
        self.assertEqual(column_map(JAZAN_ROWS, 1)['completed'], 3)

    def test_activity_does_not_claim_activity_weightage(self):
        """Matching by prefix here would map both to the same column and every
        weight would import as text."""
        masco = column_map(MASCO_ROWS, 2)
        self.assertNotEqual(masco['activity'], masco['weightage'])

    def test_the_prerequisite_column_survives_the_extra_value_column(self):
        """Both sheets happen to put something at index 9 — the prerequisite on
        one, and on the other it is only there because Value (SAR) pushed it
        along. Read from the header, not from the position."""
        self.assertEqual(column_map(JAZAN_ROWS, 1)['invoice_prerequisite'], 9)
        self.assertEqual(column_map(MASCO_ROWS, 2)['invoice_prerequisite'], 9)


class ParseTests(TestCase):

    def test_a_masco_style_sheet_parses(self):
        rows = parse_sheet(MASCO_ROWS)
        self.assertEqual([r['level'] for r in rows],
                         ['parent', 'child', 'child', 'parent', 'child'])
        self.assertEqual(rows[1]['weightage'], Decimal('0.0500'))
        self.assertEqual(rows[4]['completed_fraction'], Decimal('0.4933'))

    def test_a_jazan_style_sheet_parses_from_a_different_column_set(self):
        rows = parse_sheet(JAZAN_ROWS)
        self.assertEqual([r['activity'] for r in rows][:2],
                         ['Mobilisation', 'Mobilisation'])
        self.assertEqual(rows[1]['weightage'], Decimal('0.1500'))
        self.assertEqual(rows[1]['invoice_prerequisite'], 'Mobilization plan')

    def test_the_sheets_own_totals_are_not_imported_as_activities(self):
        """They are the arithmetic this replaces."""
        for rows in (MASCO_ROWS, JAZAN_ROWS):
            self.assertNotIn('Total', [r['activity'] for r in parse_sheet(rows)])

    def test_excel_floats_become_exact_decimals(self):
        """0.05 out of Excel is a float. Held as one it would not sum to its
        parent and every sheet would carry a spurious warning."""
        rows = parse_sheet(MASCO_ROWS)
        self.assertEqual(rows[1]['weightage'] + rows[2]['weightage'],
                         Decimal('0.1000'))

    def test_a_sheet_with_no_header_yields_nothing(self):
        self.assertEqual(parse_sheet([(None, 'just a title'), (None, 'notes')]), [])


class WeightProblemTests(TestCase):

    def test_a_masco_style_sheet_is_clean(self):
        self.assertEqual(weight_problems(parse_sheet(MASCO_ROWS)), [])

    def test_a_jazan_style_sheet_with_blank_parents_is_clean(self):
        """The other convention: the parent is blank and the children carry it
        all. Flagging this would warn on half the workbook."""
        self.assertEqual(weight_problems(parse_sheet(JAZAN_ROWS)), [])

    def test_leaves_that_do_not_reach_one_are_reported(self):
        """This is the real defect on the ITC sheet — its activities sum to
        0.973, so that project can never reach 100%."""
        rows = parse_sheet(MASCO_ROWS)
        rows[-1]['weightage'] = Decimal('0.8')
        self.assertTrue(weight_problems(rows))


class MatchProjectTests(TestCase):

    def setUp(self):
        self.region = Region.objects.create(name='KSA', code='MCH', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='open')

    def make(self, reference):
        return Project.objects.create(
            project_name=reference, proposal_reference=reference,
            region=self.region, status=self.status)

    def test_a_reference_matches_its_project_however_it_is_punctuated(self):
        project = self.make('LNA 2308 - Amiral Project')
        found, _reason = match_project('LNA2308', [project])
        self.assertEqual(found, project)

    def test_an_unknown_reference_is_reported_not_invented(self):
        """Creating the project here is how the workbook ended up with the
        same job under five names."""
        found, reason = match_project('LNA9999', [self.make('LNA 2308')])
        self.assertIsNone(found)
        self.assertIn('LNA9999', reason)

    def test_an_ambiguous_reference_is_refused(self):
        """Two projects starting the same way is a question for a person."""
        projects = [self.make('LNA 2308 - Phase 1'), self.make('LNA 2308 - Phase 2')]
        found, reason = match_project('LNA2308', projects)
        self.assertIsNone(found)
        self.assertIn('matches 2 projects', reason)
