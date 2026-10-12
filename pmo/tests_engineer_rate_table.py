"""Engineer Rate Table: the rate build-up, the page and its Excel export."""
import io
from decimal import Decimal

import openpyxl
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Project, ProjectStatus, Region

from .engineer_rates import build_rate_tables, rate_build_up
from .models import EmploymentCostAssumptions, GradeStructureLine

CENT = Decimal('0.01')


def q(value):
    return Decimal(value).quantize(CENT)


def assumptions(**changes):
    """An in-memory assumptions row: the model defaults (12%, 25%, Iqama
    10,350, ...) with any changes applied."""
    a = EmploymentCostAssumptions()
    for name, value in changes.items():
        setattr(a, name, Decimal(str(value)))
    return a


ALL_ZERO = dict(overhead_pct=0, profit_markup_pct=0, iqama_annual=0, medical_annual=0,
                ticket_annual=0, esb_pct=0, gosi_expat_pct=0, gosi_saudi_pct=0)

# The source workbook's 18 engineers, with its hourly billing rate matrix.
WORKBOOK = [
    ('Telecom', 'E-1', 'Telecom System Engineer', 18000, 'expat', '140.02'),
    ('Telecom', 'E-2', 'Network Engineer', 14000, 'expat', '110.61'),
    ('Telecom', 'E-3', 'Telecom Engineer (Junior)', 10000, 'saudi', '80.63'),
    ('Security', 'E-1', 'Security Systems Engineer', 19000, 'expat', '147.37'),
    ('Security', 'E-2', 'CCTV & Access Control Engineer', 15000, 'expat', '117.96'),
    ('Security', 'E-3', 'Junior Security Engineer', 11000, 'saudi', '88.56'),
    ('AI', 'E-1', 'AI / Machine Learning Engineer', 24000, 'expat', '184.12'),
    ('AI', 'E-2', 'AI Applications Engineer', 19000, 'expat', '147.37'),
    ('AI', 'E-3', 'Junior AI Engineer', 14000, 'saudi', '112.38'),
    ('Hydraulics', 'E-1', 'Hydraulic Engineer (Senior)', 18500, 'expat', '143.69'),
    ('Hydraulics', 'E-2', 'Hydraulic Engineer', 14500, 'expat', '114.28'),
    ('Hydraulics', 'E-3', 'Junior Hydraulic Engineer', 10500, 'saudi', '84.60'),
    ('Cyber', 'E-1', 'Cybersecurity Engineer (Lead)', 23000, 'expat', '176.77'),
    ('Cyber', 'E-2', 'Cybersecurity Engineer', 18000, 'expat', '140.02'),
    ('Cyber', 'E-3', 'SOC Analyst (Junior)', 13000, 'saudi', '104.44'),
    ('IT', 'E-1', 'IT Infrastructure Engineer', 17000, 'expat', '132.66'),
    ('IT', 'E-2', 'IT Systems Engineer', 13000, 'expat', '103.26'),
    ('IT', 'E-3', 'IT Support Engineer', 9500, 'saudi', '76.66'),
]


def grade_line(discipline, grade, salary=10000, nationality='expat', designation='', order=0):
    return GradeStructureLine(
        category=GradeStructureLine.CATEGORY_ENGINEERING, discipline=discipline, grade_code=grade,
        typical_designation=designation, indicative_monthly_salary=Decimal(salary),
        nationality=nationality, order=order)


# ── 1. The rate build-up ─────────────────────────────────────────────────────

