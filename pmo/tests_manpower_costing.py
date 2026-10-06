from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from projects.models import Project, ProjectStatus, Region

from .models import (
    DesignationManpowerLine, EmploymentCostAssumptions,
    FirstYearMaintenanceLine, GradeStructureLine, ManpowerCostingHeader,
)


class ManpowerCostingAccessTests(TestCase):
    """Viewing follows can_see_delivery (same audience as the rest of pmo);
    editing follows the narrower can_manage_manpower_costing, which
    deliberately excludes site_manager — a PM-only bid estimate, unlike
    can_update_progress which site_manager also gets."""

    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.site_role, _ = Role.objects.get_or_create(name=Role.SITE_MANAGER)
        self.dev_role, _ = Role.objects.get_or_create(name=Role.DEVELOPER)
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='Test Project', status=self.status, region=self.region)
        self.pm_user = User.objects.create_user(
            'mpc_pm', password='pw', role=self.pm_role, region=self.region)
        self.site_user = User.objects.create_user(
            'mpc_site', password='pw', role=self.site_role, region=self.region)
        self.dev_user = User.objects.create_user(
            'mpc_dev', password='pw', role=self.dev_role, region=self.region)

    def _empty_grade_structure_payload(self):
        return {
            'grades-TOTAL_FORMS': '0', 'grades-INITIAL_FORMS': '0',
            'grades-MIN_NUM_FORMS': '0', 'grades-MAX_NUM_FORMS': '1000',
            'designations-TOTAL_FORMS': '0', 'designations-INITIAL_FORMS': '0',
            'designations-MIN_NUM_FORMS': '0', 'designations-MAX_NUM_FORMS': '1000',
        }

    def test_no_delivery_access_is_denied_everywhere(self):
        self.client.force_login(self.dev_user)
        urls = [
            reverse('pmo:mpc_index'),
            reverse('pmo:mpc_project_overview', args=[self.project.pk]),
            reverse('pmo:mpc_first_year_detail', args=[self.project.pk]),
            reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_project_manager_can_view_and_edit(self):
        self.client.force_login(self.pm_user)
        self.assertEqual(self.client.get(reverse('pmo:mpc_index')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('pmo:mpc_project_overview', args=[self.project.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('pmo:mpc_first_year_detail', args=[self.project.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk])).status_code, 200)

    def test_site_manager_can_view_but_not_edit(self):
        self.client.force_login(self.site_user)
        self.assertEqual(
            self.client.get(reverse('pmo:mpc_first_year_detail', args=[self.project.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk])).status_code, 200)
        # Both pages are now single view+edit pages — GET is 200 for everyone
        # with delivery access, but a site_manager's POST must still be
        # refused since can_manage_manpower_costing excludes them.
        post_payload = {
            'po_type': '', 'po_value': '', 'currency_override': '',
            'vat_rate': '15.00', 'profit_margin_pct': '',
            'first_year_maintenance_lines-TOTAL_FORMS': '0',
            'first_year_maintenance_lines-INITIAL_FORMS': '0',
            'first_year_maintenance_lines-MIN_NUM_FORMS': '0',
            'first_year_maintenance_lines-MAX_NUM_FORMS': '1000',
        }
        self.assertEqual(
            self.client.post(reverse('pmo:mpc_first_year_detail', args=[self.project.pk]), post_payload).status_code,
            403)
        self.assertEqual(
            self.client.post(reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk]),
                              self._empty_grade_structure_payload()).status_code,
            403)

    def test_site_manager_cannot_see_bid_value_figures(self):
        """Narrowing decided after review: a Site Manager (view-only) must
        not see Proposed Contract Value, Profit, or the VAT-inclusive value
        anywhere — not the project index chip, not the overview KPI tiles,
        not the First Year Maintenance page (HTML or its Excel export), and
        not the margin % itself (cost is already visible there, so a
        visible margin % would let the same figures be back-calculated).
        A PM, who can edit the bid, still sees all of it."""
        ManpowerCostingHeader.objects.create(
            project=self.project, profit_margin_pct=Decimal('25'))
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_B,
            description='Mobilization', nature_of_expense=FirstYearMaintenanceLine.NATURE_ONE_TIME,
            amount=Decimal('240000.00'), qty=1)
        # cost=240,000; margin 25% -> contract value 320,000; profit 80,000.

        self.client.force_login(self.site_user)
        index_resp = self.client.get(reverse('pmo:mpc_index'))
        self.assertNotContains(index_resp, 'Proposed Contract Value')

        overview_resp = self.client.get(reverse('pmo:mpc_project_overview', args=[self.project.pk]))
        self.assertNotContains(overview_resp, 'Proposed Contract Value')
        self.assertNotContains(overview_resp, '80,000')
        self.assertContains(overview_resp, 'Total Annual Cost')

        fym_resp = self.client.get(reverse('pmo:mpc_first_year_detail', args=[self.project.pk]))
        self.assertNotContains(fym_resp, 'Proposed Contract Value')
        self.assertNotContains(fym_resp, '>Profit<')
        self.assertNotContains(fym_resp, 'Target Profit Margin')
        self.assertNotContains(fym_resp, '80,000')
        self.assertContains(fym_resp, 'Total Annual Cost')

        import openpyxl
        from io import BytesIO
        excel_resp = self.client.get(reverse('pmo:mpc_first_year_export_excel', args=[self.project.pk]))
        wb = openpyxl.load_workbook(BytesIO(excel_resp.content))
        values = [cell.value for row in wb.active.iter_rows() for cell in row if cell.value is not None]
        self.assertNotIn('Proposed Contract Value', values)
        self.assertNotIn('Profit', values)
        self.assertNotIn(80000.0, values)
        self.assertNotIn(320000.0, values)

        # A PM sees all of it.
        self.client.force_login(self.pm_user)
        pm_resp = self.client.get(reverse('pmo:mpc_first_year_detail', args=[self.project.pk]))
        self.assertContains(pm_resp, 'Proposed Contract Value')
        self.assertContains(pm_resp, 'Target Profit Margin')

    def test_project_in_another_region_404s(self):
        other_region = Region.objects.create(name='Other', code='OTH', currency='USD')
        other_project = Project.objects.create(
            project_name='Other Project', status=self.status, region=other_region,
            proposal_reference='LNA-OTHER-001')
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:mpc_project_overview', args=[other_project.pk]))
        self.assertEqual(r.status_code, 404)


