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

    def test_unpriced_pdf_omits_pricing(self):
        from procurement.models import PurchaseOrderItem
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=1,
            description='Widget', quantity=2, rate_per_unit=100)
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader

        def pdf_text(url_name):
            r = self.client.get(
                reverse(url_name, kwargs={'pk': self.po.pk}))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r['Content-Type'], 'application/pdf')
            reader = PdfReader(io.BytesIO(r.content))
            return '\n'.join((p.extract_text() or '') for p in reader.pages)

        priced = pdf_text('procurement:po_export_pdf')
        self.assertIn('Rate/Unit', priced)
        self.assertIn('Total (SAR)', priced)

        unpriced = pdf_text('procurement:po_export_pdf_unpriced')
        self.assertNotIn('Rate/Unit', unpriced)
        self.assertNotIn('Total (SAR)', unpriced)
        self.assertNotIn('Amount in words', unpriced)
        self.assertIn('UNPRICED', unpriced)

    def test_out_of_scope_user_gets_404(self):
        # A plain user who didn't create the PO can't see (or sign) it.
        outsider = User.objects.create_user('outsider', password='pw')
        self.client.force_login(outsider)
        r = self.client.post(self._url('scm'), {'signature_data': _png_data_url()})
        self.assertEqual(r.status_code, 404)
        self.po.refresh_from_db()
        self.assertIsNone(self.po.scm_approved_at)

    def test_storage_failure_returns_json_500_not_html(self):
        # Simulate object storage / DB write blowing up during the save. The
        # endpoint must answer with JSON 500 carrying the exception detail, not
        # an HTML 500 page (which the browser reports as "Network error").
        with mock.patch.object(PurchaseOrder, 'save', side_effect=Exception('R2 down')):
            r = self.client.post(self._url('scm'),
                                 {'signature_data': _png_data_url()})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r['Content-Type'], 'application/json')
        err = r.json()['error']
        self.assertIn('Approval failed', err)
        self.assertIn('R2 down', err)
        # Nothing should have been committed.
        self.po.refresh_from_db()
        self.assertIsNone(self.po.scm_approved_at)


class POEditFormSetTests(TestCase):
    """Editing a PO must persist line-item changes even when an existing row
    has a blank description — previously one such row silently blocked the
    entire save (the formset was invalid and no error was surfaced)."""

    def setUp(self):
        from procurement.models import PurchaseOrderItem
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('boss', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-EDIT-1', vendor_name='V',
            po_issued_by='T', cost_center='projects', created_by=self.user)
        self.good = PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=1, description='Good',
            quantity=1, uom='Nos', rate_per_unit=10, order=0)
        self.blank = PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=2, description='',
            quantity=1, uom='Nos', rate_per_unit=5, order=1)

    def _row(self, i, item, desc, qty):
        return {
            f'items-{i}-id': str(item.pk) if item else '',
            f'items-{i}-serial_number': str(i + 1),
            f'items-{i}-description': desc,
            f'items-{i}-quantity': qty, f'items-{i}-uom': 'Nos',
            f'items-{i}-rate_per_unit': '10', f'items-{i}-order': str(i),
            f'items-{i}-system': '', f'items-{i}-make_model': '',
            f'items-{i}-remarks': '', f'items-{i}-po_value_usd': '',
            f'items-{i}-advance_payment_sar': '', f'items-{i}-delivery_status': '',
            f'items-{i}-scm': '',
        }

    def test_edit_persists_despite_blank_description_row(self):
        data = {
            'po_date': '2026-01-01', 'po_number': 'PO-EDIT-1',
            'cost_center': 'projects', 'status': self.po.status,
            'vendor_name': 'V', 'po_issued_by': 'T', 'vendor_contact_email': '',
            'issuer_email': '', 'project_name': '', 'currency': 'SAR',
            'discount_rate': '0',
            'vat_rate': '15', 'lead_time': '', 'payment_terms_text': '',
            'warranty': '', 'terms_and_conditions': '', 'delivery_incoterms': '',
            'delivery_location': '',
            'items-TOTAL_FORMS': '3', 'items-INITIAL_FORMS': '2',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
        }
        data.update(self._row(0, self.good, 'Good EDITED', '9'))
        data.update(self._row(1, self.blank, '', '7'))            # blank desc kept
        data.update(self._row(2, None, 'BRAND NEW', '4'))         # new row

        r = self.client.post(
            reverse('procurement:po_update', kwargs={'pk': self.po.pk}), data)
        self.assertEqual(r.status_code, 302)  # saved + redirected

        self.good.refresh_from_db()
        self.blank.refresh_from_db()
        self.assertEqual(self.good.quantity, 9)        # edit persisted
        self.assertEqual(self.blank.quantity, 7)       # blank-desc row updated
        self.assertTrue(
            self.po.items.filter(description='BRAND NEW').exists())  # new row added


