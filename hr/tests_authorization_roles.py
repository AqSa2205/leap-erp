"""Tests for the four HR/administration authorization roles:
project_manager, site_manager, erp_admin, document_controller.

Covers: role/capability seeding, the org-chart scope engine, ERP Admin's
global admin-section access (and its exclusions), the team-scoped roles'
data filtering across attendance/assets/org-chart/KPIs, and the Document
Controller's lack of approval authority.
"""
from datetime import date as _date, time as _time

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from accounts.models import Role, RolePermission
from hr.models import (
    Employee, Asset, AssetAssignment, AttendanceRecord, AttendanceException,
    LeaveType,
)
from hr.scoping import scoped_employee_ids
from hr.views import can_decide_attendance_exception

User = get_user_model()


def _role(name):
    r, _ = Role.objects.get_or_create(name=name)
    return r


def _emp(iqama, name, manager=None):
    return Employee.objects.create(
        iqama_number=iqama, full_name=name, main_manager=manager, is_active=True)


def _user(username, role_name, employee=None):
    u = User.objects.create_user(username, password='x')
    u.role = _role(role_name)
    u.save()
    if employee is not None:
        employee.user = u
        employee.save()
    return u


class RoleSeedingTests(TestCase):
    """Migration 0028 creates the rows + seeds capabilities."""

    def test_roles_exist(self):
        for name in ['project_manager', 'site_manager', 'erp_admin', 'document_controller']:
            self.assertTrue(Role.objects.filter(name=name).exists(), name)

    def _allowed(self, role_name):
        return set(
            RolePermission.objects.filter(role__name=role_name, allowed=True)
            .values_list('codename', flat=True))

    def test_scoped_roles_get_kpis_but_not_manage(self):
        for name in ['project_manager', 'site_manager', 'document_controller']:
            allowed = self._allowed(name)
            self.assertIn('kpis.access', allowed, name)
            self.assertIn('kpis.activity', allowed, name)
            self.assertNotIn('kpis.manage', allowed, name)   # no company KPI editing
            self.assertIn('dashboard.access', allowed, name)

    def test_erp_admin_has_dashboard_only(self):
        allowed = self._allowed('erp_admin')
        self.assertIn('dashboard.access', allowed)
        self.assertNotIn('kpis.access', allowed)


class ScopeEngineTests(TestCase):
    """scoped_employee_ids: subtree for PM/DC, direct-only for Site Manager,
    None (unrestricted) for the admin tiers."""

    def setUp(self):
        # mgr -> a, b ; a -> c  (c is a grand-report of mgr)
        self.mgr = _emp('M', 'Manager Person')
        self.a = _emp('A', 'Report A', manager=self.mgr)
        self.b = _emp('B', 'Report B', manager=self.mgr)
        self.c = _emp('C', 'Report C', manager=self.a)

    def test_project_manager_sees_whole_subtree(self):
        u = _user('pm', 'project_manager', employee=self.mgr)
        self.assertEqual(scoped_employee_ids(u), {self.a.pk, self.b.pk, self.c.pk})

    def test_document_controller_sees_whole_subtree(self):
        u = _user('dc', 'document_controller', employee=self.mgr)
        self.assertEqual(scoped_employee_ids(u), {self.a.pk, self.b.pk, self.c.pk})

    def test_site_manager_sees_direct_reports_only(self):
        u = _user('sm', 'site_manager', employee=self.mgr)
        self.assertEqual(scoped_employee_ids(u), {self.a.pk, self.b.pk})  # not c

    def test_erp_admin_unrestricted(self):
        u = _user('erp', 'erp_admin')
        self.assertIsNone(scoped_employee_ids(u))

    def test_scoped_role_without_employee_sees_nobody(self):
        u = _user('pm2', 'project_manager')   # no linked Employee
        self.assertEqual(scoped_employee_ids(u), set())


class ERPAdminAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.erp = _user('erp', 'erp_admin')
        self.client.force_login(self.erp)

    def test_can_open_hr_admin_pages(self):
        for name in ['hr:hr_dashboard', 'hr:employee_list', 'hr:attendance_matrix',
                     'hr:asset_list', 'hr:org_chart']:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)

    def test_blocked_from_super_admin_only_tools(self):
        # Users + Permission Matrix stay Super-Admin only.
        for name in ['accounts:user_list', 'accounts:permission_matrix']:
            resp = self.client.get(reverse(name))
            self.assertIn(resp.status_code, (302, 403), name)


class AttendanceScopeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mgr = _emp('M', 'Manager Person')
        self.direct = _emp('A', 'DirectReportZebra', manager=self.mgr)
        self.grand = _emp('C', 'GrandReportZebra', manager=self.direct)
        self.outsider = _emp('O', 'OutsiderZebra')
        self.annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': 30})

    def test_site_manager_matrix_shows_direct_only(self):
        u = _user('sm', 'site_manager', employee=self.mgr)
        self.client.force_login(u)
        resp = self.client.get(reverse('hr:attendance_matrix'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('DirectReportZebra', body)
        self.assertNotIn('GrandReportZebra', body)   # deeper than direct
        self.assertNotIn('OutsiderZebra', body)

    def test_project_manager_matrix_shows_subtree(self):
        u = _user('pm', 'project_manager', employee=self.mgr)
        self.client.force_login(u)
        body = self.client.get(reverse('hr:attendance_matrix')).content.decode()
        self.assertIn('DirectReportZebra', body)
        self.assertIn('GrandReportZebra', body)
        self.assertNotIn('OutsiderZebra', body)

    def test_history_404_for_out_of_team(self):
        u = _user('sm', 'site_manager', employee=self.mgr)
        self.client.force_login(u)
        ok = self.client.get(reverse('hr:attendance_history', args=[self.direct.pk]))
        self.assertEqual(ok.status_code, 200)
        blocked = self.client.get(reverse('hr:attendance_history', args=[self.outsider.pk]))
        self.assertEqual(blocked.status_code, 404)

    def test_mark_leave_scoped_to_team(self):
        import json
        u = _user('sm', 'site_manager', employee=self.mgr)
        self.client.force_login(u)
        # Own team member: allowed.
        ok = self.client.post(
            reverse('hr:attendance_mark_leave'),
            data=json.dumps({'employee': self.direct.pk, 'date': '2026-03-02',
                             'leave_type': self.annual.pk}),
            content_type='application/json')
        self.assertEqual(ok.status_code, 200)
        # Outsider: forbidden.
        blocked = self.client.post(
            reverse('hr:attendance_mark_leave'),
            data=json.dumps({'employee': self.outsider.pk, 'date': '2026-03-02',
                             'leave_type': self.annual.pk}),
            content_type='application/json')
        self.assertEqual(blocked.status_code, 403)

    def test_scoped_role_cannot_open_employee_admin(self):
        # Employees list is admin-only (not a scoped-role feature).
        u = _user('sm', 'site_manager', employee=self.mgr)
        self.client.force_login(u)
        resp = self.client.get(reverse('hr:employee_list'))
        self.assertIn(resp.status_code, (302, 403))


class DocumentControllerTests(TestCase):
    def setUp(self):
        self.mgr = _emp('M', 'DC Person')
        self.report = _emp('A', 'A Report', manager=self.mgr)

    def test_no_approval_authority_even_as_manager(self):
        dc = _user('dc', 'document_controller', employee=self.mgr)
        exc = AttendanceException.objects.create(
            employee=self.report, event_date=_date(2026, 3, 2),
            event_start_time=_time(9, 0))
        # A PM (subtree manager) COULD decide; a Document Controller never can.
        self.assertFalse(can_decide_attendance_exception(dc, exc))

    def test_project_manager_can_decide(self):
        pm = _user('pm', 'project_manager', employee=self.mgr)
        exc = AttendanceException.objects.create(
            employee=self.report, event_date=_date(2026, 3, 2),
            event_start_time=_time(9, 0))
        self.assertTrue(can_decide_attendance_exception(pm, exc))


class OrgChartScopeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mgr = _emp('M', 'OC Manager')
        self.report = _emp('A', 'OC Report', manager=self.mgr)

    def test_scoped_role_readonly_no_manage(self):
        u = _user('pm', 'project_manager', employee=self.mgr)
        self.client.force_login(u)
        resp = self.client.get(reverse('hr:org_chart'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['can_manage_org'])
        # The per-row assignment form (management-only) must be absent. ("Assign
        # Managers" itself also appears in the page's CSS comments, so match the
        # editable table's hidden employee_id input instead.)
        self.assertNotIn('name="employee_id"', resp.content.decode())

    def test_scoped_role_post_blocked(self):
        u = _user('pm', 'project_manager', employee=self.mgr)
        self.client.force_login(u)
        resp = self.client.post(reverse('hr:org_chart'),
                                data={'employee_id': self.report.pk})
        self.assertEqual(resp.status_code, 403)

    def test_hierarchy_tree_uses_hybrid_layout_classes(self):
        # CEO -> VP -> Manager -> Team Lead -> Employee: the top THREE fan-outs
        # render horizontally (org-h); the fourth level down renders vertically
        # indented (org-v).
        ceo = _emp('CEO', 'Chief Exec')
        vp = _emp('VP', 'Veep', manager=ceo)
        mgr = _emp('MG', 'Middle Mgr', manager=vp)
        lead = _emp('TL', 'Team Lead', manager=mgr)
        _emp('EMP', 'Line Employee', manager=lead)
        sa, _ = Role.objects.get_or_create(name='super_admin')
        su = User.objects.create_user('sa', password='x'); su.role = sa; su.save()
        self.client.force_login(su)
        body = self.client.get(reverse('hr:org_chart')).content.decode()
        # Assert the real rendered class attributes (not just the substring —
        # otherwise a stray comment mentioning org-h/org-v could pass this).
        self.assertIn('class="org-h"', body)   # CEO->VP, VP->Manager (horizontal)
        self.assertIn('class="org-v"', body)   # Manager->Employee (vertical)
        self.assertIn('Line Employee', body)
        # The template-comment guidance must NOT leak into the page as text.
        self.assertNotIn('vertical threshold', body)

    def test_erp_admin_can_manage_but_not_config_access(self):
        u = _user('erp', 'erp_admin')
        self.client.force_login(u)
        resp = self.client.get(reverse('hr:org_chart'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['can_manage_org'])
        self.assertFalse(resp.context['can_config_access'])
        # Leave Dashboard Access actions are Super-Admin only -> 403 for ERP Admin.
        blocked = self.client.post(reverse('hr:org_chart'),
                                   data={'grant_dashboard_access': self.report.pk})
        self.assertEqual(blocked.status_code, 403)
        # Hierarchy assignment IS allowed for ERP Admin (redirects on save).
        ok = self.client.post(reverse('hr:org_chart'),
                              data={'employee_id': self.report.pk, 'main_manager': '',
                                    'secondary_managers': []})
        self.assertEqual(ok.status_code, 302)


class AssetScopeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mgr = _emp('M', 'Asset Manager')
        self.report = _emp('A', 'Asset Report', manager=self.mgr)
        self.outsider = _emp('O', 'Asset Outsider')
        # One asset held by the team member, one by an outsider.
        self.team_asset = Asset.objects.create(asset_name='TeamLaptopZebra')
        AssetAssignment.objects.create(
            asset=self.team_asset, employee=self.report, assigned_at=_date(2026, 1, 1))
        self.other_asset = Asset.objects.create(asset_name='OtherLaptopZebra')
        AssetAssignment.objects.create(
            asset=self.other_asset, employee=self.outsider, assigned_at=_date(2026, 1, 1))

    def test_scoped_role_sees_only_team_assets(self):
        u = _user('pm', 'project_manager', employee=self.mgr)
        self.client.force_login(u)
        resp = self.client.get(reverse('hr:asset_list'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('TeamLaptopZebra', body)
        self.assertNotIn('OtherLaptopZebra', body)
        self.assertFalse(resp.context['can_manage_assets'])   # read-only

    def test_erp_admin_sees_all_assets(self):
        u = _user('erp', 'erp_admin')
        self.client.force_login(u)
        body = self.client.get(reverse('hr:asset_list')).content.decode()
        self.assertIn('TeamLaptopZebra', body)
        self.assertIn('OtherLaptopZebra', body)


class KPIScopeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mgr = _emp('M', 'KPI Manager')
        self.report = _emp('A', 'KPI Report', manager=self.mgr)
        self.report_user = _user('rep', 'sales_rep', employee=self.report)
        self.outsider_user = _user('out', 'sales_rep')

    def test_activity_detail_blocked_for_out_of_team(self):
        u = _user('pm', 'project_manager', employee=self.mgr)
        self.client.force_login(u)
        resp = self.client.get(reverse('kpis:activity_detail', args=[self.outsider_user.pk]))
        self.assertEqual(resp.status_code, 403)


class SidebarRenderTests(TestCase):
    """Smoke test: base.html renders without error for each new role."""

    def test_pages_render_for_each_role(self):
        client = Client()
        for role_name in ['project_manager', 'site_manager', 'erp_admin', 'document_controller']:
            emp = _emp(f'E-{role_name}', f'Person {role_name}')
            u = _user(f'u-{role_name}', role_name, employee=emp)
            client.force_login(u)
            resp = client.get(reverse('hr:my_profile'))
            self.assertEqual(resp.status_code, 200, role_name)
