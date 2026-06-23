import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from company.models import CompanyDocument


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CompanyDocumentTests(TestCase):
    def setUp(self):
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.admin = User.objects.create_user('admin1', password='x')
        self.admin.role = sa
        self.admin.save()
        self.plain = User.objects.create_user('plain', password='x')

    def _upload(self, **overrides):
        data = {
            'title': 'Trade License', 'document_type': 'license',
            'custom_type': '', 'issuing_authority': 'MOC',
            'reference_number': 'TL-123', 'notes': '',
            'file': SimpleUploadedFile('license.pdf', b'%PDF-1.4 x',
                                       content_type='application/pdf'),
        }
        data.update(overrides)
        return self.client.post(reverse('company:document_upload'), data)

    def test_list_requires_admin(self):
        self.client.force_login(self.plain)
        self.assertEqual(
            self.client.get(reverse('company:document_list')).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse('company:document_list')).status_code, 200)

    def test_upload_creates_document(self):
        self.client.force_login(self.admin)
        self._upload()
        self.assertEqual(CompanyDocument.objects.count(), 1)
        doc = CompanyDocument.objects.first()
        self.assertEqual(doc.title, 'Trade License')
        self.assertEqual(doc.uploaded_by, self.admin)

    def test_other_type_requires_custom_label(self):
        self.client.force_login(self.admin)
        self._upload(document_type='other', custom_type='')
        self.assertEqual(CompanyDocument.objects.count(), 0)  # rejected by clean()

    def test_delete_reclaims_file(self):
        self.client.force_login(self.admin)
        self._upload()
        doc = CompanyDocument.objects.first()
        name = doc.file.name
        self.assertTrue(default_storage.exists(name))
        self.client.get(reverse('company:document_delete', kwargs={'pk': doc.pk}))
        self.assertEqual(CompanyDocument.objects.count(), 0)
        self.assertFalse(default_storage.exists(name))  # cleanup signal fired

    def test_edit_metadata_keeps_file(self):
        self.client.force_login(self.admin)
        self._upload()
        doc = CompanyDocument.objects.first()
        name = doc.file.name
        self.client.post(reverse('company:document_edit', kwargs={'pk': doc.pk}), {
            'title': 'Renamed License', 'document_type': 'license', 'custom_type': '',
            'issuing_authority': 'MOC', 'reference_number': 'TL-999', 'notes': 'updated',
        })  # no file -> keep existing
        doc.refresh_from_db()
        self.assertEqual(doc.title, 'Renamed License')
        self.assertEqual(doc.file.name, name)
        self.assertTrue(default_storage.exists(name))

    def test_edit_replacing_file_reclaims_old(self):
        self.client.force_login(self.admin)
        self._upload()
        doc = CompanyDocument.objects.first()
        old_name = doc.file.name
        self.client.post(reverse('company:document_edit', kwargs={'pk': doc.pk}), {
            'title': 'Trade License', 'document_type': 'license', 'custom_type': '',
            'issuing_authority': 'MOC', 'reference_number': 'TL-123', 'notes': '',
            'file': SimpleUploadedFile('newlicense.pdf', b'%PDF new',
                                       content_type='application/pdf'),
        })
        doc.refresh_from_db()
        self.assertNotEqual(doc.file.name, old_name)
        self.assertFalse(default_storage.exists(old_name))   # old reclaimed, no orphan
        self.assertTrue(default_storage.exists(doc.file.name))

    def test_edit_form_renders_for_admin(self):
        self.client.force_login(self.admin)
        self._upload()
        doc = CompanyDocument.objects.first()
        r = self.client.get(reverse('company:document_edit', kwargs={'pk': doc.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Edit Company Document', r.content)

    def test_edit_requires_admin(self):
        self.client.force_login(self.admin)
        self._upload()
        doc = CompanyDocument.objects.first()
        self.client.force_login(self.plain)
        r = self.client.get(reverse('company:document_edit', kwargs={'pk': doc.pk}))
        self.assertEqual(r.status_code, 302)  # bounced, not allowed
