"""Who reaches the delivery board, and what the weekly update accepts.

The numbers on this board feed the company's view of every project, so the
guards worth testing are the ones that stop a figure being moved by the wrong
person, onto the wrong row, or to a value that cannot mean anything.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from pmo.models import MilestoneProgressEntry, ProjectMilestone
from projects.models import Project, ProjectStatus, Region

User = get_user_model()


class DeliveryTestCase(TestCase):

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.region = Region.objects.create(name='KSA', code='DLV', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='open')
        self.project = self.make_project('DLV-1')
        self.parent = ProjectMilestone.objects.create(
            project=self.project, order=1, activity='Delivery', weightage=Decimal('1'))
        self.leaf = ProjectMilestone.objects.create(
            project=self.project, parent=self.parent, order=1,
            activity='Material at site', weightage=Decimal('1'),
            completed_fraction=Decimal('0.25'))

    def make_project(self, reference):
        return Project.objects.create(
            project_name=f'Project {reference}', proposal_reference=reference,
            region=self.region, status=self.status)

    def user(self, username, role_name):
        return User.objects.create_user(
            username, password='x', region=self.region,
            role=Role.objects.get(name=role_name))

    def post_progress(self, who, value, milestone=None):
        self.client.force_login(who)
        return self.client.post(
            reverse('pmo:update_progress', args=[(milestone or self.leaf).pk]),
            {'completed_fraction': value})


class AccessTests(DeliveryTestCase):

    def test_the_delivery_roles_can_see_the_board(self):
        for role in (Role.PROJECT_MANAGER, Role.SITE_MANAGER, Role.SUPER_ADMIN):
            with self.subTest(role=role):
                self.client.force_login(self.user(f'see-{role}', role))
                self.assertEqual(self.client.get(reverse('pmo:board')).status_code, 200)

    def test_a_role_outside_the_department_is_refused(self):
        """Refused, not shown an empty board — an empty page reads as 'no
        projects' and sends somebody asking why their work has vanished."""
        self.client.force_login(self.user('proc', Role.PROCUREMENT_OFF))
        self.assertEqual(self.client.get(reverse('pmo:board')).status_code, 403)

    def test_the_sidebar_gate_and_the_view_gate_agree(self):
        """The sidebar condition in base.html and can_see_delivery are written
        separately and can drift apart. A link offered to somebody who then
        gets a 403 is worse than no link at all.

        Checked by loading the board itself, which extends base.html: an
        allowed role must see the department in its own sidebar, and a denied
        role must not reach the page.
        """
        from pmo.views import can_see_delivery
        for role in (Role.PROJECT_MANAGER, Role.SITE_MANAGER, Role.MANAGER,
                     Role.ADMIN, Role.SUPER_ADMIN, Role.PROCUREMENT_OFF,
                     Role.FINANCE_REP, Role.SALES_REP):
            with self.subTest(role=role):
                person = self.user(f'nav-{role}', role)
                self.client.force_login(person)
                response = self.client.get(reverse('pmo:board'))
                if can_see_delivery(person):
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('/delivery/', response.content.decode())
                else:
                    self.assertEqual(response.status_code, 403)