class RateBuildUpTests(SimpleTestCase):

    def test_expat_engineer_matches_the_workbook(self):
        r = rate_build_up(Decimal('18000'), True, assumptions())
        expected = {
            'iqama': '862.50', 'medical': '208.33', 'ticket': '208.33', 'esb': '1499.40',
            'gosi_expat': '360.00', 'gosi_saudi': '0.00', 'additional': '3138.57',
            'overhead': '2160.00', 'loaded': '23298.57', 'daily': '896.10', 'hourly': '112.01',
            'bill_monthly': '29123.21', 'bill_daily': '1120.12', 'bill_hourly': '140.02',
        }
        for key, value in expected.items():
            self.assertEqual(q(r[key]), Decimal(value), key)

    def test_saudi_engineer_gets_no_iqama_ticket_or_expat_gosi(self):
        r = rate_build_up(Decimal('10000'), False, assumptions())
        expected = {
            'iqama': '0.00', 'medical': '208.33', 'ticket': '0.00', 'esb': '833.00',
            'gosi_expat': '0.00', 'gosi_saudi': '1175.00', 'additional': '2216.33',
            'loaded': '13416.33', 'bill_hourly': '80.63',
        }
        for key, value in expected.items():
            self.assertEqual(q(r[key]), Decimal(value), key)

    def test_every_hourly_billing_rate_matches_the_workbook_matrix(self):
        for discipline, grade, _d, salary, nationality, hourly in WORKBOOK:
            r = rate_build_up(Decimal(salary), nationality == 'expat', assumptions())
            self.assertEqual(q(r['bill_hourly']), Decimal(hourly), f'{discipline} {grade}')

    def test_markup_changes_billing_but_not_cost(self):
        base = rate_build_up(Decimal('18000'), True, assumptions())
        higher = rate_build_up(Decimal('18000'), True, assumptions(profit_markup_pct=30))
        for key in ('additional', 'loaded', 'daily', 'hourly'):
            self.assertEqual(higher[key], base[key], key)
        self.assertEqual(q(higher['bill_monthly']), q(base['loaded'] * Decimal('1.30')))
        self.assertGreater(higher['bill_hourly'], base['bill_hourly'])

    def test_overhead_changes_cost_and_billing(self):
        base = rate_build_up(Decimal('18000'), True, assumptions())
        higher = rate_build_up(Decimal('18000'), True, assumptions(overhead_pct=15))
        self.assertEqual(q(higher['loaded']), q(base['loaded'] + Decimal('540')))  # 3% of 18,000
        self.assertEqual(higher['additional'], base['additional'])
        self.assertGreater(higher['bill_monthly'], base['bill_monthly'])

    def test_zero_salary_still_carries_the_fixed_annual_costs(self):
        expat = rate_build_up(Decimal('0'), True, assumptions())
        self.assertEqual(q(expat['loaded']), Decimal('1279.17'))  # Iqama + medical + ticket
        self.assertEqual(expat['esb'] + expat['gosi_expat'], 0)
        saudi = rate_build_up(Decimal('0'), False, assumptions())
        self.assertEqual(q(saudi['loaded']), Decimal('208.33'))  # medical only

    def test_missing_salary_counts_as_zero(self):
        self.assertEqual(q(rate_build_up(None, False, assumptions())['loaded']), Decimal('208.33'))

    def test_all_rates_zero_leaves_just_the_salary(self):
        r = rate_build_up(Decimal('5000'), True, assumptions(**ALL_ZERO))
        self.assertEqual(r['loaded'], Decimal('5000'))
        self.assertEqual(r['bill_monthly'], Decimal('5000'))

    def test_daily_and_hourly_use_26_days_and_8_hours(self):
        r = rate_build_up(Decimal('10400'), True, assumptions(**ALL_ZERO))
        self.assertEqual(r['daily'], Decimal('400'))
        self.assertEqual(r['hourly'], Decimal('50'))
        self.assertEqual(r['bill_daily'], Decimal('400'))
        self.assertEqual(r['bill_hourly'], Decimal('50'))


# ── 2. Grouping into tables, matrix and totals ───────────────────────────────

