"""Project Costing (Integrated): calculations, inputs, the page and its export."""
import io
from decimal import Decimal

import openpyxl
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Project, ProjectStatus, Region

from .forms import ManpowerCostingHeaderForm, ProjectCostingInputsForm
from .models import (DesignationManpowerLine, EmploymentCostAssumptions,
                     FirstYearMaintenanceLine, ManpowerCostingHeader)
from .project_costing import (GROUP_STAFF, GROUP_TECHNICIAN, accommodation_group,
                              build_project_costing, split_line)

CENT = Decimal('0.01')


def q(value):
    return Decimal(value).quantize(CENT)


# The source workbook's sheet: 21 designations and its B/C/D lines.
PEOPLE = [
    ('Technical Consultancy (Shareholder / Partner)', 'EX-1', 'saudi', 45000, 2),
    ('Technical Consultancy', 'EX-2', 'expat', 32000, 3),
    ('Project Coordinator (Senior)', 'EX-3', 'expat', 25000, 4),
    ('HSE (Manager)', 'EX-4', 'saudi', 18000, 2),
    ('Document Controller (Lead)', 'A-1', 'expat', 14000, 3),
    ('Project Coordinator', 'A-2', 'saudi', 10000, 5),
    ('Document Controller', 'A-3', 'saudi', 7000, 6),
    ('Hydraulic Engineer', 'E-1', 'expat', 18000, 4),
    ('Engineer', 'E-2', 'expat', 14000, 5),
    ('HSE (Engineer / Officer)', 'E-2', 'expat', 14000, 2),
    ('Designers', 'E-3', 'expat', 10000, 8),
    ('Electrical Technician', 'T-1', 'expat', 9000, 6),
    ('Pipe Fitter', 'T-2', 'expat', 7000, 10),
    ('Painter', 'T-3', 'expat', 5500, 6),
    ('Carpenter', 'T-3', 'expat', 5500, 6),
    ('Helper (Skilled)', 'O-1', 'saudi', 4500, 9),
    ('Helper', 'O-2', 'saudi', 3500, 14),
    ('WPR (Work Permit Receiver)', 'T-1', 'expat', 9000, 1),
    ('Vacation Replacement', 'T-3', 'expat', 5500, 1),
    ('Fence Cleaning Labor', 'O-2', 'saudi', 3500, 1),
    ('Supporting Labor', 'O-2', 'saudi', 3500, 1),
]
LINES = [
    ('B', 'Transfer / Visa Fee (per expat)', 'one_time', 6000, 59),
    ('B', 'Profession Change', 'one_time', 3000, 1),
    ('B', 'Mobilization & Demobilization', 'one_time', 250000, 1),
    ('C', 'Pickup Truck (Rental)', 'monthly', 5500, 1),
    ('C', 'Fuel', 'monthly', 2500, 1),
    ('C', 'Scaffolding / Manlift Rental', 'monthly', 2500, 1),
    ('C', 'Consumables / Support from HO or OEM', 'monthly', 1000, 1),
    ('D', 'Tools / Equipment / Tester', 'one_time', 5000, 1),
    ('D', 'Fire Retardant Uniform / PPE', 'one_time', 1000, 99),
]


def person(designation, grade, nationality, salary, heads, order=0):
    return DesignationManpowerLine(
        designation=designation, grade_code=grade, category=grade.split('-')[0],
        nationality=nationality, monthly_salary=Decimal(salary), headcount=heads, order=order)


def fym(section, description, nature, amount, qty, order=0):
    return FirstYearMaintenanceLine(
        section=section, description=description, nature_of_expense=nature,
        amount=Decimal(amount), qty=Decimal(qty), order=order)


def header(months=12, margin='28', vat='15', **extra):
    return ManpowerCostingHeader(contract_duration_months=months,
                                 profit_margin_pct=Decimal(margin) if margin is not None else None,
                                 vat_rate=Decimal(vat), **extra)


def workbook(months=12, margin='28'):
    return build_project_costing(
        header(months, margin), EmploymentCostAssumptions(),
        [person(*p, order=i) for i, p in enumerate(PEOPLE)],
        [fym(*l, order=i) for i, l in enumerate(LINES)])


