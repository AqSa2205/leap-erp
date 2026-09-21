from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from hr.models import Employee

from .models import ManpowerResource


class ManpowerAccessTests(TestCase):
    def setUp(self):
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.manager_role, _ = Role.objects.get_or_create(name=Role.MANAGER)
        self.dev_role, _ = Role.objects.get_or_create(name=Role.DEVELOPER)
        self.pm_user = User.objects.create_user('mp_pm', password='pw', role=self.pm_role)
        self.manager_user = User.objects.create_user('mp_mgr', password='pw', role=self.manager_role)
        self.dev_user = User.objects.create_user('mp_dev', password='pw', role=self.dev_role)
        self.employee = Employee.objects.create(
            iqama_number='1000000001', full_name='Test Employee', designation='Technician')

    def test_role_without_delivery_access_is_denied(self):
        self.client.force_login(self.dev_user)
        self.assertEqual(self.client.get(reverse('pmo:manpower_list')).status_code, 403)

    def test_manager_can_view_but_not_add(self):
        self.client.force_login(self.manager_user)
        self.assertEqual(self.client.get(reverse('pmo:manpower_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('pmo:manpower_create')).status_code, 403)

    def test_list_shows_every_hr_employee_even_without_details_yet(self):
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:manpower_list'))
        self.assertContains(r, 'Test Employee')
        self.assertContains(r, '1000000001')

    def test_edit_fills_in_details_for_an_existing_employee(self):
        self.client.force_login(self.pm_user)
        r = self.client.post(reverse('pmo:manpower_edit', args=[self.employee.pk]), {
            'title': 'Site Technician',
            'start_contract_date': '2026-01-01',
            'end_contract_date': '',
            'engagement_type': 'direct',
            'discipline': 'technical',
            'employment_level': 'entry',
            'notes': '',
        })
        self.assertRedirects(r, reverse('pmo:manpower_list'))
        resource = ManpowerResource.objects.get(employee=self.employee)
        self.assertEqual(resource.title, 'Site Technician')
        self.assertIsNotNone(resource.experience_days)

        list_html = self.client.get(reverse('pmo:manpower_list')).content.decode()
        self.assertIn('Site Technician', list_html)

    def test_manager_cannot_edit_details(self):
        self.client.force_login(self.manager_user)
        r = self.client.get(reverse('pmo:manpower_edit', args=[self.employee.pk]))
        self.assertEqual(r.status_code, 403)

    def test_add_employee_onboards_someone_new_to_hr(self):
        self.client.force_login(self.pm_user)
        r = self.client.post(reverse('pmo:manpower_create'), {
            'full_name': 'Brand New Person',
            'iqama_number': '2000000009',
            'designation': 'Site Engineer',
            'title': '',
            'start_contract_date': '',
            'end_contract_date': '',
            'engagement_type': '',
            'discipline': '',
            'employment_level': '',
            'notes': '',
        })
        self.assertRedirects(r, reverse('pmo:manpower_list'))
        employee = Employee.objects.get(iqama_number='2000000009')
        self.assertEqual(employee.full_name, 'Brand New Person')
        self.assertTrue(ManpowerResource.objects.filter(employee=employee).exists())

    def test_add_employee_rejects_a_duplicate_id_number(self):
        self.client.force_login(self.pm_user)
        r = self.client.post(reverse('pmo:manpower_create'), {
            'full_name': 'Duplicate Person',
            'iqama_number': self.employee.iqama_number,
            'designation': '',
            'title': '', 'start_contract_date': '', 'end_contract_date': '',
            'engagement_type': '', 'discipline': '', 'employment_level': '', 'notes': '',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already exists in HR')
        self.assertEqual(Employee.objects.filter(iqama_number=self.employee.iqama_number).count(), 1)

    def test_clear_removes_details_but_keeps_employee_on_list(self):
        ManpowerResource.objects.create(employee=self.employee, title='Site Technician')
        self.client.force_login(self.pm_user)
        r = self.client.post(reverse('pmo:manpower_clear', args=[self.employee.pk]))
        self.assertRedirects(r, reverse('pmo:manpower_list'))
        self.assertFalse(ManpowerResource.objects.filter(employee=self.employee).exists())

        list_html = self.client.get(reverse('pmo:manpower_list')).content.decode()
        self.assertIn('Test Employee', list_html)


class ManpowerAssignmentStatusTests(TestCase):
    """ManpowerResource.engagement_status / current_assignments - derived
    from ManpowerAssignment date ranges, never stored, so a status can't go
    stale the way the Excel sheet's manually-ticked checkboxes could."""

    def setUp(self):
        from datetime import timedelta
        from projects.models import Region, ProjectStatus, Project
        self.today = date.today()
        self.yesterday = self.today - timedelta(days=1)
        self.tomorrow = self.today + timedelta(days=1)
        self.last_week = self.today - timedelta(days=7)
        self.next_week = self.today + timedelta(days=7)

        employee = Employee.objects.create(
            iqama_number='2000000001', full_name='Assignment Test Employee', designation='Engineer')
        self.resource = ManpowerResource.objects.create(employee=employee)

        region = Region.objects.create(name='MA Region', code='MAREG')
        won = ProjectStatus.objects.create(name='Won-MA', category='won')
        self.project_a = Project.objects.create(
            project_name='Project A', proposal_reference='MA-REF-A', status=won, region=region)
        self.project_b = Project.objects.create(
            project_name='Project B', proposal_reference='MA-REF-B', status=won, region=region)

    def test_no_assignments_is_not_occupied(self):
        self.assertEqual(self.resource.engagement_status, 'not_occupied')
        self.assertEqual(self.resource.current_assignments, [])

    def test_one_open_ended_assignment_is_occupied(self):
        self.resource.assignments.create(project=self.project_a, start_date=self.last_week)
        self.assertEqual(self.resource.engagement_status, 'occupied')
        self.assertEqual(len(self.resource.current_assignments), 1)

    def test_two_overlapping_assignments_is_overoccupied(self):
        self.resource.assignments.create(project=self.project_a, start_date=self.last_week)
        self.resource.assignments.create(project=self.project_b, start_date=self.yesterday)
        self.assertEqual(self.resource.engagement_status, 'overoccupied')
        self.assertEqual(len(self.resource.current_assignments), 2)

    def test_an_assignment_that_already_ended_does_not_count(self):
        self.resource.assignments.create(
            project=self.project_a, start_date=self.last_week, end_date=self.yesterday)
        self.assertEqual(self.resource.engagement_status, 'not_occupied')

    def test_an_assignment_that_has_not_started_yet_does_not_count(self):
        self.resource.assignments.create(project=self.project_a, start_date=self.tomorrow)
        self.assertEqual(self.resource.engagement_status, 'not_occupied')

    def test_ending_today_still_counts_as_current(self):
        self.resource.assignments.create(
            project=self.project_a, start_date=self.last_week, end_date=self.today)
        self.assertEqual(self.resource.engagement_status, 'occupied')

    def test_a_past_and_a_current_assignment_together_is_just_occupied(self):
        """Only the live one counts toward the status - history doesn't
        make someone look busier than they actually are."""
        self.resource.assignments.create(
            project=self.project_a, start_date=self.last_week, end_date=self.yesterday)
        self.resource.assignments.create(project=self.project_b, start_date=self.today)
        self.assertEqual(self.resource.engagement_status, 'occupied')
        self.assertEqual(len(self.resource.current_assignments), 1)


class ManpowerAssignViewTests(TestCase):
    """manpower_assign - the form that actually creates a ManpowerAssignment
    row, distinct from manpower_edit's general profile fields."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.pm_user = User.objects.create_user('assign_pm', password='pw', role=self.pm_role)
        self.employee = Employee.objects.create(
            iqama_number='3000000001', full_name='Assign View Employee', designation='Engineer')
        region = Region.objects.create(name='AV Region', code='AVREG')
        won = ProjectStatus.objects.create(name='Won-AV', category='won')
        self.project = Project.objects.create(
            project_name='AV Project', proposal_reference='AV-REF-1', status=won, region=region)

    def test_requires_the_profile_to_exist_first(self):
        """No ManpowerResource yet - nothing to attach an assignment to."""
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:manpower_assign', args=[self.employee.pk]))
        self.assertRedirects(r, reverse('pmo:manpower_list'))

    def test_pm_can_open_the_form_once_a_profile_exists(self):
        ManpowerResource.objects.create(employee=self.employee)
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:manpower_assign', args=[self.employee.pk]))
        self.assertEqual(r.status_code, 200)

    def test_posting_creates_an_assignment(self):
        resource = ManpowerResource.objects.create(employee=self.employee)
        self.client.force_login(self.pm_user)
        r = self.client.post(reverse('pmo:manpower_assign', args=[self.employee.pk]), {
            'project': self.project.pk,
            'start_date': '2026-01-01',
            'end_date': '',
            'role_on_project': '',
        })
        self.assertRedirects(r, reverse('pmo:manpower_list'))
        assignment = resource.assignments.get()
        self.assertEqual(assignment.project, self.project)
        self.assertEqual(assignment.created_by, self.pm_user)
        self.assertEqual(resource.engagement_status, 'occupied')


class ManpowerDashboardViewTests(TestCase):
    """manpower_dashboard - KPI counts and the Overoccupied list, built
    on live ManpowerAssignment data rather than a manually re-tallied
    summary block."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        self.pm_role, _ = Role.objects.get_or_create(name=Role.PROJECT_MANAGER)
        self.pm_user = User.objects.create_user('dash_pm', password='pw', role=self.pm_role)
        region = Region.objects.create(name='DB Region', code='DBREG')
        won = ProjectStatus.objects.create(name='Won-DB', category='won')
        self.project_a = Project.objects.create(
            project_name='DB Project A', proposal_reference='DB-REF-A', status=won, region=region)
        self.project_b = Project.objects.create(
            project_name='DB Project B', proposal_reference='DB-REF-B', status=won, region=region)

        def make_resource(iqama, name):
            emp = Employee.objects.create(iqama_number=iqama, full_name=name, designation='Engineer')
            return ManpowerResource.objects.create(employee=emp)

        self.not_occ = make_resource('4000000001', 'Not Occupied Person')
        self.occ = make_resource('4000000002', 'Occupied Person')
        self.occ.assignments.create(project=self.project_a, start_date=date.today())
        self.over = make_resource('4000000003', 'Overoccupied Person')
        self.over.assignments.create(project=self.project_a, start_date=date.today())
        self.over.assignments.create(project=self.project_b, start_date=date.today())

    def test_kpi_counts_are_correct(self):
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:manpower_dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total'], 3)
        self.assertEqual(r.context['occupied'], 1)
        self.assertEqual(r.context['not_occupied'], 1)
        self.assertEqual(r.context['overoccupied'], 1)

    def test_overoccupied_list_contains_only_the_double_booked_resource(self):
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:manpower_dashboard'))
        self.assertEqual(list(r.context['overoccupied_list']), [self.over])
        self.assertContains(r, 'Overoccupied Person')
        self.assertNotContains(r, 'Occupied Person')

    def test_heatmap_counts_current_assignments_by_project_and_discipline(self):
        """Three current assignments total (occ on A, over on A and B),
        all with no discipline set, so they collapse into one
        Uncategorized row: 2 on Project A, 1 on Project B."""
        self.client.force_login(self.pm_user)
        r = self.client.get(reverse('pmo:manpower_dashboard'))
        self.assertEqual(r.context['heatmap_projects'], ['DB Project A', 'DB Project B'])
        rows = r.context['heatmap_rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['discipline'], 'Uncategorized')
        self.assertEqual(
            rows[0]['cells'],
            [{'count': 2, 'level': 4}, {'count': 1, 'level': 2}])
        self.assertEqual(rows[0]['total'], 3)
