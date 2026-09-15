"""Deletes must not happen on GET. Audit F05.

A GET is fired by link prefetchers, crawlers, browser previews, an <img src>
on any page an admin opens, and a pasted link. Three document deletes acted on
GET with no confirmation beyond a JavaScript prompt the request never passed
through. This file is the audit's acceptance check: GET and HEAD are refused
with no side effect, a POST without a CSRF token is refused, an authorized
POST succeeds - plus a sweep so a new GET-deleting view cannot appear quietly.
"""

import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from company.models import CompanyDocument
from hr.models import EmployeeDocument, Vehicle, VehicleDocument
from hr.tests import make_employee

MEDIA = tempfile.mkdtemp()


def pdf():
    return SimpleUploadedFile('doc.pdf', b'%PDF-1.4 test', content_type='application/pdf')


@override_settings(MEDIA_ROOT=MEDIA)
class PostOnlyDeleteTests(TestCase):

    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.admin = User.objects.create_user('del_admin', password='x', role=role)
        self.client.force_login(self.admin)

        employee = make_employee(iqama='DEL-1', name='Doc Owner')
        vehicle = Vehicle.objects.create(plate_number='DEL-123')
        self.cases = {
            'company': (
                CompanyDocument,
                CompanyDocument.objects.create(title='Policy', file=pdf()),
                'company:document_delete'),
            'employee': (
                EmployeeDocument,
                EmployeeDocument.objects.create(employee=employee, file=pdf()),
                'hr:employee_doc_delete'),
            'vehicle': (
                VehicleDocument,
                VehicleDocument.objects.create(vehicle=vehicle, title='Registration',
                                               file=pdf()),
                'hr:vehicle_doc_delete'),
        }

    def _url(self, name):
        _model, obj, url_name = self.cases[name]
        return reverse(url_name, args=[obj.pk])

    def _exists(self, name):
        model, obj, _url = self.cases[name]
        return model.objects.filter(pk=obj.pk).exists()

    def test_get_is_refused_and_deletes_nothing(self):
        for name in self.cases:
            with self.subTest(name):
                resp = self.client.get(self._url(name))
                self.assertEqual(resp.status_code, 405)
                self.assertTrue(self._exists(name), f'{name} was deleted by a GET')

    def test_head_is_refused_and_deletes_nothing(self):
        for name in self.cases:
            with self.subTest(name):
                resp = self.client.head(self._url(name))
                self.assertEqual(resp.status_code, 405)
                self.assertTrue(self._exists(name), f'{name} was deleted by a HEAD')

    def test_a_post_without_a_csrf_token_is_refused(self):
        """The test client skips CSRF by default, which would hide exactly
        the check this is meant to prove."""
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        for name in self.cases:
            with self.subTest(name):
                resp = strict.post(self._url(name))
                self.assertEqual(resp.status_code, 403)
                self.assertTrue(self._exists(name))

    def test_an_authorized_post_deletes(self):
        for name in self.cases:
            with self.subTest(name):
                resp = self.client.post(self._url(name))
                self.assertEqual(resp.status_code, 302)
                self.assertFalse(self._exists(name))

    def test_an_unauthorized_post_deletes_nothing(self):
        """POST-only must not have loosened the role check behind it."""
        role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        rep = User.objects.create_user('del_rep', password='x', role=role)
        self.client.force_login(rep)
        for name in self.cases:
            with self.subTest(name):
                self.client.post(self._url(name))
                self.assertTrue(self._exists(name))

    def test_the_pages_offer_a_form_not_a_link(self):
        """A delete rendered as <a href> would now 405 when clicked."""
        employee_doc = self.cases['employee'][1]
        vehicle_doc = self.cases['vehicle'][1]
        pages = {
            reverse('company:document_list'): self._url('company'),
            reverse('hr:employee_detail', args=[employee_doc.employee_id]): self._url('employee'),
            reverse('hr:vehicle_detail', args=[vehicle_doc.vehicle_id]): self._url('vehicle'),
        }
        for page, delete_url in pages.items():
            with self.subTest(page):
                html = self.client.get(page).content.decode()
                self.assertIn(f'action="{delete_url}"', html)
                self.assertNotIn(f'href="{delete_url}"', html)


class NoGetDeletingViewsTests(TestCase):
    """Every function view whose name says it deletes must refuse GET.

    The audit found three. There are around twenty such views, and the rest
    were already guarded - this keeps the next one honest rather than relying
    on someone remembering to check.
    """

    GUARD = re.compile(
        r'@require_POST|@require_http_methods\(\s*\[\s*[\'"]POST[\'"]\s*\]\s*\)'
        r'|request\.method\s*(?:!=|==)\s*[\'"]POST[\'"]')

    def test_every_delete_function_view_requires_post(self):
        base = Path(settings.BASE_DIR)
        offenders = []
        for path in base.glob('*/views*.py'):
            if 'venv' in path.parts:
                continue
            lines = path.read_text(encoding='utf-8').splitlines()
            for i, line in enumerate(lines):
                match = re.match(r'def ([a-z_]*delete[a-z_]*)\(request', line)
                if not match:
                    continue
                decorators = []
                j = i - 1
                while j >= 0 and lines[j].startswith('@'):
                    decorators.append(lines[j])
                    j -= 1
                window = '\n'.join(decorators + lines[i:i + 40])
                if not self.GUARD.search(window):
                    offenders.append(f'{path.relative_to(base)}:{match.group(1)}')
        self.assertEqual(offenders, [], 'delete views that act on GET')