# ── 1. The workbook's figures ────────────────────────────────────────────────

class WorkbookFiguresTests(SimpleTestCase):

    def test_manpower_heads_and_salaries(self):
        d = workbook()
        self.assertEqual(d['heads_total'], 99)
        self.assertEqual(q(d['salaries_total']), Decimal('1007000.00'))
        self.assertEqual(len(d['manpower_rows']), 21)

    def test_each_statutory_and_accommodation_line(self):
        expected = ['50887.50', '20625.00', '12291.67', '65250.00', '62500.00',
                    '83883.10', '13850.00', '36953.75']
        self.assertEqual([q(s['monthly']) for s in workbook()['statutory']],
                         [Decimal(x) for x in expected])

    def test_accommodation_heads_split_staff_and_technician(self):
        statutory = workbook()['statutory']
        self.assertEqual(statutory[3]['heads'], 29)  # expat staff
        self.assertEqual(statutory[4]['heads'], 30)  # expat technicians
        self.assertEqual(statutory[0]['heads'], 59)  # all expats (Iqama)

    def test_section_totals(self):
        d = workbook()
        self.assertEqual(q(d['total_a']), Decimal('1353241.02'))
        self.assertEqual([q(s['total']) for s in d['sections']],
                         [Decimal('607000.00'), Decimal('11500.00'), Decimal('104000.00')])

    def test_grand_total_and_pricing(self):
        d = workbook()
        self.assertEqual(q(d['grand_total']), Decimal('17087892.20'))
        self.assertEqual(q(d['monthly_cost']), Decimal('1423991.02'))
        self.assertEqual(q(d['daily_cost']), Decimal('46816.14'))
        self.assertEqual(q(d['contract_value']), Decimal('23733183.61'))
        self.assertEqual(q(d['profit']), Decimal('6645291.41'))
        self.assertEqual(q(d['contract_value_incl_vat']), Decimal('27293161.15'))


# ── 2. Rules and edge cases ──────────────────────────────────────────────────