class UpdateProgressTests(DeliveryTestCase):

    def setUp(self):
        super().setUp()
        self.pm = self.user('pm', Role.PROJECT_MANAGER)

    def test_a_project_manager_can_move_a_figure(self):
        response = self.post_progress(self.pm, '0.75')
        self.assertEqual(response.status_code, 200)
        self.leaf.refresh_from_db()
        self.assertEqual(self.leaf.completed_fraction, Decimal('0.7500'))

    def test_every_change_is_recorded(self):
        """Who and when. This is what makes the board's last-updated column
        answerable at all — the workbook's version was TODAY()."""
        self.post_progress(self.pm, '0.75')
        entry = MilestoneProgressEntry.objects.get()
        self.assertEqual(entry.completed_fraction, Decimal('0.7500'))
        self.assertEqual(entry.recorded_by, self.pm)

    def test_the_history_is_appended_not_overwritten(self):
        self.post_progress(self.pm, '0.5')
        self.post_progress(self.pm, '0.75')
        self.assertEqual(MilestoneProgressEntry.objects.count(), 2)

    def test_a_summary_row_refuses_progress(self):
        """A parent's figure is the sum of its children. Accepting one here
        would let the same weight be claimed twice and put the project above
        100%."""
        response = self.post_progress(self.pm, '1', milestone=self.parent)
        self.assertEqual(response.status_code, 400)
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.completed_fraction, Decimal('0'))

    def test_a_fraction_above_one_is_refused(self):
        """75 meaning 75% is the obvious slip, and it would put one activity at
        7500% of its weight."""
        response = self.post_progress(self.pm, '75')
        self.assertEqual(response.status_code, 400)
        self.leaf.refresh_from_db()
        self.assertEqual(self.leaf.completed_fraction, Decimal('0.2500'))

    def test_a_negative_fraction_is_refused(self):
        self.assertEqual(self.post_progress(self.pm, '-0.5').status_code, 400)

    def test_something_that_is_not_a_number_is_refused(self):
        self.assertEqual(self.post_progress(self.pm, 'nearly done').status_code, 400)

    def test_a_refused_value_leaves_no_history_behind(self):
        """A rejected update that still logged would make the board report a
        change that never happened."""
        self.post_progress(self.pm, '75')
        self.assertFalse(MilestoneProgressEntry.objects.exists())

    def test_a_read_only_role_cannot_move_a_figure(self):
        """Manager sees the board but does not update it — reading and writing
        these numbers are separate decisions."""
        response = self.post_progress(self.user('mgr', Role.MANAGER), '0.9')
        self.assertEqual(response.status_code, 403)
        self.leaf.refresh_from_db()
        self.assertEqual(self.leaf.completed_fraction, Decimal('0.2500'))

    def test_a_milestone_on_an_invisible_project_is_not_found(self):
        """Scoping is enforced on the update endpoint too, not only on the
        page that renders the grid."""
        other_region = Region.objects.create(name='UK', code='OTH', currency='GBP')
        far = Project.objects.create(
            project_name='Elsewhere', proposal_reference='OTH-1',
            region=other_region, status=self.status)
        parent = ProjectMilestone.objects.create(
            project=far, order=1, activity='Top', weightage=Decimal('1'))
        leaf = ProjectMilestone.objects.create(
            project=far, parent=parent, order=1, activity='Work',
            weightage=Decimal('1'))
        scoped = self.user('scoped-pm', Role.PROJECT_MANAGER)
        self.assertEqual(self.post_progress(scoped, '0.5', milestone=leaf).status_code, 404)


class BoardQueryTests(DeliveryTestCase):

    def _build(self, count, prefix):
        for i in range(count):
            project = self.make_project(f'{prefix}-{i}')
            parent = ProjectMilestone.objects.create(
                project=project, order=1, activity='Delivery', weightage=Decimal('1'))
            ProjectMilestone.objects.create(
                project=project, parent=parent, order=1, activity='Work',
                weightage=Decimal('1'), completed_fraction=Decimal('0.5'))

    def test_the_board_does_not_query_per_project(self):
        """Compared across two sizes rather than pinned to a number: a fixed
        count only records what the code does today and gets bumped whenever
        it changes, which is how an N+1 gets waved through.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        admin = self.user('board-admin', Role.SUPER_ADMIN)
        self.client.force_login(admin)

        self._build(3, 'SMALL')
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse('pmo:board'))

        self._build(9, 'BIG')
        with CaptureQueriesContext(connection) as big:
            self.client.get(reverse('pmo:board'))

        self.assertEqual(len(big.captured_queries), len(small.captured_queries))
