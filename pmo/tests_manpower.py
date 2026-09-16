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