class CalculationRuleTests(SimpleTestCase):

    def build(self, people=(), lines=(), **h):
        return build_project_costing(header(**h), EmploymentCostAssumptions(),
                                     list(people), list(lines))

    def test_accommodation_group_comes_from_the_grade_category(self):
        for category in ('EX', 'A', 'E'):
            self.assertEqual(accommodation_group(category), GROUP_STAFF, category)
        for category in ('T', 'O'):
            self.assertEqual(accommodation_group(category), GROUP_TECHNICIAN, category)

    def test_saudis_get_no_iqama_tickets_or_accommodation(self):
        d = self.build([person('Helper', 'O-2', 'saudi', 3500, 4), person('Engineer', 'E-1', 'saudi', 9000, 2)])
        iqama, medical, ticket, staff, tech = d['statutory'][:5]
        self.assertEqual((iqama['monthly'], ticket['monthly'], staff['monthly'], tech['monthly']), (0, 0, 0, 0))
        self.assertEqual(medical['heads'], 6)  # medical is for everyone

    def test_gosi_splits_by_nationality(self):
        d = self.build([person('Expat', 'E-1', 'expat', 10000, 1), person('Saudi', 'E-2', 'saudi', 10000, 1)])
        self.assertEqual(q(d['statutory'][6]['monthly']), Decimal('200.00'))   # 2% of expat 10,000
        self.assertEqual(q(d['statutory'][7]['monthly']), Decimal('1175.00'))  # 11.75% of Saudi 10,000

    def test_longer_contract_multiplies_recurring_only(self):
        twelve, twenty_four = workbook(12), workbook(24)
        a12, b12, c12, d12 = (r['contract_total'] for r in twelve['totals'])
        a24, b24, c24, d24 = (r['contract_total'] for r in twenty_four['totals'])
        self.assertEqual((q(a24), q(c24)), (q(a12 * 2), q(c12 * 2)))
        self.assertEqual((b24, d24), (b12, d12))
        self.assertEqual(q(twenty_four['grand_total']), Decimal('33464784.40'))

    def test_daily_cost_uses_the_contract_days(self):
        d = workbook(24)
        self.assertEqual(d['daily_cost'], d['grand_total'] / (Decimal('24') * 365 / 12))

    def test_one_month_contract(self):
        d = workbook(1)
        self.assertEqual(d['monthly_cost'], d['grand_total'])

    def test_annual_line_counts_one_twelfth_per_month(self):
        monthly, one_time = split_line(fym('C', 'Licence', 'annual', 12000, 1))
        self.assertEqual((monthly, one_time), (Decimal('1000'), Decimal('0')))

    def test_a_line_annualises_by_its_own_nature_not_its_section(self):
        monthly, one_time = split_line(fym('B', 'Monthly item in B', 'monthly', 500, 2))
        self.assertEqual((monthly, one_time), (Decimal('1000'), Decimal('0')))
        monthly, one_time = split_line(fym('C', 'One-off in C', 'one_time', 700, 1))
        self.assertEqual((monthly, one_time), (Decimal('0'), Decimal('700')))

    def test_no_margin_prices_at_cost(self):
        d = workbook(margin=None)
        self.assertEqual(d['contract_value'], d['grand_total'])
        self.assertEqual(d['profit'], 0)
        self.assertFalse(d['margin_set'])

    def test_margin_above_99_percent_is_clamped(self):
        d = workbook(margin='150')
        self.assertEqual(d['margin_pct'], Decimal('99'))
        self.assertEqual(q(d['contract_value']), q(d['grand_total'] / Decimal('0.01')))

    def test_empty_project_is_all_zero(self):
        d = self.build()
        self.assertEqual((d['heads_total'], d['total_a'], d['grand_total'], d['daily_cost'], d['contract_value']),
                         (0, 0, 0, 0, 0))
        self.assertEqual([s['lines'] for s in d['sections']], [[], [], []])

    def test_zero_heads_and_zero_salary_add_nothing(self):
        d = self.build([person('Vacant', 'E-1', 'expat', 15000, 0), person('Unpaid', 'E-2', 'expat', 0, 3)])
        self.assertEqual(d['salaries_total'], 0)
        self.assertEqual(d['heads_total'], 3)
        self.assertEqual(q(d['statutory'][0]['monthly']), Decimal('2587.50'))  # Iqama still due for 3 expats

    def test_rows_are_numbered_and_ordered_by_category_then_grade(self):
        d = self.build([person('Helper', 'O-1', 'saudi', 1, 1), person('Lead', 'E-10', 'expat', 1, 1),
                        person('Engineer', 'E-2', 'expat', 1, 1), person('Director', 'EX-1', 'saudi', 1, 1)])
        self.assertEqual([r['line'].grade_code for r in d['manpower_rows']], ['EX-1', 'E-2', 'E-10', 'O-1'])
        self.assertEqual([r['no'] for r in d['manpower_rows']], [1, 2, 3, 4])
        self.assertEqual(d['statutory'][0]['no'], 5)


# ── 3. Inputs form ───────────────────────────────────────────────────────────

class InputsFormTests(SimpleTestCase):

    def form(self, **changes):
        data = {'contract_duration_months': '12', 'accommodation_staff_annual': '27000',
                'accommodation_technician_annual': '25000'}
        data.update(changes)
        return ProjectCostingInputsForm(data, instance=ManpowerCostingHeader())

    def test_valid_values(self):
        for months in ('1', '12', '120'):
            self.assertTrue(self.form(contract_duration_months=months).is_valid(), months)
        self.assertTrue(self.form(accommodation_staff_annual='0').is_valid())

    def test_duration_out_of_range_or_blank_is_rejected(self):
        for months in ('0', '121', ''):
            self.assertIn('contract_duration_months', self.form(contract_duration_months=months).errors, months)

    def test_negative_accommodation_is_rejected(self):
        form = self.form(accommodation_technician_annual='-1')
        self.assertFalse(form.is_valid())
        self.assertIn('accommodation_technician_annual', form.errors)


# ── 4. The page, saving, access and export ───────────────────────────────────

