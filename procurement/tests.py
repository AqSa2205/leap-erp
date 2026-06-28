import base64
import io
from datetime import date
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from PIL import Image

from accounts.models import User, Role
from procurement.models import PurchaseOrder


def _png_data_url():
    buf = io.BytesIO()
    Image.new('RGBA', (8, 8), (0, 0, 0, 0)).save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


class POApproveStageTests(TestCase):
    """The Approve-&-Sign endpoint must always answer with JSON so the modal can
    show a real message — a storage failure must not leak an HTML 500 that the
    browser reports as an opaque 'Network error'."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user(
            'boss', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-TEST-1',
            vendor_name='ACME', po_issued_by='Tester',
            created_by=self.user)

    def _url(self, stage='scm'):
        return reverse('procurement:po_approve_stage',
                       kwargs={'pk': self.po.pk, 'stage': stage})

    def test_happy_path_signs_first_stage(self):
        r = self.client.post(self._url('scm'), {'signature_data': _png_data_url()})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])
        self.po.refresh_from_db()
        self.assertIsNotNone(self.po.scm_approved_at)

    def test_missing_signature_is_json_400(self):
        r = self.client.post(self._url('scm'), {})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r['Content-Type'], 'application/json')
        self.assertIn('signature', r.json()['error'].lower())

    def test_out_of_sequence_stage_is_json_400(self):
        # 'pm' before 'scm' is signed — rejected as out of sequence.
        r = self.client.post(self._url('pm'), {'signature_data': _png_data_url()})
        self.assertEqual(r.status_code, 400)
        self.assertIn('sequence', r.json()['error'].lower())

    def test_storage_failure_returns_json_500_not_html(self):
        # Simulate object storage / DB write blowing up during the save.
        with mock.patch.object(PurchaseOrder, 'save', side_effect=Exception('R2 down')):
            r = self.client.post(self._url('scm'),
                                 {'signature_data': _png_data_url()})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r['Content-Type'], 'application/json')
        self.assertIn('signature', r.json()['error'].lower())
        # Nothing should have been committed.
        self.po.refresh_from_db()
        self.assertIsNone(self.po.scm_approved_at)