class BuildRateTablesTests(SimpleTestCase):

    def test_workbook_totals_and_layout(self):
        lines = [grade_line(d, g, s, n, des) for d, g, des, s, n, _h in reversed(WORKBOOK)]
        data = build_rate_tables(lines, assumptions())
        totals = {k: q(v) for k, v in data['totals'].items()}
        self.assertEqual(totals['salary'], Decimal('281000.00'))
        self.assertEqual(totals['iqama'], Decimal('10350.00'))
        self.assertEqual(totals['medical'], Decimal('3750.00'))
        self.assertEqual(totals['ticket'], Decimal('2500.00'))
        self.assertEqual(totals['esb'], Decimal('23407.30'))
        self.assertEqual(totals['gosi_expat'], Decimal('4260.00'))
        self.assertEqual(totals['gosi_saudi'], Decimal('7990.00'))
        self.assertEqual(totals['additional'], Decimal('52257.30'))
        self.assertEqual([t['discipline'] for t in data['tables']],
                         ['Telecom', 'Security', 'AI', 'Hydraulics', 'Cyber', 'IT'])
        self.assertEqual(data['grade_codes'], ['E-1', 'E-2', 'E-3'])
        self.assertEqual([q(c['value']) for c in data['matrix'][2]['cells']],
                         [Decimal('184.12'), Decimal('147.37'), Decimal('112.38')])  # AI

    def test_workbook_disciplines_first_then_others_then_unassigned(self):
        lines = [grade_line('IT', 'E-1'), grade_line('Solar', 'E-1'),
                 grade_line('Telecom', 'E-1'), grade_line('   ', 'E-1')]
        data = build_rate_tables(lines, assumptions())
        self.assertEqual([t['discipline'] for t in data['tables']],
                         ['Telecom', 'IT', 'Solar', 'Unassigned'])

    def test_grades_sort_by_number_not_text(self):
        lines = [grade_line('IT', 'E-10'), grade_line('IT', 'E-2'), grade_line('IT', 'E-1')]
        data = build_rate_tables(lines, assumptions())
        self.assertEqual([r['line'].grade_code for r in data['tables'][0]['rows']], ['E-1', 'E-2', 'E-10'])
        self.assertEqual(data['grade_codes'], ['E-1', 'E-2', 'E-10'])

    def test_matrix_leaves_a_missing_grade_empty(self):
        lines = [grade_line('Telecom', 'E-1'), grade_line('Telecom', 'E-2'), grade_line('IT', 'E-1')]
        data = build_rate_tables(lines, assumptions())
        it_row = data['matrix'][1]
        self.assertEqual(it_row['discipline'], 'IT')
        self.assertIsNotNone(it_row['cells'][0])
        self.assertIsNone(it_row['cells'][1])

    def test_two_lines_on_one_grade_are_both_listed_matrix_shows_the_first(self):
        first = grade_line('IT', 'E-1', salary=10000, order=0)
        second = grade_line('IT', 'E-1', salary=20000, order=1)
        data = build_rate_tables([second, first], assumptions())
        self.assertEqual(len(data['tables'][0]['rows']), 2)
        expected = rate_build_up(Decimal('10000'), True, assumptions())['bill_hourly']
        self.assertEqual(data['matrix'][0]['cells'][0]['value'], expected)

    def test_no_lines_gives_empty_tables_and_zero_totals(self):
        data = build_rate_tables([], assumptions())
        self.assertEqual((data['tables'], data['rows'], data['matrix'], data['grade_codes']), ([], [], [], []))
        self.assertTrue(all(v == 0 for v in data['totals'].values()))

    def test_monthly_equivalents_are_annual_over_twelve(self):
        eq = build_rate_tables([], assumptions())['monthly_equivalents']
        self.assertEqual(eq['iqama'], Decimal('862.5'))
        self.assertEqual(q(eq['medical']), Decimal('208.33'))
        self.assertEqual(q(eq['ticket']), Decimal('208.33'))


# ── 3. The page, saving, access, and the export ──────────────────────────────