class ManpowerCostingCalculationTests(TestCase):
    """Pins the calculation logic — section+nature-based annual rollup,
    margin, profit and VAT — against hand-verified Decimal arithmetic for a
    fixture spanning all 4 sections and all 3 natures."""

    def setUp(self):
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='Costing Project', status=self.status, region=self.region)

    def test_section_and_nature_based_annual_rollup(self):
        # Section A (monthly-nature sections annualize ×12): monthly amounts
        # that sum to a 21,388.27 monthly subtotal → ×12 = 256,659.23.
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_A,
            description='Site Manager', nature_of_expense=FirstYearMaintenanceLine.NATURE_MONTHLY,
            amount=Decimal('15000.00'), qty=1)
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_A,
            description='Annual Iqama/Medical (spread monthly)',
            nature_of_expense=FirstYearMaintenanceLine.NATURE_ANNUAL,
            amount=Decimal('76659.24'), qty=1)
        # Section B (one-time, no ×12): mobilization costs.
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_B,
            description='Mobilization', nature_of_expense=FirstYearMaintenanceLine.NATURE_ONE_TIME,
            amount=Decimal('250000.00'), qty=1)
        # Section C (monthly, ×12): vehicle lease.
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_C,
            description='Vehicle Lease', nature_of_expense=FirstYearMaintenanceLine.NATURE_MONTHLY,
            amount=Decimal('9000.00'), qty=1)
        # Section D (one-time, no ×12): tools.
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_D,
            description='Tools', nature_of_expense=FirstYearMaintenanceLine.NATURE_ONE_TIME,
            amount=Decimal('11000.00'), qty=1)

        header = ManpowerCostingHeader.objects.create(project=self.project, profit_margin_pct=Decimal('39'))

        self.assertEqual(header.total_annual_cost, Decimal('625659.24'))
        self.assertEqual(header.proposed_contract_value, Decimal('1025670.89'))
        self.assertEqual(header.profit, Decimal('400011.65'))
        self.assertEqual(header.proposed_contract_value_incl_vat, Decimal('1179521.52'))

    def test_annual_contribution_is_nature_driven_not_section_driven(self):
        """Regression test for a reported bug: a line's annualisation used
        to be decided by which section it sat in (MONTHLY_SECTIONS), not by
        its own nature_of_expense — so a one-time line filed under a
        "monthly" section (A/C) was wrongly multiplied by 12, and a monthly
        line filed under a "one-time" section (B/D) was wrongly counted
        only once. annual_contribution must now give the same answer
        regardless of which section the line is filed under."""
        one_time_in_monthly_section = FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_A,
            description='Mobilization air ticket (mis-filed)',
            nature_of_expense=FirstYearMaintenanceLine.NATURE_ONE_TIME,
            amount=Decimal('1000.00'), qty=1)
        monthly_in_one_time_section = FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_B,
            description='Recurring allowance (mis-filed)',
            nature_of_expense=FirstYearMaintenanceLine.NATURE_MONTHLY,
            amount=Decimal('1000.00'), qty=1)

        # A one-time cost is counted once, no matter which section holds it.
        self.assertEqual(one_time_in_monthly_section.annual_contribution, Decimal('1000.00'))
        # A monthly cost recurs ×12, no matter which section holds it.
        self.assertEqual(monthly_in_one_time_section.annual_contribution, Decimal('12000.00'))

        header = ManpowerCostingHeader.objects.create(project=self.project)
        self.assertEqual(header.total_annual_cost, Decimal('13000.00'))
        breakdown = {row['code']: row['annual_total'] for row in header.section_annual_breakdown()}
        self.assertEqual(breakdown['A'], Decimal('1000.00'))
        self.assertEqual(breakdown['B'], Decimal('12000.00'))

    def test_blank_margin_means_contract_value_equals_cost(self):
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_B,
            description='One-off item', nature_of_expense=FirstYearMaintenanceLine.NATURE_ONE_TIME,
            amount=Decimal('1000.00'), qty=2)
        header = ManpowerCostingHeader.objects.create(project=self.project)

        self.assertEqual(header.total_annual_cost, Decimal('2000.00'))
        self.assertEqual(header.proposed_contract_value, Decimal('2000.00'))
        self.assertEqual(header.profit, Decimal('0.00'))

    def test_header_built_in_memory_when_missing_does_not_write_a_row(self):
        self.assertFalse(hasattr(self.project, 'manpower_costing_header'))
        pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        pm_user = User.objects.create_user(
            'mpc_calc_pm', password='pw', role=pm_role, region=self.region)
        self.client.force_login(pm_user)
        r = self.client.get(reverse('pmo:mpc_project_overview', args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ManpowerCostingHeader.objects.filter(project=self.project).exists())

    def test_grade_headcount_and_cost_are_summed_from_designations(self):
        """GradeStructureLine no longer stores headcount/cost directly — it
        sums whichever DesignationManpowerLine rows share its grade_code,
        matching the source workbook's SUMIF-from-roster formula. A grade
        with two designations (e.g. 'Engineer' and 'HSE Engineer' both under
        E-1) must sum both, not just reflect one."""
        line = GradeStructureLine.objects.create(
            project=self.project, category=GradeStructureLine.CATEGORY_ENGINEERING,
            grade_code='E-1', indicative_monthly_salary=Decimal('8000.00'))
        # Grade line with no matching designations yet: zero, not an error.
        self.assertEqual(line.headcount, 0)
        self.assertEqual(line.monthly_cost, Decimal('0'))

        DesignationManpowerLine.objects.create(
            project=self.project, designation='Engineer', category=GradeStructureLine.CATEGORY_ENGINEERING,
            grade_code='E-1', headcount=3, monthly_salary=Decimal('8000.00'))
        DesignationManpowerLine.objects.create(
            project=self.project, designation='HSE Engineer', category=GradeStructureLine.CATEGORY_ENGINEERING,
            grade_code='E-1', headcount=2, monthly_salary=Decimal('9000.00'))
        # A designation under a different grade must not be counted.
        DesignationManpowerLine.objects.create(
            project=self.project, designation='Unrelated', category=GradeStructureLine.CATEGORY_ADMIN,
            grade_code='A-1', headcount=99, monthly_salary=Decimal('1.00'))

        self.assertEqual(line.headcount, 5)
        self.assertEqual(line.monthly_cost, Decimal('42000.00'))
        self.assertEqual(line.annual_cost, Decimal('504000.00'))

    def test_additional_employment_cost_is_nationality_gated(self):
        """Pins the exact worked example verified against the source
        workbook's Engineer Rate Tables sheet: a Telecom E-1 expat on
        18,000 base salary should come to a 23,298.57 fully loaded cost,
        to the penny. Iqama/Ticket/GOSI-Expat apply only to the expat;
        GOSI-Saudi only to the Saudi; Medical and overhead apply to both."""
        EmploymentCostAssumptions.objects.create(
            project=self.project, overhead_pct=Decimal('12'),
            iqama_annual=Decimal('10350'), medical_annual=Decimal('2500'),
            ticket_annual=Decimal('2500'), esb_pct=Decimal('8.33'),
            gosi_expat_pct=Decimal('2'), gosi_saudi_pct=Decimal('11.75'))

        expat = DesignationManpowerLine.objects.create(
            project=self.project, designation='Telecom System Engineer',
            category=GradeStructureLine.CATEGORY_ENGINEERING, grade_code='E-1',
            nationality=DesignationManpowerLine.NATIONALITY_EXPAT,
            headcount=1, monthly_salary=Decimal('18000'))
        self.assertEqual(expat.iqama_monthly, Decimal('862.50'))
        self.assertEqual(expat.medical_monthly, Decimal('208.3333333333333333333333333'))
        self.assertEqual(expat.ticket_monthly, Decimal('208.3333333333333333333333333'))
        self.assertEqual(expat.esb_monthly, Decimal('1499.40'))
        self.assertEqual(expat.gosi_monthly, Decimal('360'))
        self.assertEqual(expat.overhead_monthly, Decimal('2160'))
        self.assertEqual(expat.fully_loaded_monthly_cost.quantize(Decimal('0.01')), Decimal('23298.57'))

        saudi = DesignationManpowerLine.objects.create(
            project=self.project, designation='Telecom System Engineer (Saudi)',
            category=GradeStructureLine.CATEGORY_ENGINEERING, grade_code='E-1',
            nationality=DesignationManpowerLine.NATIONALITY_SAUDI,
            headcount=1, monthly_salary=Decimal('18000'))
        # No Iqama, no ticket for a Saudi — but GOSI-Saudi (11.75%) applies
        # instead of GOSI-Expat, and medical still applies to everyone.
        self.assertEqual(saudi.iqama_monthly, Decimal('0'))
        self.assertEqual(saudi.ticket_monthly, Decimal('0'))
        self.assertEqual(saudi.medical_monthly, Decimal('208.3333333333333333333333333'))
        self.assertEqual(saudi.gosi_monthly, Decimal('2115.00'))

    def test_additional_cost_breakdown_lists_every_component(self):
        EmploymentCostAssumptions.objects.create(project=self.project)
        line = DesignationManpowerLine.objects.create(
            project=self.project, designation='Engineer',
            category=GradeStructureLine.CATEGORY_ENGINEERING, grade_code='E-1',
            nationality=DesignationManpowerLine.NATIONALITY_EXPAT,
            headcount=1, monthly_salary=Decimal('10000'))
        labels = [row['label'] for row in line.additional_cost_breakdown()]
        self.assertEqual(labels, ['Iqama', 'Medical', 'Ticket', 'ESB', 'GOSI (Expat)'])
        total = sum((row['amount'] for row in line.additional_cost_breakdown()), Decimal('0'))
        self.assertEqual(total, line.additional_cost_monthly)

    def test_assumptions_built_in_memory_when_missing_does_not_write_a_row(self):
        line = DesignationManpowerLine(
            project=self.project, designation='Engineer',
            category=GradeStructureLine.CATEGORY_ENGINEERING, grade_code='E-1',
            nationality=DesignationManpowerLine.NATIONALITY_EXPAT,
            headcount=1, monthly_salary=Decimal('10000'))
        # Uses the model defaults (12% overhead, 10,350 Iqama, etc.) even
        # though no EmploymentCostAssumptions row exists for this project.
        self.assertFalse(hasattr(self.project, 'employment_cost_assumptions'))
        self.assertGreater(line.additional_cost_monthly, Decimal('0'))
        self.assertFalse(EmploymentCostAssumptions.objects.filter(project=self.project).exists())

    def test_grand_total_monthly_and_daily_derive_from_annual(self):
        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_B,
            description='One-off item', nature_of_expense=FirstYearMaintenanceLine.NATURE_ONE_TIME,
            amount=Decimal('1200000.00'), qty=1)
        header = ManpowerCostingHeader.objects.create(project=self.project)
        self.assertEqual(header.total_annual_cost, Decimal('1200000.00'))
        self.assertEqual(header.total_monthly_cost, Decimal('100000.00'))
        # Matches the source workbook's own DAILY COST row: =MONTHLY COST/26.
        self.assertEqual(header.total_daily_cost, Decimal('3846.15'))