class ProjectCostingPageTests(TestCase):

    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.site_role, _ = Role.objects.get_or_create(name=Role.SITE_MANAGER)
        self.dev_role, _ = Role.objects.get_or_create(name=Role.DEVELOPER)
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.west = Region.objects.create(name='West', code='WST', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(project_name='Test Project', status=self.status, region=self.region)
        self.pm = User.objects.create_user('pc_pm', password='pw', role=self.pm_role, region=self.region)
        self.site = User.objects.create_user('pc_site', password='pw', role=self.site_role, region=self.region)
        self.dev = User.objects.create_user('pc_dev', password='pw', role=self.dev_role, region=self.region)
        self.engineer = DesignationManpowerLine.objects.create(
            project=self.project, designation='Engineer', grade_code='E-1', category='E',
            nationality='expat', headcount=2, monthly_salary=Decimal('10000'))
        DesignationManpowerLine.objects.create(
            project=self.project, designation='Helper', grade_code='O-2', category='O',
            nationality='saudi', headcount=1, monthly_salary=Decimal('3000'))
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section='C', description='Pickup Truck', nature_of_expense='monthly',
            amount=Decimal('5500'), qty=1)
        self.url = reverse('pmo:mpc_project_costing', args=[self.project.pk])
        self.export_url = reverse('pmo:mpc_project_costing_export_excel', args=[self.project.pk])

    def other_project(self, name, ref, region=None):
        return Project.objects.create(project_name=name, proposal_reference=ref,
                                      status=self.status, region=region or self.region)

    def payload(self, **changes):
        data = {'contract_duration_months': '12', 'accommodation_staff_annual': '27000',
                'accommodation_technician_annual': '25000'}
        data.update(changes)
        return data

    def sheet_values(self, response):
        ws = openpyxl.load_workbook(io.BytesIO(response.content)).active
        return [[c.value for c in row] for row in ws.iter_rows()]

    # Access

    def test_logged_out_is_sent_to_login(self):
        for url in (self.url, self.export_url):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn('next=', resp.url, url)

    def test_no_delivery_access_is_denied_page_export_and_save(self):
        self.client.force_login(self.dev)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.export_url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload(contract_duration_months='24')).status_code, 403)
        self.assertFalse(ManpowerCostingHeader.objects.filter(project=self.project).exists())

    def test_project_in_another_region_is_not_found(self):
        west = self.other_project('West Job', 'REF-PC-WEST', self.west)
        self.client.force_login(self.pm)
        for name in ('pmo:mpc_project_costing', 'pmo:mpc_project_costing_export_excel'):
            self.assertEqual(self.client.get(reverse(name, args=[west.pk])).status_code, 404, name)
        resp = self.client.post(reverse('pmo:mpc_project_costing', args=[west.pk]), self.payload())
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(ManpowerCostingHeader.objects.filter(project=west).exists())

    def test_site_manager_can_view_but_not_edit(self):
        self.client.force_login(self.site)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'GRAND TOTAL COST')
        self.assertNotContains(resp, 'name="contract_duration_months"')
        self.assertNotContains(resp, 'name="accommodation_staff_annual"')
        self.assertEqual(self.client.post(self.url, self.payload(contract_duration_months='24')).status_code, 403)
        self.assertFalse(ManpowerCostingHeader.objects.filter(project=self.project).exists())

    def test_site_manager_does_not_see_the_bid_value(self):
        self.client.force_login(self.site)
        resp = self.client.get(self.url)
        for hidden in ('Proposed Contract Value', 'Target Profit Margin', '>Profit<'):
            self.assertNotContains(resp, hidden)
        cells = {v for row in self.sheet_values(self.client.get(self.export_url)) for v in row}
        self.assertIn('GRAND TOTAL COST', cells)
        for hidden in ('Proposed Contract Value (excl. VAT)', 'Profit', 'Target Profit Margin %'):
            self.assertNotIn(hidden, cells)

    def test_project_manager_sees_the_bid_value(self):
        self.client.force_login(self.pm)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Proposed Contract Value (excl. VAT)')
        self.assertContains(resp, 'name="contract_duration_months"')

    # Saving

    def test_project_manager_saves_duration_and_accommodation(self):
        self.client.force_login(self.pm)
        resp = self.client.post(self.url, self.payload(contract_duration_months='24',
                                                       accommodation_staff_annual='30000'))
        self.assertRedirects(resp, self.url)
        h = ManpowerCostingHeader.objects.get(project=self.project)
        self.assertEqual((h.contract_duration_months, h.accommodation_staff_annual), (24, Decimal('30000')))
        self.assertEqual(h.updated_by, self.pm)

    def test_invalid_save_changes_nothing_and_shows_saved_figures(self):
        ManpowerCostingHeader.objects.create(project=self.project, contract_duration_months=12)
        self.client.force_login(self.pm)
        resp = self.client.post(self.url, self.payload(contract_duration_months='0'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('contract_duration_months', resp.context['form'].errors)
        self.assertEqual(resp.context['months'], 12)
        self.assertEqual(ManpowerCostingHeader.objects.get(project=self.project).contract_duration_months, 12)

    def test_saving_does_not_touch_margin_or_vat(self):
        ManpowerCostingHeader.objects.create(project=self.project, profit_margin_pct=Decimal('28'),
                                             vat_rate=Decimal('15'))
        self.client.force_login(self.pm)
        self.client.post(self.url, self.payload(contract_duration_months='18'))
        h = ManpowerCostingHeader.objects.get(project=self.project)
        self.assertEqual((h.profit_margin_pct, h.vat_rate, h.contract_duration_months),
                         (Decimal('28'), Decimal('15'), 18))

    def test_viewing_does_not_create_a_header_row(self):
        self.client.force_login(self.pm)
        self.client.get(self.url)
        self.client.get(self.export_url)
        self.assertFalse(ManpowerCostingHeader.objects.filter(project=self.project).exists())

    def test_first_year_maintenance_form_is_unchanged(self):
        for name in ('contract_duration_months', 'accommodation_staff_annual', 'accommodation_technician_annual'):
            self.assertNotIn(name, ManpowerCostingHeaderForm._meta.fields)

    # What the page shows

    def test_first_year_maintenance_section_a_is_not_counted_twice(self):
        self.client.force_login(self.pm)
        before = self.client.get(self.url).context['grand_total']
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section='A', description='Typed manpower', nature_of_expense='monthly',
            amount=Decimal('999999'), qty=1)
        self.assertEqual(self.client.get(self.url).context['grand_total'], before)

    def test_manpower_is_read_live_from_the_grade_structure(self):
        self.client.force_login(self.pm)
        before = self.client.get(self.url).context['salaries_total']
        self.engineer.monthly_salary = Decimal('12000')
        self.engineer.save()
        self.assertEqual(self.client.get(self.url).context['salaries_total'], before + 4000)

    def test_page_matches_the_calculation(self):
        self.client.force_login(self.pm)
        resp = self.client.get(self.url)
        expected = build_project_costing(
            ManpowerCostingHeader(project=self.project), EmploymentCostAssumptions(),
            list(self.project.designation_manpower_lines.all()),
            list(self.project.first_year_maintenance_lines.exclude(section='A')))
        self.assertEqual(resp.context['grand_total'], expected['grand_total'])
        self.assertContains(resp, 'Engineer')
        self.assertContains(resp, 'Pickup Truck')

    def test_overview_has_the_project_costing_card(self):
        self.client.force_login(self.pm)
        resp = self.client.get(reverse('pmo:mpc_project_overview', args=[self.project.pk]))
        self.assertContains(resp, self.url)
        self.assertContains(resp, 'Project Costing (Integrated)')

    # Export

    def test_export_matches_the_page(self):
        self.client.force_login(self.pm)
        page_total = self.client.get(self.url).context['grand_total']
        resp = self.client.get(self.export_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'],
                         'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('project-costing-test-project.xlsx', resp['Content-Disposition'])
        rows = self.sheet_values(resp)
        grand = next(r for r in rows if r[1] == 'GRAND TOTAL COST')
        self.assertEqual(q(grand[8]), q(page_total))
        self.assertTrue(any(r[1] == 'Proposed Contract Value (excl. VAT)' for r in rows))

    def test_export_of_a_project_with_no_data(self):
        empty = self.other_project('Empty Job', 'REF-PC-EMPTY')
        self.client.force_login(self.pm)
        resp = self.client.get(reverse('pmo:mpc_project_costing_export_excel', args=[empty.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(self.client.get(reverse('pmo:mpc_project_costing', args=[empty.pk])),
                            'No designations yet')