class EngineerRateTablePageTests(TestCase):

    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.site_role, _ = Role.objects.get_or_create(name=Role.SITE_MANAGER)
        self.dev_role, _ = Role.objects.get_or_create(name=Role.DEVELOPER)
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.west = Region.objects.create(name='West', code='WST', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(project_name='Test Project', status=self.status, region=self.region)
        self.pm = User.objects.create_user('er_pm', password='pw', role=self.pm_role, region=self.region)
        self.site = User.objects.create_user('er_site', password='pw', role=self.site_role, region=self.region)
        self.dev = User.objects.create_user('er_dev', password='pw', role=self.dev_role, region=self.region)
        self.e1 = self.line(self.project, 'E-1', 'Telecom System Engineer', 18000, 'expat')
        self.e3 = self.line(self.project, 'E-3', 'Telecom Engineer (Junior)', 10000, 'saudi')
        GradeStructureLine.objects.create(
            project=self.project, category=GradeStructureLine.CATEGORY_ADMIN, grade_code='A-1',
            typical_designation='Admin Officer', indicative_monthly_salary=Decimal('7000'))
        self.url = reverse('pmo:mpc_engineer_rate_table', args=[self.project.pk])
        self.export_url = reverse('pmo:mpc_engineer_rate_table_export_excel', args=[self.project.pk])

    def line(self, project, grade, designation, salary, nationality, discipline='Telecom'):
        return GradeStructureLine.objects.create(
            project=project, category=GradeStructureLine.CATEGORY_ENGINEERING, grade_code=grade,
            discipline=discipline, typical_designation=designation,
            indicative_monthly_salary=Decimal(salary), nationality=nationality)

    def payload(self, **changes):
        data = {'overhead_pct': '12', 'profit_markup_pct': '25', 'iqama_annual': '10350',
                'medical_annual': '2500', 'ticket_annual': '2500', 'esb_pct': '8.33',
                'gosi_expat_pct': '2', 'gosi_saudi_pct': '11.75',
                f'nationality_{self.e1.pk}': 'expat', f'nationality_{self.e3.pk}': 'saudi'}
        data.update(changes)
        return data

    def row_for(self, response, line):
        return next(r for r in response.context['rows'] if r['line'].pk == line.pk)

    def sheet_rows(self, response):
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
        self.assertEqual(self.client.post(self.url, self.payload(profit_markup_pct='99')).status_code, 403)
        self.assertFalse(EmploymentCostAssumptions.objects.filter(project=self.project).exists())

    def test_project_in_another_region_is_not_found(self):
        other = Project.objects.create(project_name='West Job', proposal_reference='REF-ER-WEST', status=self.status, region=self.west)
        self.client.force_login(self.pm)
        for name in ('pmo:mpc_engineer_rate_table', 'pmo:mpc_engineer_rate_table_export_excel'):
            self.assertEqual(self.client.get(reverse(name, args=[other.pk])).status_code, 404, name)
        resp = self.client.post(reverse('pmo:mpc_engineer_rate_table', args=[other.pk]), self.payload())
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(EmploymentCostAssumptions.objects.filter(project=other).exists())

    def test_site_manager_can_view_but_not_edit(self):
        self.client.force_login(self.site)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'name="overhead_pct"')
        self.assertNotContains(resp, f'name="nationality_{self.e1.pk}"')
        self.assertEqual(self.client.post(
            self.url, self.payload(**{f'nationality_{self.e1.pk}': 'saudi'})).status_code, 403)
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.nationality, 'expat')
        self.assertFalse(EmploymentCostAssumptions.objects.filter(project=self.project).exists())

    def test_site_manager_does_not_see_markup_or_billing(self):
        self.client.force_login(self.site)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Fully Loaded Monthly Cost')
        for hidden in ('Billing Rate', 'Profit markup', 'Hourly Billing Rate Matrix', '140.02'):
            self.assertNotContains(resp, hidden)
        cells = {v for row in self.sheet_rows(self.client.get(self.export_url)) for v in row}
        self.assertIn('Fully Loaded Monthly Cost (SAR)', cells)
        for hidden in ('Monthly Billing Rate (SAR)', 'Profit markup %', 'HOURLY BILLING RATE MATRIX (SAR / HOUR)'):
            self.assertNotIn(hidden, cells)

    def test_project_manager_sees_markup_and_billing(self):
        self.client.force_login(self.pm)
        resp = self.client.get(self.url)
        for shown in ('Hourly Billing Rate (SAR)', 'Profit markup', 'Hourly Billing Rate Matrix', '140.02'):
            self.assertContains(resp, shown)

    # What the page shows

    def test_only_engineering_grades_are_shown(self):
        self.client.force_login(self.pm)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Telecom System Engineer')
        self.assertNotContains(resp, 'Admin Officer')
        self.assertEqual(len(resp.context['rows']), 2)

    def test_page_figures_match_the_workbook(self):
        self.client.force_login(self.pm)
        resp = self.client.get(self.url)
        self.assertEqual(q(self.row_for(resp, self.e1)['calc']['loaded']), Decimal('23298.57'))
        self.assertEqual(q(self.row_for(resp, self.e3)['calc']['additional']), Decimal('2216.33'))
        self.assertContains(resp, '23,298.57')

    def test_reads_salary_live_from_the_grade_structure(self):
        self.client.force_login(self.pm)
        self.e1.indicative_monthly_salary = Decimal('20000')
        self.e1.save()
        resp = self.client.get(self.url)
        expected = rate_build_up(Decimal('20000'), True, assumptions())['loaded']
        self.assertEqual(self.row_for(resp, self.e1)['calc']['loaded'], expected)

    def test_viewing_does_not_create_an_assumptions_row(self):
        self.client.force_login(self.pm)
        self.client.get(self.url)
        self.client.get(self.export_url)
        self.assertFalse(EmploymentCostAssumptions.objects.filter(project=self.project).exists())

    def test_assumptions_are_per_project(self):
        other = Project.objects.create(project_name='Other Job', proposal_reference='REF-ER-OTHER', status=self.status, region=self.region)
        other_line = self.line(other, 'E-1', 'Telecom System Engineer', 18000, 'expat')
        EmploymentCostAssumptions.objects.create(project=other, overhead_pct=Decimal('20'))
        self.client.force_login(self.pm)
        mine = self.row_for(self.client.get(self.url), self.e1)['calc']['loaded']
        theirs = self.row_for(self.client.get(
            reverse('pmo:mpc_engineer_rate_table', args=[other.pk])), other_line)['calc']['loaded']
        self.assertEqual(q(mine), Decimal('23298.57'))
        self.assertEqual(q(theirs), q(mine + Decimal('1440')))  # 8% more overhead on 18,000

    def test_project_without_engineering_grades_shows_guidance_and_still_exports(self):
        empty = Project.objects.create(project_name='Empty Job', proposal_reference='REF-ER-EMPTY', status=self.status, region=self.region)
        self.client.force_login(self.pm)
        self.assertContains(self.client.get(reverse('pmo:mpc_engineer_rate_table', args=[empty.pk])),
                            'No engineering grades yet')
        resp = self.client.get(reverse('pmo:mpc_engineer_rate_table_export_excel', args=[empty.pk]))
        self.assertEqual(resp.status_code, 200)

    # Saving

    def test_project_manager_saves_assumptions_and_nationality(self):
        self.client.force_login(self.pm)
        resp = self.client.post(self.url, self.payload(
            profit_markup_pct='30', **{f'nationality_{self.e1.pk}': 'saudi'}))
        self.assertRedirects(resp, self.url)
        eca = EmploymentCostAssumptions.objects.get(project=self.project)
        self.assertEqual(eca.profit_markup_pct, Decimal('30'))
        self.assertEqual(eca.updated_by, self.pm)
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.nationality, 'saudi')

    def test_negative_value_is_rejected_and_nothing_saved(self):
        self.client.force_login(self.pm)
        resp = self.client.post(self.url, self.payload(
            overhead_pct='-5', **{f'nationality_{self.e1.pk}': 'saudi'}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('overhead_pct', resp.context['form'].errors)
        self.assertFalse(EmploymentCostAssumptions.objects.filter(project=self.project).exists())
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.nationality, 'expat')

    def test_blank_value_is_rejected(self):
        self.client.force_login(self.pm)
        resp = self.client.post(self.url, self.payload(profit_markup_pct=''))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('profit_markup_pct', resp.context['form'].errors)
        self.assertFalse(EmploymentCostAssumptions.objects.filter(project=self.project).exists())

    def test_unknown_nationality_is_ignored(self):
        self.client.force_login(self.pm)
        self.client.post(self.url, self.payload(**{f'nationality_{self.e1.pk}': 'martian'}))
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.nationality, 'expat')

    def test_cannot_change_another_projects_grade_line(self):
        other = Project.objects.create(project_name='Other Job', proposal_reference='REF-ER-OTHER', status=self.status, region=self.region)
        other_line = self.line(other, 'E-1', 'Someone Else', 18000, 'expat')
        self.client.force_login(self.pm)
        self.client.post(self.url, self.payload(**{f'nationality_{other_line.pk}': 'saudi'}))
        other_line.refresh_from_db()
        self.assertEqual(other_line.nationality, 'expat')

    def test_grade_structure_page_still_saves_and_keeps_markup_and_nationality(self):
        EmploymentCostAssumptions.objects.create(project=self.project, profit_markup_pct=Decimal('30'))
        self.client.force_login(self.pm)
        resp = self.client.post(reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk]), {
            'grades-TOTAL_FORMS': '0', 'grades-INITIAL_FORMS': '0',
            'grades-MIN_NUM_FORMS': '0', 'grades-MAX_NUM_FORMS': '1000',
            'designations-TOTAL_FORMS': '0', 'designations-INITIAL_FORMS': '0',
            'designations-MIN_NUM_FORMS': '0', 'designations-MAX_NUM_FORMS': '1000',
            'overhead_pct': '14', 'iqama_annual': '10350', 'medical_annual': '2500',
            'ticket_annual': '2500', 'esb_pct': '8.33', 'gosi_expat_pct': '2', 'gosi_saudi_pct': '11.75',
        })
        self.assertEqual(resp.status_code, 302)
        eca = EmploymentCostAssumptions.objects.get(project=self.project)
        self.assertEqual(eca.overhead_pct, Decimal('14'))
        self.assertEqual(eca.profit_markup_pct, Decimal('30'))
        self.e3.refresh_from_db()
        self.assertEqual(self.e3.nationality, 'saudi')

    # Export

    def test_export_matches_the_page(self):
        self.client.force_login(self.pm)
        resp = self.client.get(self.export_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'],
                         'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('engineer-rate-table-test-project.xlsx', resp['Content-Disposition'])
        rows = self.sheet_rows(resp)
        e1 = next(r for r in rows if r[1] == 'Telecom System Engineer')
        self.assertEqual(q(e1[3]), Decimal('23298.57'))   # fully loaded
        self.assertEqual(q(e1[8]), Decimal('140.02'))     # hourly billing
        total = next(r for r in rows if r[0] == 'TOTAL')
        self.assertEqual(q(total[10]), Decimal('5354.90'))  # 3,138.57 + 2,216.33
        self.assertFalse(any('Admin Officer' in [v for v in r if isinstance(v, str)] for r in rows))
