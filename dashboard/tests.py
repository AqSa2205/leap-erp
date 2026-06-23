import tempfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StorageReportTests(TestCase):
    """The Storage admin page and the orphan-preview endpoint are super-admin
    only; preview redirects to an existing object and 404s otherwise."""

    def setUp(self):
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.super = User.objects.create_user('sa', password='x')
        self.super.role = sa
        self.super.save()
        self.plain = User.objects.create_user('plain', password='x')

    def test_report_page_super_admin_only(self):
        self.client.force_login(self.plain)
        self.assertEqual(
            self.client.get(reverse('dashboard:storage_report')).status_code, 403)
        self.client.force_login(self.super)
        self.assertEqual(
            self.client.get(reverse('dashboard:storage_report')).status_code, 200)

    def test_preview_requires_super_admin(self):
        self.client.force_login(self.plain)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': 'x.txt'})
        self.assertEqual(r.status_code, 403)

    def test_preview_missing_key_404(self):
        self.client.force_login(self.super)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': 'nope/missing.txt'})
        self.assertEqual(r.status_code, 404)

    def test_preview_existing_file_redirects(self):
        name = default_storage.save('orphan-preview.txt', ContentFile(b'hello'))
        self.client.force_login(self.super)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': name})
        self.assertEqual(r.status_code, 302)
