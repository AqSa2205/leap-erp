"""Loading the workbook from the browser.

This screen exists because the management command cannot be run in production —
the web service has no shell — so the import that actually matters has to work
from a page. The guards worth testing are the ones that stop it doing something
irreversible: replacing a project's WBS (and the progress history attached to
it) by accident, or applying something other than what was previewed.
"""
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from pmo.models import MilestoneProgressEntry, ProjectMilestone
from pmo.views import PREVIEW_SALT
from projects.models import Project, ProjectStatus, Region

User = get_user_model()


def build_workbook(sheet_name='LNA-2308-Milestones(MASCO)'):
    """A workbook in the MASCO layout: S.No at column B, no Value column."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = sheet_name
    rows = [
        (None, None, 'Amiral Project - MASCO'),
        (None, 'S. No', 'Activity', 'Activity Weightage', 'Completed', 'Pending',
         'Completed Activity Weightage', 'Completion\nDate',
         'Weightage of Pending Activit', 'Invoice Pre'),
        (None, '1', 'Documents', 0.1, None, None, None, None, None, None),
        (None, '1.1', 'Submission', 0.05, 1, 0, 0.05, None, 0, 'Transmittal'),
        (None, '1.2', 'Approval', 0.05, 1, 0, 0.05, None, 0, 'Transmittal'),
        (None, '2', 'Delivery', 0.9, None, None, None, None, None, None),
        (None, '2.1', 'Material at site', 0.9, 0.4933, 0.5067, 0.44397, None, 0, 'Note'),
        (None, None, 'Total', 1),
    ]
    for row in rows:
        sheet.append(list(row))
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class ImportScreenTestCase(TestCase):

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.region = Region.objects.create(name='KSA', code='IMP', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='open')
        self.project = Project.objects.create(
            project_name='Amiral MASCO', proposal_reference='LNA 2308 - Amiral',
            region=self.region, status=self.status)
        self.admin = self.user('imp-admin', Role.SUPER_ADMIN)

    def user(self, username, role_name):
        return User.objects.create_user(
            username, password='x', region=self.region,
            role=Role.objects.get(name=role_name))

    def upload(self, who=None, content=None, filename='overview.xlsx'):
        self.client.force_login(who or self.admin)
        return self.client.post(
            reverse('pmo:milestone_import'),
            {'workbook': SimpleUploadedFile(
                filename, content if content is not None else build_workbook(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})

    def apply(self, payload, replace=False, who=None):
        self.client.force_login(who or self.admin)
        data = {'payload': payload}
        if replace:
            data['replace'] = 'on'
        return self.client.post(reverse('pmo:milestone_import_apply'), data, follow=True)


class AccessTests(ImportScreenTestCase):

    def test_an_administrator_can_open_the_screen(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('pmo:milestone_import')).status_code, 200)

    def test_a_project_manager_cannot_import(self):
        """They update figures weekly; replacing the whole structure is a
        different decision and a destructive one."""
        pm = self.user('imp-pm', Role.PROJECT_MANAGER)
        self.client.force_login(pm)
        self.assertEqual(self.client.get(reverse('pmo:milestone_import')).status_code, 403)
        self.assertEqual(
            self.client.post(reverse('pmo:milestone_import_apply'),
                             {'payload': 'x'}).status_code, 403)

    def test_the_sidebar_link_and_the_view_gate_agree(self):
        from pmo.views import can_import_milestones
        for role in (Role.SUPER_ADMIN, Role.ADMIN, Role.MANAGER, Role.PROJECT_MANAGER):
            with self.subTest(role=role):
                person = self.user(f'nav-imp-{role}', role)
                self.client.force_login(person)
                body = self.client.get(reverse('pmo:board')).content.decode()
                self.assertEqual('/delivery/import/' in body,
                                 can_import_milestones(person))


class PreviewTests(ImportScreenTestCase):

    def test_the_preview_writes_nothing(self):
        """The whole point of the two steps. A preview that already wrote would
        make the confirm button a lie."""
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectMilestone.objects.exists())

    def test_the_preview_matches_the_sheet_to_its_project(self):
        response = self.upload()
        self.assertEqual(len(response.context['matched']), 1)
        self.assertEqual(response.context['matched'][0]['project'], self.project)

    def test_an_unmatched_sheet_is_reported_not_created(self):
        response = self.upload(content=build_workbook('LNA-9999-Milestones(Nobody)'))
        self.assertEqual(len(response.context['unmatched']), 1)
        self.assertEqual(Project.objects.count(), 1)

    def test_the_preview_says_when_a_project_already_has_milestones(self):
        ProjectMilestone.objects.create(
            project=self.project, order=1, activity='Existing', weightage=Decimal('1'))
        response = self.upload()
        self.assertEqual(len(response.context['already_have']), 1)

    def test_a_file_that_is_not_a_workbook_is_refused(self):
        response = self.upload(content=b'this is not a spreadsheet')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectMilestone.objects.exists())

    def test_a_workbook_with_no_milestone_sheets_is_refused(self):
        book = openpyxl.Workbook()
        book.active.title = 'Budget'
        buffer = BytesIO()
        book.save(buffer)
        self.assertEqual(self.upload(content=buffer.getvalue()).status_code, 302)


class ApplyTests(ImportScreenTestCase):

    def preview_payload(self):
        return self.upload().context['payload']

    def test_confirming_writes_the_tree(self):
        self.apply(self.preview_payload())
        self.assertEqual(self.project.milestones.count(), 5)
        parents = self.project.milestones.filter(parent__isnull=True)
        self.assertEqual(parents.count(), 2)

    def test_the_weights_survive_the_round_trip(self):
        """Decimals and dates do not survive JSON, so they are written as
        strings and read back. If that were wrong the import would look fine
        and every weight would be zero."""
        self.apply(self.preview_payload())
        leaf = self.project.milestones.get(activity='Material at site')
        self.assertEqual(leaf.weightage, Decimal('0.9000'))
        self.assertEqual(leaf.completed_fraction, Decimal('0.4933'))

    def test_the_tree_is_built_from_order_not_the_typed_numbering(self):
        """A child belongs to whichever parent it follows. The MASCO sheet has
        two rows both labelled 1.1, so trusting the typed number would put them
        under the wrong activity or collapse them."""
        self.apply(self.preview_payload())
        submission = self.project.milestones.get(activity='Submission')
        self.assertEqual(submission.parent.activity, 'Documents')
        material = self.project.milestones.get(activity='Material at site')
        self.assertEqual(material.parent.activity, 'Delivery')

    def test_a_project_with_milestones_is_left_alone_by_default(self):
        """Re-uploading the file is a normal thing to do. Silently discarding
        weeks of progress updates because somebody did it twice is not
        recoverable."""
        existing = ProjectMilestone.objects.create(
            project=self.project, order=1, activity='Existing', weightage=Decimal('1'))
        self.apply(self.preview_payload())
        self.assertEqual(self.project.milestones.count(), 1)
        self.assertTrue(self.project.milestones.filter(pk=existing.pk).exists())

    def test_replace_overwrites_when_it_is_asked_for(self):
        ProjectMilestone.objects.create(
            project=self.project, order=1, activity='Existing', weightage=Decimal('1'))
        self.apply(self.preview_payload(), replace=True)
        self.assertEqual(self.project.milestones.count(), 5)
        self.assertFalse(self.project.milestones.filter(activity='Existing').exists())

    def test_a_tampered_payload_is_refused(self):
        """It carries what will be written, so it is signed. Without the check
        a crafted post could write anything into any project."""
        payload = self.preview_payload()
        self.apply(payload[:-4] + 'aaaa')
        self.assertFalse(ProjectMilestone.objects.exists())

    def test_an_expired_preview_is_refused(self):
        """A preview older than an hour may describe projects that have moved
        on since, so it is re-uploaded rather than trusted. Signed payloads
        carry their own timestamp, so the age is forced here rather than
        waiting an hour."""
        from unittest import mock

        payload = self.preview_payload()
        with mock.patch('pmo.views.signing.loads',
                        side_effect=signing.SignatureExpired('too old')):
            self.apply(payload)
        self.assertFalse(ProjectMilestone.objects.exists())

    def test_an_empty_payload_is_refused(self):
        self.apply('')
        self.assertFalse(ProjectMilestone.objects.exists())

    def test_replacing_also_clears_the_progress_history(self):
        """Stated in the confirmation text, so it is worth pinning: entries
        cascade off the milestone they belong to."""
        milestone = ProjectMilestone.objects.create(
            project=self.project, order=1, activity='Existing', weightage=Decimal('1'))
        MilestoneProgressEntry.objects.create(
            milestone=milestone, completed_fraction=Decimal('0.5'), recorded_by=self.admin)
        self.apply(self.preview_payload(), replace=True)
        self.assertFalse(MilestoneProgressEntry.objects.exists())
