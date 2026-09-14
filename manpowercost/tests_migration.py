"""Tests for the carry-over from the old `manpower` app.

The migration reinterprets existing figures as monthly, which is the single
judgement call in this module. If it is wrong, every migrated sheet is out by
a factor of twelve, so it is asserted directly rather than assumed.

The migration's `forwards` is called against the live app registry. That is
not a historical-model test, but it exercises the same function with the same
`apps.get_model` lookups, and it fails for the same reasons.
"""

from decimal import Decimal

from django.apps import apps as global_apps
from django.test import TestCase

from manpower.models import ManpowerSheet, ManpowerLineItem
from manpowercost.models import CostBasis, ManpowerCostSheet
# The migration module name starts with a digit, so it cannot be imported
# with normal syntax.
from importlib import import_module

forwards = import_module(
    'manpowercost.migrations.0003_seed_basis_and_migrate_manpower').forwards


class CarryOverTests(TestCase):

    def setUp(self):
        # The seeded default from the real migration is already present in the
        # test database; clear it so these tests control their own fixtures.
        CostBasis.objects.all().delete()
        ManpowerCostSheet.objects.all().delete()
        self.old = ManpowerSheet.objects.create(
            title='Legacy sheet', project_reference='REF-1',
            date='2026-01-31', notes='original note')
        ManpowerLineItem.objects.create(
            sheet=self.old, order=0, employee_name='Ali',
            department='PMT', designation='Engineer',
            classification='Eastern',
            gross_salary=Decimal('12000'), iqama_cost=Decimal('854.17'))

    def test_figures_are_carried_over_as_monthly(self):
        """12,000 in the old row was a monthly salary, because that is what
        the old importer wrote. Yearly must therefore be 12x it."""
        forwards(global_apps, None)
        line = ManpowerCostSheet.objects.get(title='Legacy sheet').lines.get()
        self.assertEqual(line.gross_salary, Decimal('12000'))
        self.assertEqual(line.monthly_cost, Decimal('12854.17'))
        self.assertEqual(line.yearly_cost, Decimal('12854.17') * 12)

    def test_the_assumption_is_written_onto_the_sheet(self):
        """It cannot be inferred per row later, so it is recorded, and the
        original note is kept rather than overwritten."""
        forwards(global_apps, None)
        sheet = ManpowerCostSheet.objects.get(title='Legacy sheet')
        self.assertIn('MONTHLY', sheet.notes)
        self.assertIn('original note', sheet.notes)

    def test_classification_is_mapped_to_the_choice_value(self):
        forwards(global_apps, None)
        line = ManpowerCostSheet.objects.get(title='Legacy sheet').lines.get()
        self.assertEqual(line.classification, 'eastern')

    def test_an_unrecognised_classification_becomes_blank_not_invalid(self):
        ManpowerLineItem.objects.create(
            sheet=self.old, order=1, employee_name='Bob',
            classification='Martian', gross_salary=Decimal('1'))
        forwards(global_apps, None)
        line = (ManpowerCostSheet.objects.get(title='Legacy sheet')
                .lines.get(employee_name='Bob'))
        self.assertEqual(line.classification, '')

    def test_running_twice_does_not_duplicate(self):
        """Migrations get replayed; a second run must be a no-op."""
        forwards(global_apps, None)
        forwards(global_apps, None)
        self.assertEqual(
            ManpowerCostSheet.objects.filter(title='Legacy sheet').count(), 1)

    def test_a_default_basis_is_seeded_and_matches_the_real_arithmetic(self):
        """5%/20%/11/176 - what REAL COST computed, not what it labelled."""
        forwards(global_apps, None)
        basis = CostBasis.objects.get(is_default=True)
        self.assertEqual(basis.overhead_pct, Decimal('5.000'))
        self.assertEqual(basis.profit_pct, Decimal('20.000'))
        self.assertEqual(basis.billable_months, Decimal('11.00'))
        self.assertEqual(basis.hours_per_month, Decimal('176.00'))

    def test_the_old_rows_are_left_untouched(self):
        """The old tables are the only copy of this data. A migration that
        both transforms and deletes leaves nothing to check against."""
        forwards(global_apps, None)
        self.assertEqual(ManpowerSheet.objects.count(), 1)
        self.assertEqual(self.old.line_items.count(), 1)