class ManpowerCostingFormsetSaveTests(TestCase):
    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.region = Region.objects.create(name='Arabia', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='Formset Project', status=self.status, region=self.region)
        self.pm_user = User.objects.create_user(
            'mpc_formset_pm', password='pw', role=self.pm_role, region=self.region)

    def _eca_payload(self):
        """The Employment Cost Assumptions fields a real form submission
        always includes (they're rendered pre-filled) — needed on every
        POST to mpc_grade_structure_detail since that form is validated
        alongside the two formsets."""
        return {
            'overhead_pct': '12', 'iqama_annual': '10350', 'medical_annual': '2500',
            'ticket_annual': '2500', 'esb_pct': '8.33', 'gosi_expat_pct': '2',
            'gosi_saudi_pct': '11.75',
        }

    def test_grade_structure_add_edit_delete(self):
        self.client.force_login(self.pm_user)
        url = reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk])
        payload = {
            'grades-TOTAL_FORMS': '2', 'grades-INITIAL_FORMS': '0',
            'grades-MIN_NUM_FORMS': '0', 'grades-MAX_NUM_FORMS': '1000',
            'grades-0-category': GradeStructureLine.CATEGORY_ENGINEERING,
            'grades-0-grade_code': 'E-1',
            'grades-0-discipline': 'Telecom',
            'grades-0-typical_designation': 'Field Engineer',
            'grades-0-criteria': '',
            'grades-0-indicative_monthly_salary': '7500.00',
            'grades-0-order': '0',
            # trailing blank row — must not block the save.
            'grades-1-category': GradeStructureLine.CATEGORY_ADMIN,
            'grades-1-grade_code': '',
            'grades-1-discipline': '',
            'grades-1-typical_designation': '',
            'grades-1-criteria': '',
            'grades-1-indicative_monthly_salary': '0',
            'grades-1-order': '1',
            'designations-TOTAL_FORMS': '1', 'designations-INITIAL_FORMS': '0',
            'designations-MIN_NUM_FORMS': '0', 'designations-MAX_NUM_FORMS': '1000',
            'designations-0-designation': 'Field Engineer',
            'designations-0-category': GradeStructureLine.CATEGORY_ENGINEERING,
            'designations-0-grade_code': 'E-1',
            'designations-0-discipline': 'Telecom',
            'designations-0-nationality': DesignationManpowerLine.NATIONALITY_EXPAT,
            'designations-0-headcount': '2',
            'designations-0-monthly_salary': '7500.00',
            'designations-0-order': '0',
            **self._eca_payload(),
        }
        r = self.client.post(url, payload)
        self.assertRedirects(r, url)
        self.assertEqual(self.project.grade_structure_lines.count(), 1)
        line = self.project.grade_structure_lines.get()
        self.assertEqual(line.grade_code, 'E-1')
        self.assertEqual(line.headcount, 2)
        designation = self.project.designation_manpower_lines.get()

        # Edit the designation's headcount — the grade's computed headcount
        # must follow it without the grade line itself being touched.
        edit_payload = dict(payload)
        edit_payload['grades-INITIAL_FORMS'] = '1'
        edit_payload['grades-0-id'] = str(line.pk)
        edit_payload['designations-INITIAL_FORMS'] = '1'
        edit_payload['designations-0-id'] = str(designation.pk)
        edit_payload['designations-0-headcount'] = '5'
        r = self.client.post(url, edit_payload)
        self.assertRedirects(r, url)
        line.refresh_from_db()
        self.assertEqual(line.headcount, 5)

        delete_payload = dict(edit_payload)
        delete_payload['designations-0-DELETE'] = 'on'
        r = self.client.post(url, delete_payload)
        self.assertRedirects(r, url)
        self.assertEqual(self.project.designation_manpower_lines.count(), 0)
        self.assertEqual(line.headcount, 0)
        # The grade line itself survives — only its designation was deleted.
        self.assertEqual(self.project.grade_structure_lines.count(), 1)

    def test_new_designation_under_unseen_grade_code_auto_creates_its_grade_row(self):
        """Reported behaviour: adding a Designation-wise Manpower row for a
        grade code with no existing Individual Grade row must still show up
        in both Individual Grades and Summary by Category, not roll up
        nowhere — matching the user's expectation that the tables are
        linked, one step beyond the source workbook's own SUMIF (which
        silently drops an unmatched roster row)."""
        self.client.force_login(self.pm_user)
        url = reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk])
        payload = {
            'grades-TOTAL_FORMS': '0', 'grades-INITIAL_FORMS': '0',
            'grades-MIN_NUM_FORMS': '0', 'grades-MAX_NUM_FORMS': '1000',
            'designations-TOTAL_FORMS': '1', 'designations-INITIAL_FORMS': '0',
            'designations-MIN_NUM_FORMS': '0', 'designations-MAX_NUM_FORMS': '1000',
            'designations-0-designation': 'Deputy Project Director',
            'designations-0-category': GradeStructureLine.CATEGORY_EXECUTIVE,
            'designations-0-grade_code': 'EX-2',
            'designations-0-discipline': 'General/N/A',
            'designations-0-nationality': DesignationManpowerLine.NATIONALITY_EXPAT,
            'designations-0-headcount': '1',
            'designations-0-monthly_salary': '20000.00',
            'designations-0-order': '0',
            **self._eca_payload(),
        }
        self.assertEqual(self.project.grade_structure_lines.count(), 0)
        r = self.client.post(url, payload)
        self.assertRedirects(r, url)

        self.assertEqual(self.project.grade_structure_lines.count(), 1)
        grade = self.project.grade_structure_lines.get()
        self.assertEqual(grade.grade_code, 'EX-2')
        self.assertEqual(grade.headcount, 1)
        self.assertEqual(grade.monthly_cost, Decimal('20000.00'))

    def test_employment_cost_assumptions_save_alongside_the_rosters(self):
        self.client.force_login(self.pm_user)
        url = reverse('pmo:mpc_grade_structure_detail', args=[self.project.pk])
        payload = {
            'grades-TOTAL_FORMS': '0', 'grades-INITIAL_FORMS': '0',
            'grades-MIN_NUM_FORMS': '0', 'grades-MAX_NUM_FORMS': '1000',
            'designations-TOTAL_FORMS': '0', 'designations-INITIAL_FORMS': '0',
            'designations-MIN_NUM_FORMS': '0', 'designations-MAX_NUM_FORMS': '1000',
            'overhead_pct': '15', 'iqama_annual': '9000', 'medical_annual': '3000',
            'ticket_annual': '2000', 'esb_pct': '9.5', 'gosi_expat_pct': '2.5',
            'gosi_saudi_pct': '12',
        }
        r = self.client.post(url, payload)
        self.assertRedirects(r, url)
        eca = EmploymentCostAssumptions.objects.get(project=self.project)
        self.assertEqual(eca.overhead_pct, Decimal('15.00'))
        self.assertEqual(eca.iqama_annual, Decimal('9000.00'))
        self.assertEqual(eca.gosi_saudi_pct, Decimal('12.00'))
        self.assertEqual(eca.updated_by, self.pm_user)

    def test_first_year_maintenance_header_and_lines_save_together(self):
        self.client.force_login(self.pm_user)
        url = reverse('pmo:mpc_first_year_detail', args=[self.project.pk])
        payload = {
            'po_type': '1 Year',
            'po_value': '1000000.00',
            'currency_override': '',
            'vat_rate': '15.00',
            'profit_margin_pct': '30.00',
            'first_year_maintenance_lines-TOTAL_FORMS': '1',
            'first_year_maintenance_lines-INITIAL_FORMS': '0',
            'first_year_maintenance_lines-MIN_NUM_FORMS': '0',
            'first_year_maintenance_lines-MAX_NUM_FORMS': '1000',
            'first_year_maintenance_lines-0-section': FirstYearMaintenanceLine.SECTION_B,
            'first_year_maintenance_lines-0-description': 'Mobilization',
            'first_year_maintenance_lines-0-nature_of_expense': FirstYearMaintenanceLine.NATURE_ONE_TIME,
            'first_year_maintenance_lines-0-amount': '50000.00',
            'first_year_maintenance_lines-0-qty': '1',
            'first_year_maintenance_lines-0-remarks': '',
            'first_year_maintenance_lines-0-order': '0',
        }
        r = self.client.post(url, payload)
        self.assertRedirects(r, reverse('pmo:mpc_first_year_detail', args=[self.project.pk]))
        header = ManpowerCostingHeader.objects.get(project=self.project)
        self.assertEqual(header.po_type, '1 Year')
        self.assertEqual(header.updated_by, self.pm_user)
        self.assertEqual(self.project.first_year_maintenance_lines.count(), 1)
        self.assertEqual(header.total_annual_cost, Decimal('50000.00'))

    def test_po_number_and_date_are_editable_from_this_page(self):
        """PO Number/Date live on Project, but a PM pricing a bid often
        doesn't have them yet at award — they must be fillable here rather
        than only from the pipeline elsewhere in the ERP."""
        self.client.force_login(self.pm_user)
        url = reverse('pmo:mpc_first_year_detail', args=[self.project.pk])
        payload = {
            'po_type': '', 'po_value': '', 'currency_override': '',
            'vat_rate': '15.00', 'profit_margin_pct': '',
            'po_number': 'PO-2026-9001',
            'estimated_po_date': '2026-03-15',
            'first_year_maintenance_lines-TOTAL_FORMS': '0',
            'first_year_maintenance_lines-INITIAL_FORMS': '0',
            'first_year_maintenance_lines-MIN_NUM_FORMS': '0',
            'first_year_maintenance_lines-MAX_NUM_FORMS': '1000',
        }
        r = self.client.post(url, payload)
        self.assertRedirects(r, url)
        self.project.refresh_from_db()
        self.assertEqual(self.project.po_number, 'PO-2026-9001')
        self.assertEqual(str(self.project.estimated_po_date), '2026-03-15')

    def test_newly_added_line_reappears_when_reopening_the_page(self):
        """Regression test for a reported bug: adding a new line and saving,
        then reopening the same (now merged) page, must show the new line —
        not just the rows that existed before it was added."""
        self.client.force_login(self.pm_user)
        url = reverse('pmo:mpc_first_year_detail', args=[self.project.pk])
        payload = {
            'po_type': '', 'po_value': '', 'currency_override': '',
            'vat_rate': '15.00', 'profit_margin_pct': '',
            'first_year_maintenance_lines-TOTAL_FORMS': '1',
            'first_year_maintenance_lines-INITIAL_FORMS': '0',
            'first_year_maintenance_lines-MIN_NUM_FORMS': '0',
            'first_year_maintenance_lines-MAX_NUM_FORMS': '1000',
            'first_year_maintenance_lines-0-section': FirstYearMaintenanceLine.SECTION_A,
            'first_year_maintenance_lines-0-description': 'CCTV Technician',
            'first_year_maintenance_lines-0-nature_of_expense': FirstYearMaintenanceLine.NATURE_MONTHLY,
            'first_year_maintenance_lines-0-amount': '3500.00',
            'first_year_maintenance_lines-0-qty': '2',
            'first_year_maintenance_lines-0-remarks': '',
            'first_year_maintenance_lines-0-order': '0',
        }
        r = self.client.post(url, payload)
        self.assertRedirects(r, url)

        r2 = self.client.get(url)
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, 'CCTV Technician')
        self.assertEqual(r2['Cache-Control'], 'no-store')

    def test_export_excel_returns_a_workbook(self):
        import openpyxl
        from io import BytesIO

        FirstYearMaintenanceLine.objects.create(
            project=self.project, section=FirstYearMaintenanceLine.SECTION_B,
            description='Mobilization', nature_of_expense=FirstYearMaintenanceLine.NATURE_ONE_TIME,
            amount=Decimal('50000.00'), qty=1)
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:mpc_first_year_export_excel', args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        wb = openpyxl.load_workbook(BytesIO(r.content))
        ws = wb.active
        values = [cell.value for row in ws.iter_rows() for cell in row if cell.value is not None]
        self.assertIn('Mobilization', values)
        self.assertIn('GRAND TOTAL', values)

    def test_grade_structure_export_excel_returns_a_workbook(self):
        import openpyxl
        from io import BytesIO

        GradeStructureLine.objects.create(
            project=self.project, category=GradeStructureLine.CATEGORY_ENGINEERING,
            grade_code='E-1', discipline='Telecom', typical_designation='Senior Engineer',
            indicative_monthly_salary=Decimal('12000.00'))
        DesignationManpowerLine.objects.create(
            project=self.project, designation='Senior Engineer', category=GradeStructureLine.CATEGORY_ENGINEERING,
            grade_code='E-1', discipline='Telecom', headcount=2, monthly_salary=Decimal('12000.00'))
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:mpc_grade_structure_export_excel', args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        wb = openpyxl.load_workbook(BytesIO(r.content))
        ws = wb.active
        values = [cell.value for row in ws.iter_rows() for cell in row if cell.value is not None]
        self.assertIn('E-1', values)
        self.assertIn('SUMMARY BY CATEGORY', values)
        self.assertIn('DESIGNATION-WISE MANPOWER', values)
        self.assertIn('Senior Engineer', values)
        # Font colours must be full 8-digit ARGB, or openpyxl (and Excel)
        # silently render the colour as 0% opacity — a real bug hit earlier
        # in this feature, pinned here so it can't quietly come back.
        for row in ws.iter_rows():
            for cell in row:
                if cell.font.color and cell.font.color.type == 'rgb':
                    self.assertTrue(
                        cell.font.color.rgb is None or cell.font.color.rgb.startswith('FF')
                        or len(cell.font.color.rgb) == 8,
                        f'{cell.coordinate} has a non-opaque/short colour: {cell.font.color.rgb}')