class POImportExcelTests(TestCase):
    """Excel import must bring in ALL line items, not just the first ~18 — the
    old parser hard-capped at row 30 and silently dropped the rest, and read
    discount/VAT from fixed rows that large POs push out of range."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('imp', password='pw', role=self.sa_role)
        self.client.force_login(self.user)

    def _build_workbook(self, n_items, po_number='IMP-TEST'):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PURCHASE ORDER'
        ws['B1'] = '01-Jan-2026'; ws['B2'] = po_number; ws['B3'] = 'Projects'
        ws['B4'] = 'Vendor X'
        ws['F1'] = 'Issuer'
        ws.cell(row=11, column=1, value='S No.')
        r = 12
        for i in range(1, n_items + 1):
            ws.cell(row=r, column=1, value=i)
            ws.cell(row=r, column=2, value=f'Make-{i}')
            ws.cell(row=r, column=3, value=f'Item {i}')
            ws.cell(row=r, column=6, value=i)
            ws.cell(row=r, column=7, value='Nos')
            ws.cell(row=r, column=8, value=10.0)
            ws.cell(row=r, column=10, value=f'rem{i}')
            r += 1
        r += 1  # blank spacer
        for label, c6, c9 in [('Base Amount', None, 1000), ('Discount', 0.05, 50),
                              ('Gross Value', None, 950), ('VAT', 0.15, 142.5),
                              ('Total Value in SAR', None, 1092.5)]:
            ws.cell(row=r, column=3, value=label)
            if c6 is not None:
                ws.cell(row=r, column=6, value=c6)
            ws.cell(row=r, column=9, value=c9)
            r += 1
        r += 2
        ws.cell(row=r, column=1, value='TERMS AND CONDITIONS')
        ws.cell(row=r + 1, column=2, value='Payment 30 days')
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        return buf.read()

    def _import(self, content):
        from django.core.files.uploadedfile import SimpleUploadedFile
        up = SimpleUploadedFile(
            'po.xlsx', content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        return self.client.post(reverse('procurement:po_import'), {'excel_file': up})

    def test_imports_all_items_beyond_old_row_cap(self):
        r = self._import(self._build_workbook(25, 'IMP-25'))
        self.assertEqual(r.status_code, 302)
        po = PurchaseOrder.objects.get(po_number='IMP-25')
        self.assertEqual(po.items.count(), 25)
        # discount/VAT parsed from the totals block (now well below row 30)
        self.assertEqual(po.discount_rate, 5)
        self.assertEqual(po.vat_rate, 15)
        self.assertEqual(po.items.order_by('order').last().description, 'Item 25')

    def test_small_po_still_imports(self):
        r = self._import(self._build_workbook(3, 'IMP-3'))
        self.assertEqual(r.status_code, 302)
        po = PurchaseOrder.objects.get(po_number='IMP-3')
        self.assertEqual(po.items.count(), 3)
        self.assertIn('Payment 30 days', po.terms_and_conditions)


class POCurrencyTests(TestCase):
    """A PO carries a selectable currency (SAR/USD/EUR/AED) that drives the
    rate/total labels, the amount-in-words fraction unit, and the exports."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('cur', password='pw', role=self.sa_role)
        self.client.force_login(self.user)

    def test_amount_in_words_fraction_unit_per_currency(self):
        from procurement.views import _amount_in_words
        from decimal import Decimal
        self.assertIn('Halalas', _amount_in_words(Decimal('10.50'), 'SAR'))
        self.assertIn('Cents', _amount_in_words(Decimal('10.50'), 'USD'))
        self.assertIn('Cents', _amount_in_words(Decimal('10.50'), 'EUR'))
        self.assertIn('Fils', _amount_in_words(Decimal('10.50'), 'AED'))

    def test_excel_headers_show_currency(self):
        from procurement.models import PurchaseOrderItem
        from django.utils import timezone
        import io, openpyxl
        po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='CUR-1', vendor_name='V',
            po_issued_by='T', cost_center='projects', currency='AED',
            created_by=self.user)
        PurchaseOrderItem.objects.create(
            purchase_order=po, serial_number=1, description='X', quantity=1,
            uom='Nos', rate_per_unit=10, order=0)
        for s in po.required_stages:        # release so export is unlocked
            setattr(po, f'{s}_approved_at', timezone.now())
            setattr(po, f'{s}_approved_by', self.user)
        po.save()
        r = self.client.get(reverse('procurement:po_export', kwargs={'pk': po.pk}))
        self.assertEqual(r.status_code, 200)
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(ws.cell(row=11, column=8).value, 'Rate/unit (AED)')
        self.assertEqual(ws.cell(row=11, column=9).value, 'Total Value (AED)')
