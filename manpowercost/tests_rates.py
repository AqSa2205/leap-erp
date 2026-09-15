"""Tests for the cost -> charge-rate derivation.

The anchor test reproduces a real row from the `REAL COST` sheet these rates
came from. That matters more than a synthetic example: the sheet's own labels
said 10% overhead, 25% profit and 10.5 billable months while its formulas used
5%, 20% and 11. Pinning the arithmetic to a known output proves this module
reproduces what was actually charged, not what the labels claimed.
"""

from decimal import Decimal

from django.test import TestCase

from manpowercost.models import (
    CostBasis, ManpowerCostSheet, ManpowerCostLine, Classification,
)
from manpowercost import rates


def real_cost_basis():
    """The parameters the REAL COST sheet actually computed with."""
    return CostBasis(
        name='REAL COST',
        overhead_pct=Decimal('5'),
        profit_pct=Decimal('20'),
        billable_months=Decimal('11'),
        hours_per_month=Decimal('176'),
        working_days_per_month=Decimal('22'),
    )


class DeriveChargeRateTests(TestCase):

    def test_reproduces_the_real_cost_sheet_hydraulic_engineer(self):
        """REAL COST row 12: 22,000/month salary + 76,340/year benefits.

            R12 = 340,340   salary + benefits, yearly
            S12 = 17,017    R * 5%
            T12 = 68,068    R * 20%
            U12 = 425,425
            V12 = 38,675    U / 11
            W12 = 219.7443  V / 176
        """
        monthly_cost = Decimal('340340') / 12
        out = rates.derive_charge_rate(monthly_cost, real_cost_basis())

        self.assertEqual(out['yearly_cost'], Decimal('340340'))
        self.assertEqual(out['overhead'], Decimal('17017'))
        self.assertEqual(out['profit'], Decimal('68068'))
        self.assertEqual(out['yearly_total'], Decimal('425425'))
        self.assertEqual(out['monthly_invoice'], Decimal('38675'))
        self.assertEqual(
            out['hourly_rate'].quantize(Decimal('0.0001')),
            Decimal('219.7443'))

    def test_overhead_and_profit_are_not_compounded(self):
        """Both are taken on the base cost, as the source sheet does.

        Compounding profit onto (cost + overhead) would raise every price
        silently. 100 * 10% = 10 and 100 * 20% = 20, so the total is 130 -
        not 132, which is what compounding would give.
        """
        basis = CostBasis(
            name='b', overhead_pct=Decimal('10'), profit_pct=Decimal('20'),
            billable_months=Decimal('12'), hours_per_month=Decimal('1'),
            working_days_per_month=Decimal('1'))
        out = rates.derive_charge_rate(Decimal('100') / 12, basis)
        self.assertEqual(out['yearly_total'], Decimal('130'))

    def test_zero_markup_returns_cost_spread_over_the_basis(self):
        """With no overhead and no profit the rate is pure cost recovery -
        never below cost, which would mean pricing at a loss by default."""
        basis = CostBasis(
            name='b', overhead_pct=Decimal('0'), profit_pct=Decimal('0'),
            billable_months=Decimal('12'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'))
        monthly = Decimal('17600')
        out = rates.derive_charge_rate(monthly, basis)
        self.assertEqual(out['hourly_rate'], Decimal('100'))
        self.assertGreaterEqual(
            out['hourly_rate'],
            rates.hourly_cost_from_monthly(monthly, basis))

    def test_fewer_billable_months_raises_the_rate(self):
        """11 invoices must recover the same year as 12 would."""
        twelve = CostBasis(
            name='a', overhead_pct=0, profit_pct=0,
            billable_months=Decimal('12'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'))
        eleven = CostBasis(
            name='b', overhead_pct=0, profit_pct=0,
            billable_months=Decimal('11'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'))
        m = Decimal('10000')
        self.assertGreater(
            rates.derive_charge_rate(m, eleven)['hourly_rate'],
            rates.derive_charge_rate(m, twelve)['hourly_rate'])

    def test_no_basis_does_not_invent_a_rate(self):
        out = rates.derive_charge_rate(Decimal('10000'), None)
        self.assertEqual(out['hourly_rate'], Decimal('0'))

    def test_zero_hours_does_not_divide_by_zero(self):
        basis = CostBasis(
            name='b', overhead_pct=0, profit_pct=0,
            billable_months=Decimal('12'), hours_per_month=Decimal('0'),
            working_days_per_month=Decimal('0'))
        out = rates.derive_charge_rate(Decimal('10000'), basis)
        self.assertEqual(out['hourly_rate'], Decimal('0'))
        self.assertEqual(out['daily_rate'], Decimal('0'))


class HourlyCostTests(TestCase):

    def test_hourly_cost_uses_working_hours_not_calendar_hours(self):
        """The replaced app divided a yearly cost by 2920 (365 x 8), billing
        weekends and holidays as working time. On the same monthly cost the
        working-hours figure must come out materially higher."""
        basis = real_cost_basis()
        monthly = Decimal('17600')
        working = rates.hourly_cost_from_monthly(monthly, basis)
        calendar = (monthly * 12) / Decimal('2920')
        self.assertEqual(working, Decimal('100'))
        self.assertGreater(working, calendar)


class MarginTests(TestCase):

    def test_margin_is_on_the_sell_price(self):
        self.assertEqual(
            rates.margin_pct(Decimal('200'), Decimal('150')),
            Decimal('25'))

    def test_margin_is_negative_when_charging_below_cost(self):
        self.assertLess(rates.margin_pct(Decimal('100'), Decimal('150')), 0)

    def test_unset_rate_is_not_an_infinite_loss(self):
        self.assertEqual(rates.margin_pct(Decimal('0'), Decimal('150')),
                         Decimal('0'))


class LineAndSheetCostTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(
            name='Standard', overhead_pct=Decimal('5'),
            profit_pct=Decimal('20'), billable_months=Decimal('11'),
            hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'), is_default=True)
        self.sheet = ManpowerCostSheet.objects.create(
            title='Test', date='2026-09-13', basis=self.basis)

    def _line(self, **kw):
        defaults = dict(sheet=self.sheet, employee_name='Someone',
                        gross_salary=Decimal('10000'))
        defaults.update(kw)
        return ManpowerCostLine.objects.create(**defaults)

    def test_yearly_is_exactly_twelve_times_monthly(self):
        """The identity the replaced app got wrong: it summed monthly figures
        and called the result yearly, reporting 1/12 of the real cost."""
        line = self._line(gross_salary=Decimal('10000'),
                          iqama_cost=Decimal('854.17'))
        self.assertEqual(line.yearly_cost, line.monthly_cost * 12)
        self.assertEqual(line.monthly_cost, Decimal('10854.17'))

    def test_every_declared_component_is_counted(self):
        """Asserted against the declared list, not a hardcoded count, so
        adding a component cannot leave it out of the total."""
        from manpowercost.models import COST_COMPONENT_FIELDS
        line = self._line(**{f: Decimal('1') for f in COST_COMPONENT_FIELDS})
        self.assertEqual(line.monthly_cost,
                         Decimal(len(COST_COMPONENT_FIELDS)))

    def test_saudization_is_part_of_cost(self):
        """12,000 a year per expat head in the source workbook — about 16%
        of its benefits bill. Omitting it understates cost materially."""
        line = self._line(gross_salary=Decimal('0'),
                          saudization_cost=Decimal('1000'))
        self.assertEqual(line.monthly_cost, Decimal('1000'))

    def test_re_entry_visa_is_part_of_cost(self):
        line = self._line(gross_salary=Decimal('0'),
                          re_entry_visa=Decimal('66.67'))
        self.assertEqual(line.monthly_cost, Decimal('66.67'))


    def test_sheet_total_is_the_sum_of_its_lines(self):
        self._line(gross_salary=Decimal('1000'))
        self._line(gross_salary=Decimal('2000'))
        self.assertEqual(self.sheet.monthly_total, Decimal('3000'))
        self.assertEqual(self.sheet.yearly_total, Decimal('36000'))

    def test_a_line_with_no_salary_is_reported_not_silently_totalled(self):
        """This is the defect that made both source workbooks wrong. A total
        is still produced, but the caller is told what it excludes."""
        self._line(gross_salary=Decimal('5000'))
        blank = self._line(employee_name='No salary entered',
                           gross_salary=Decimal('0'))
        summary = rates.sheet_summary(self.sheet)
        self.assertEqual(summary['incomplete_count'], 1)
        self.assertIn(blank, summary['incomplete'])
        self.assertEqual(summary['monthly_total'], Decimal('5000'))

    def test_line_falls_back_to_the_sheets_basis(self):
        line = self._line(gross_salary=Decimal('17600'))
        self.assertEqual(line.hourly_cost(), Decimal('100'))
        self.assertEqual(line.daily_cost(), Decimal('800'))

    def test_sheet_without_a_basis_uses_the_default(self):
        other = ManpowerCostSheet.objects.create(
            title='No basis', date='2026-09-13')
        self.assertEqual(other.effective_basis, self.basis)

    def test_display_name_prefers_the_linked_employee(self):
        line = self._line(employee_name='Typed Name')
        self.assertEqual(line.display_name, 'Typed Name')

    def test_classification_is_a_pricing_dimension(self):
        line = self._line(classification=Classification.SAUDI)
        self.assertEqual(line.get_classification_display(), 'Saudi')


class EffectiveRateTests(TestCase):
    """Which rate actually applies.

    Storing an override and *using* it are different things; asserting only
    the first leaves the quoted price free to disagree with the rate card.
    """

    def setUp(self):
        self.basis = CostBasis.objects.create(
            name='B', overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
            billable_months=Decimal('11'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'))
        from costing.models import ResourceCatalogueItem
        from manpowercost.models import ChargeRate
        self.rate = ChargeRate.objects.create(
            position=ResourceCatalogueItem.objects.create(name='Eng'),
            basis=self.basis, monthly_cost=Decimal('20000'))

    def test_without_an_override_the_derived_rate_is_used(self):
        expected = rates.derive_charge_rate(
            self.rate.monthly_cost, self.basis)['hourly_rate']
        self.assertEqual(rates.effective_hourly_rate(self.rate), expected)
        self.assertGreater(expected, Decimal('0'))

    def test_an_override_wins_over_the_derived_rate(self):
        derived = rates.derive_charge_rate(
            self.rate.monthly_cost, self.basis)['hourly_rate']
        self.rate.manual_rate = Decimal('999.5')
        self.assertEqual(rates.effective_hourly_rate(self.rate),
                         Decimal('999.5'))
        self.assertNotEqual(rates.effective_hourly_rate(self.rate), derived)

    def test_an_override_of_zero_is_honoured_not_treated_as_unset(self):
        """0 is falsy; a truthiness check here would silently fall back to
        the derived rate and quietly bill for something meant to be free."""
        self.rate.manual_rate = Decimal('0')
        self.assertEqual(rates.effective_hourly_rate(self.rate), Decimal('0'))

    def test_margin_follows_the_override(self):
        self.rate.manual_rate = Decimal('1')
        cost = rates.hourly_cost_from_monthly(self.rate.monthly_cost, self.basis)
        self.assertLess(
            rates.margin_pct(rates.effective_hourly_rate(self.rate), cost), 0)


class CostBasisTests(TestCase):

    def test_hours_per_day_is_derived_not_stored(self):
        basis = CostBasis(hours_per_month=Decimal('176'),
                          working_days_per_month=Decimal('22'))
        self.assertEqual(basis.hours_per_day, Decimal('8'))

    def test_default_basis_is_found(self):
        CostBasis.objects.create(name='a', is_default=False)
        d = CostBasis.objects.create(name='b', is_default=True)
        self.assertEqual(CostBasis.get_default(), d)

    def test_marking_a_default_demotes_the_previous_one(self):
        """A migration already seeds a default, so this is the normal case,
        not an edge one. Two defaults would make get_default() depend on
        insertion order and every derived rate with it."""
        first = CostBasis.objects.create(name='first', is_default=True)
        second = CostBasis.objects.create(name='second', is_default=True)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertEqual(CostBasis.get_default(), second)
        self.assertEqual(CostBasis.objects.filter(is_default=True).count(), 1)


class SalaryBreakdownTests(TestCase):
    """Basic / Housing / Transport are held for GOSI and end-of-service,
    which are calculated from basic (and housing) in KSA. They are detail on
    gross_salary, never added to it — doing both would double the pay."""

    def setUp(self):
        self.basis = CostBasis.objects.create(name='B', is_default=True)
        self.sheet = ManpowerCostSheet.objects.create(
            title='T', date='2026-09-13', basis=self.basis)

    def _line(self, **kw):
        return ManpowerCostLine.objects.create(sheet=self.sheet, **kw)

    def test_breakdown_is_not_added_on_top_of_gross(self):
        line = self._line(gross_salary=Decimal('22000'),
                          basic_salary=Decimal('16800'),
                          housing_allowance=Decimal('4200'),
                          transport_allowance=Decimal('1000'))
        self.assertEqual(line.monthly_cost, Decimal('22000'))

    def test_a_breakdown_that_agrees_reports_no_mismatch(self):
        line = self._line(gross_salary=Decimal('22000'),
                          basic_salary=Decimal('16800'),
                          housing_allowance=Decimal('4200'),
                          transport_allowance=Decimal('1000'))
        self.assertIsNone(line.salary_breakdown_mismatch)

    def test_a_breakdown_that_disagrees_is_surfaced(self):
        line = self._line(gross_salary=Decimal('22000'),
                          basic_salary=Decimal('16800'))
        self.assertEqual(line.salary_breakdown_mismatch, Decimal('-5200'))

    def test_no_breakdown_is_not_a_mismatch(self):
        line = self._line(gross_salary=Decimal('22000'))
        self.assertIsNone(line.salary_breakdown_mismatch)


class EmployeeAgeTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(name='B', is_default=True)
        self.sheet = ManpowerCostSheet.objects.create(
            title='T', date='2026-09-13', basis=self.basis)

    def test_age_is_derived_from_date_of_birth(self):
        from datetime import date
        line = ManpowerCostLine.objects.create(
            sheet=self.sheet, date_of_birth=date(1990, 1, 1))
        self.assertGreaterEqual(line.employee_age, 30)

    def test_no_date_of_birth_gives_no_age(self):
        line = ManpowerCostLine.objects.create(sheet=self.sheet)
        self.assertIsNone(line.employee_age)


class CostGroupingTests(TestCase):
    """The on-screen grouping must cover the model exactly.

    The form is built from COST_GROUPS, so a component missing from it has
    no input anywhere - it would still count towards the total while being
    impossible to enter or correct, which is the quiet-wrong-number failure
    this whole module exists to prevent.
    """

    def test_every_component_appears_exactly_once(self):
        from manpowercost.models import COST_COMPONENT_FIELDS, COST_GROUPS
        grouped = [f for _name, fields in COST_GROUPS for f in fields]
        self.assertEqual(sorted(grouped), sorted(COST_COMPONENT_FIELDS))
        self.assertEqual(len(grouped), len(set(grouped)),
                         "a component is listed in two groups")

    def test_every_grouped_field_has_a_label(self):
        from manpowercost.models import COMPONENT_LABELS, COST_GROUPS
        for _name, fields in COST_GROUPS:
            for field in fields:
                self.assertIn(field, COMPONENT_LABELS)

    def test_the_salary_split_is_not_in_any_cost_group(self):
        """It is detail on gross pay, not an addition to it. Grouping it
        with the components is how the pay would come to be counted twice."""
        from manpowercost.models import COST_GROUPS
        grouped = {f for _name, fields in COST_GROUPS for f in fields}
        for field in ("basic_salary", "housing_allowance",
                      "transport_allowance"):
            self.assertNotIn(field, grouped)
