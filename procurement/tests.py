import base64
import io
from datetime import date
from decimal import Decimal
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


class POPdfFooterTests(TestCase):
    """The PO PDF footer shows the company (left), PO number (centre) and page
    number (right) on every page — on both the priced and unpriced exports."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('boss', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-FOOT-1', vendor_name='ACME',
            po_issued_by='Tester', cost_center='projects', created_by=self.user)

    def _pdf_text(self, url_name='procurement:po_export_pdf'):
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        r = self.client.get(reverse(url_name, kwargs={'pk': self.po.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(r.content)).pages)

    def test_footer_has_company_mr_po_number_and_page(self):
        text = ' '.join(self._pdf_text().split())   # normalise wrapping
        self.assertIn('Leap Networks Arabia', text)          # company (left, line 1)
        self.assertIn('Material Requisition R00', text)      # MR + default revision (left, line 2)
        self.assertIn('Page 1 of', text)                     # page label (right)
        # PO number appears in the header AND the footer centre -> at least twice.
        self.assertGreaterEqual(text.count('PO-FOOT-1'), 2)

    def test_footer_reflects_edited_revision(self):
        self.po.mr_revision = 'R02'
        self.po.save(update_fields=['mr_revision'])
        text = ' '.join(self._pdf_text().split())
        self.assertIn('Material Requisition R02', text)
        self.assertNotIn('Material Requisition R00', text)

    def test_revision_is_editable_on_the_form(self):
        from procurement.forms import PurchaseOrderForm
        self.assertIn('mr_revision', PurchaseOrderForm().fields)

    def test_revision_defaults_to_r00(self):
        fresh = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 2), po_number='PO-FOOT-2', vendor_name='X',
            po_issued_by='T', cost_center='projects', created_by=self.user)
        self.assertEqual(fresh.mr_revision, 'R00')

    def test_unpriced_pdf_also_has_footer(self):
        text = ' '.join(self._pdf_text('procurement:po_export_pdf_unpriced').split())
        self.assertIn('Leap Networks Arabia', text)
        self.assertIn('Material Requisition R00', text)
        self.assertIn('Page 1 of', text)


class POPdfHeaderTests(TestCase):
    """The PO PDF header shows the company block (left), 'Purchase Order'
    (centre) and the logo (right). The Arabic company name renders when the
    Arabic font is present; when it isn't, the export still succeeds."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('boss', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-HDR-1', vendor_name='ACME',
            po_issued_by='Tester', cost_center='projects', created_by=self.user)

    def _text(self):
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        r = self.client.get(reverse('procurement:po_export_pdf', kwargs={'pk': self.po.pk}))
        self.assertEqual(r.status_code, 200)
        return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(r.content)).pages)

    def test_header_has_company_block_and_centre_title(self):
        text = self._text()
        self.assertIn('Leap Networks Arabia', text)
        self.assertIn('Al-Khobar, Saudi Arabia', text)
        self.assertIn('www.leap-arabia.com', text)
        self.assertIn('Purchase Order', text)

    def test_arabic_font_is_available_and_export_succeeds(self):
        # The Amiri font ships in static/fonts/, so it registers and the Arabic
        # company name renders; the export returns a valid PDF.
        from procurement.views import _arabic_font
        self.assertEqual(_arabic_font(), 'ArabicHeader')
        r = self.client.get(reverse('procurement:po_export_pdf', kwargs={'pk': self.po.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_shape_arabic_transforms_text(self):
        from procurement.views import _shape_arabic
        raw = 'شركة لييب نتوركس أرابيا'
        shaped = _shape_arabic(raw)
        self.assertEqual(len(shaped), len(raw))   # same characters, reordered/reshaped
        self.assertNotEqual(shaped, raw)          # bidi + reshape actually changed it


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
        # Match on the header text rather than fixed coordinates so the
        # assertion survives layout changes to the sheet.
        header_texts = {c.value for row in ws.iter_rows() for c in row if c.value}
        self.assertIn('Rate/unit (AED)', header_texts)
        self.assertIn('Total Value (AED)', header_texts)


class QuotationImportTests(TestCase):
    """Supplier quotation PDFs are AI-extracted into a normalized structure,
    reviewed in the PO editor, then turned into a PO. The Anthropic call is
    mocked — these tests cover normalization + the upload/review/create flow."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('proc', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.fake = {
            'vendor_name': 'Al-Oufy', 'currency': 'USD', 'vat_rate': 15,
            'project_name': 'Nariyah', 'quotation_reference': 'Q-9',
            'line_items': [
                {'description': 'RGS Conduit 1in', 'make_model': 'ITCC',
                 'quantity': 150, 'uom': 'PCS', 'unit_price': 49.5},
                {'description': 'RGS Lock Nut 1in', 'make_model': '',
                 'quantity': 100, 'uom': 'PCS', 'unit_price': 1.0},
            ],
        }

    def test_normalize_maps_currency_and_unit_price(self):
        from procurement.quotation_extract import _normalize
        out = _normalize({
            'currency': 'SR',
            'line_items': [
                {'description': 'A', 'quantity': '5', 'unit_price': '2.50', 'uom': ''},
                {'description': '', 'quantity': 1, 'unit_price': 1},  # dropped (no desc)
            ],
        })
        self.assertEqual(out['currency'], 'SAR')
        self.assertEqual(len(out['line_items']), 1)
        self.assertEqual(out['line_items'][0]['uom'], 'Nos')   # default
        self.assertEqual(out['line_items'][0]['unit_price'], 2.5)

    def _upload(self):
        from unittest import mock
        from django.core.files.uploadedfile import SimpleUploadedFile
        with mock.patch('procurement.quotation_extract.extract_text_from_pdf', return_value='text'), \
             mock.patch('procurement.quotation_extract.extract_quotation',
                        return_value=(self.fake, 'claude-sonnet-4-6')):
            up = SimpleUploadedFile('al.pdf', b'%PDF-1.4', content_type='application/pdf')
            return self.client.post(reverse('procurement:quotation_import'),
                                    {'quotation_file': up})

    def test_upload_extracts_and_redirects_to_review(self):
        from procurement.models import QuotationImport
        r = self._upload()
        qi = QuotationImport.objects.latest('id')
        self.assertEqual(qi.status, 'extracted')
        self.assertEqual(len(qi.line_items), 2)
        self.assertRedirects(
            r, reverse('procurement:quotation_review', kwargs={'pk': qi.pk}))

    def test_review_page_prefills_extraction(self):
        from procurement.models import QuotationImport
        self._upload()
        qi = QuotationImport.objects.latest('id')
        r = self.client.get(reverse('procurement:quotation_review', kwargs={'pk': qi.pk}))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Al-Oufy', body)
        self.assertIn('RGS Conduit 1in', body)
        self.assertIn('"USD" selected', body)

    def test_extraction_failure_marks_failed(self):
        from unittest import mock
        from django.core.files.uploadedfile import SimpleUploadedFile
        from procurement.models import QuotationImport
        with mock.patch('procurement.quotation_extract.extract_text_from_pdf', return_value='text'), \
             mock.patch('procurement.quotation_extract.extract_quotation',
                        side_effect=RuntimeError('no API key')):
            up = SimpleUploadedFile('x.pdf', b'%PDF-1.4', content_type='application/pdf')
            self.client.post(reverse('procurement:quotation_import'), {'quotation_file': up})
        qi = QuotationImport.objects.latest('id')
        self.assertEqual(qi.status, 'failed')
        self.assertIn('no API key', qi.error)

    def test_review_creates_po_with_items(self):
        from procurement.models import QuotationImport, PurchaseOrder
        self._upload()
        qi = QuotationImport.objects.latest('id')
        data = {
            'po_date': '2026-01-01', 'po_number': 'PO-Q-1', 'cost_center': 'projects',
            'status': 'draft', 'vendor_name': 'Al-Oufy', 'po_issued_by': 'Q',
            'vendor_contact_email': '', 'issuer_email': '', 'project_name': 'Nariyah',
            'currency': 'USD', 'discount_rate': '0', 'vat_rate': '15', 'lead_time': '',
            'payment_terms_text': '', 'warranty': '', 'terms_and_conditions': '',
            'delivery_incoterms': '', 'delivery_location': '',
            'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
        }
        def row(i, desc, qty, price):
            return {f'items-{i}-id': '', f'items-{i}-serial_number': str(i + 1),
                    f'items-{i}-description': desc, f'items-{i}-make_model': '',
                    f'items-{i}-quantity': str(qty), f'items-{i}-uom': 'PCS',
                    f'items-{i}-rate_per_unit': str(price), f'items-{i}-order': str(i),
                    f'items-{i}-system': '', f'items-{i}-remarks': '',
                    f'items-{i}-po_value_usd': '', f'items-{i}-advance_payment_sar': '',
                    f'items-{i}-delivery_status': '', f'items-{i}-scm': ''}
        data.update(row(0, 'RGS Conduit 1in', 150, 49.5))
        data.update(row(1, 'RGS Lock Nut 1in', 100, 1.0))
        r = self.client.post(
            reverse('procurement:quotation_review', kwargs={'pk': qi.pk}), data)
        po = PurchaseOrder.objects.get(po_number='PO-Q-1')
        self.assertRedirects(r, reverse('procurement:po_detail', kwargs={'pk': po.pk}))
        self.assertEqual(po.items.count(), 2)
        self.assertEqual(po.currency, 'USD')
        qi.refresh_from_db()
        self.assertEqual(qi.status, 'converted')
        self.assertEqual(qi.purchase_order_id, po.pk)


class QuotationRetryTests(TestCase):
    """A pending/failed quotation can be re-extracted without re-uploading."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('proc2', password='pw', role=self.sa_role)
        self.client.force_login(self.user)

    def _make(self, status='failed'):
        from django.core.files.base import ContentFile
        from procurement.models import QuotationImport
        qi = QuotationImport.objects.create(
            original_filename='q.pdf', status=status, created_by=self.user,
            error='AI extraction failed: timeout')
        qi.file.save('q.pdf', ContentFile(b'%PDF-1.4'), save=True)
        return qi

    def test_retry_reextracts_failed(self):
        from unittest import mock
        from procurement.models import QuotationImport
        qi = self._make('failed')
        fake = {'vendor_name': 'V', 'currency': 'SAR', 'vat_rate': 15,
                'line_items': [{'description': 'A', 'quantity': 1, 'uom': 'Nos', 'unit_price': 2}]}
        with mock.patch('procurement.quotation_extract.extract_text_from_pdf', return_value='t'), \
             mock.patch('procurement.quotation_extract.extract_quotation', return_value=(fake, 'm')):
            r = self.client.post(reverse('procurement:quotation_retry', kwargs={'pk': qi.pk}))
        qi.refresh_from_db()
        self.assertEqual(qi.status, 'extracted')
        self.assertEqual(qi.error, '')
        self.assertRedirects(r, reverse('procurement:quotation_review', kwargs={'pk': qi.pk}))

    def test_retry_records_failure_again(self):
        from unittest import mock
        qi = self._make('pending')
        with mock.patch('procurement.quotation_extract.extract_text_from_pdf', return_value='t'), \
             mock.patch('procurement.quotation_extract.extract_quotation',
                        side_effect=RuntimeError('still down')):
            self.client.post(reverse('procurement:quotation_retry', kwargs={'pk': qi.pk}))
        qi.refresh_from_db()
        self.assertEqual(qi.status, 'failed')          # never stuck at pending
        self.assertIn('still down', qi.error)


class TermsUsageSeparationTests(TestCase):
    """Sales and procurement terms are kept in separate pickers."""

    def setUp(self):
        from costing.models import TermsTemplate
        self.sales = TermsTemplate.objects.create(
            name='Sales only', category='payment_terms', usage='sales', content='x')
        self.proc = TermsTemplate.objects.create(
            name='Proc only', category='payment_terms', usage='procurement', content='x')
        self.both = TermsTemplate.objects.create(
            name='Shared', category='payment_terms', usage='both', content='x')

    def _names(self, terms_by_category):
        out = []
        for rows in terms_by_category.values():
            out += [r['template'].name for r in rows]
        return set(out)

    def test_po_picker_excludes_sales_terms(self):
        from procurement.views import _po_terms_by_category
        names = self._names(_po_terms_by_category([]))
        self.assertIn('Proc only', names)
        self.assertIn('Shared', names)
        self.assertNotIn('Sales only', names)

    def test_po_quick_add_creates_procurement_term(self):
        from costing.models import TermsTemplate
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        u = User.objects.create_user('mgr', password='pw', role=sa)
        self.client.force_login(u)
        r = self.client.post(reverse('costing:terms_template_ajax_create'), {
            'name': 'From PO', 'category': 'exclusions',
            'content': 'no warranty', 'usage': 'procurement'})
        self.assertEqual(r.status_code, 200)
        t = TermsTemplate.objects.get(name='From PO')
        self.assertEqual(t.usage, 'procurement')


class BudgetProcurementFlowTests(TestCase):
    """Procurement works from the finance-approved budget, not the costing
    sheet: budgeted price flows onto the PO and the costing sheet is off-limits."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        from costing.models import CostingSheet, CostingSection, CostingLineItem
        self.sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.proc_role, _ = Role.objects.get_or_create(name=Role.PROCUREMENT_MGR)
        self.region = Region.objects.create(name='Arabia')
        self.won = ProjectStatus.objects.create(name='Won', category='won')
        self.project = Project.objects.create(
            project_name='P', status=self.won, region=self.region)
        self.sheet = CostingSheet.objects.create(
            title='S', project=self.project, margin=Decimal('30'),
            discount_rate=Decimal('0'), workflow_stage='finance_approved')
        self.sec = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='A.1', title='CCTV', order=0)
        self.item = CostingLineItem.objects.create(
            section=self.sec, item_number='1', description='Cam', quantity=Decimal('2'),
            unit='EA', make='Bosch', model_number='X1', vendor_name='Acme',
            base_unit_cost=Decimal('100'), supplier_currency='SAR')
        self.proc = User.objects.create_user(
            'proc', password='pw', role=self.proc_role, region=self.region)

    def test_budget_flows_to_po_at_budgeted_price(self):
        from procurement.models import PurchaseOrder, PurchaseOrderItem
        self.client.force_login(self.proc)
        # Approved-budget list shows the sheet; tracker opens.
        self.assertEqual(self.client.get(reverse('procurement:approved_budgets')).status_code, 200)
        turl = reverse('procurement:bom_procurement_tracker', kwargs={'sheet_pk': self.sheet.pk})
        self.assertEqual(self.client.get(turl).status_code, 200)
        # Create a PO from the one supply line.
        r = self.client.post(turl, {'item_ids': [str(self.item.pk)]})
        self.assertEqual(r.status_code, 302)
        po = PurchaseOrder.objects.filter(project=self.project).latest('id')
        pi = po.items.get(source_bom_item=self.item)
        # rate = budgeted unit price; vendor carried over; PO vendor pre-filled.
        self.assertEqual(pi.rate_per_unit, self.item.budget_unit_price())
        self.assertEqual(pi.vendor_name, 'Acme')
        self.assertEqual(po.vendor_name, 'Acme')
        self.assertEqual(pi.make_model, 'Bosch X1')
        # Budgeted price at default equals the costing line price (margin 30%).
        self.item.refresh_from_db()
        self.assertEqual(self.item.budget_line_price(), self.item.base_total_price)

    def test_procurement_cannot_open_costing_sheet(self):
        self.client.force_login(self.proc)
        r = self.client.get(reverse('costing:detail', kwargs={'pk': self.sheet.pk}))
        self.assertNotEqual(r.status_code, 200)  # locked out (404/403/redirect)


class POTermOverrideTests(TestCase):
    """Terms are picked from the shared TermsTemplate library, but a PO can
    reword one for itself. The override is scoped to that PO — the template
    and every other PO using it must stay exactly as they were."""

    def setUp(self):
        from costing.models import TermsTemplate
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('ovr', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.original = 'ORIGINAL LINE ONE\nORIGINAL LINE TWO'
        self.tmpl = TermsTemplate.objects.create(
            name='Payment', category='payment_terms', usage='procurement',
            content=self.original)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='OVR-1', vendor_name='V',
            po_issued_by='T', cost_center='projects', created_by=self.user)
        self.po.selected_terms.add(self.tmpl)

    def _save(self, text, po=None):
        from django.http import QueryDict
        from procurement.views import _save_po_term_overrides
        qd = QueryDict(mutable=True)
        qd[f'term_content_{self.tmpl.pk}'] = text
        _save_po_term_overrides(po or self.po, qd, self.user)

    def test_no_override_falls_back_to_template(self):
        entry = self.po.resolved_terms()[0]
        self.assertEqual(entry['content'], self.original)
        self.assertFalse(entry['is_overridden'])

    def test_edit_is_scoped_to_this_po(self):
        self._save('CUSTOM WORDING')
        entry = self.po.resolved_terms()[0]
        self.assertEqual(entry['content'], 'CUSTOM WORDING')
        self.assertTrue(entry['is_overridden'])

        # The shared template must not have been written to.
        self.tmpl.refresh_from_db()
        self.assertEqual(self.tmpl.content, self.original)

        # …and a different PO on the same template still sees the original.
        other = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='OVR-2', vendor_name='V2',
            po_issued_by='T', cost_center='projects', created_by=self.user)
        other.selected_terms.add(self.tmpl)
        self.assertEqual(other.resolved_terms()[0]['content'], self.original)

    def test_editing_back_to_template_text_drops_the_override(self):
        from procurement.models import POTermOverride
        self._save('CUSTOM WORDING')
        self.assertEqual(POTermOverride.objects.filter(purchase_order=self.po).count(), 1)
        # Trailing whitespace / CRLF differences must not count as an edit.
        self._save(self.original.replace('\n', '\r\n') + '   ')
        self.assertEqual(POTermOverride.objects.filter(purchase_order=self.po).count(), 0)

    def test_deselecting_a_term_drops_its_override(self):
        from procurement.models import POTermOverride
        self._save('CUSTOM WORDING')
        self.po.selected_terms.clear()
        self._save('CUSTOM WORDING')
        self.assertEqual(POTermOverride.objects.filter(purchase_order=self.po).count(), 0)

    def test_exports_use_the_edited_text(self):
        import io
        from django.utils import timezone
        from procurement.models import PurchaseOrderItem
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=1, description='D',
            quantity=1, uom='Nos', rate_per_unit=10, order=0)
        self._save('CUSTOM WORDING FOR EXPORT')
        for s in self.po.required_stages:      # release so Excel is unlocked
            setattr(self.po, f'{s}_approved_at', timezone.now())
            setattr(self.po, f'{s}_approved_by', self.user)
        self.po.save()

        import openpyxl
        r = self.client.get(reverse('procurement:po_export', kwargs={'pk': self.po.pk}))
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        cells = {str(c.value) for row in ws.iter_rows() for c in row if c.value}
        self.assertIn('CUSTOM WORDING FOR EXPORT', cells)
        self.assertNotIn('ORIGINAL LINE ONE', cells)

        from pypdf import PdfReader
        r = self.client.get(reverse('procurement:po_export_pdf', kwargs={'pk': self.po.pk}))
        text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        self.assertIn('CUSTOM WORDING FOR EXPORT', text)
        self.assertNotIn('ORIGINAL LINE ONE', text)


class POTermsPdfNumberingTests(TestCase):
    """Procurement PO PDF must auto-number the terms (users were typing serial
    numbers by hand because nothing sequenced them) and start the Terms &
    Conditions on their own fresh page."""

    def setUp(self):
        from costing.models import TermsTemplate
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('tcnum', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='TCNUM-1', vendor_name='V',
            po_issued_by='T', cost_center='projects', created_by=self.user)
        from procurement.models import PurchaseOrderItem
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=1, description='Widget',
            quantity=1, uom='Nos', rate_per_unit=10, order=0)
        # Two templates whose content includes the user's own numbering — the
        # PDF must reproduce it verbatim and add none of its own.
        self.t1 = TermsTemplate.objects.create(
            name='Payment', category='terms_and_conditions', usage='procurement',
            content='1. Payment 30 days\n2. Prices in SAR')
        self.t2 = TermsTemplate.objects.create(
            name='Warranty', category='terms_and_conditions', usage='procurement',
            content='(a) Warranty 24 months\nUnnumbered clause here')
        self.po.selected_terms.add(self.t1, self.t2)
        from django.utils import timezone
        for s in self.po.required_stages:
            setattr(self.po, f'{s}_approved_at', timezone.now())
            setattr(self.po, f'{s}_approved_by', self.user)
        self.po.save()

    def _pdf_pages(self):
        from pypdf import PdfReader
        r = self.client.get(reverse('procurement:po_export_pdf', kwargs={'pk': self.po.pk}))
        self.assertEqual(r.status_code, 200)
        reader = PdfReader(io.BytesIO(r.content))
        return [(p.extract_text() or '') for p in reader.pages]

    def test_terms_print_content_verbatim_with_no_auto_numbering(self):
        pages = self._pdf_pages()
        text = '\n'.join(pages)
        # The user's own text/numbering is preserved exactly...
        self.assertIn('1. Payment 30 days', text)
        self.assertIn('2. Prices in SAR', text)
        self.assertIn('(a) Warranty 24 months', text)
        self.assertIn('Unnumbered clause here', text)
        # ...and the app adds no numbering of its own: the unnumbered clause
        # is not given a "3." (or any) prefix.
        self.assertNotIn('3. Unnumbered clause here', text)
        self.assertNotIn('4. Unnumbered clause here', text)

    def test_terms_start_on_their_own_page(self):
        pages = self._pdf_pages()
        self.assertGreaterEqual(len(pages), 2)
        # Items are on page 1; the T&C heading must not share it.
        self.assertNotIn('TERMS AND CONDITIONS', pages[0])
        self.assertTrue(any('TERMS AND CONDITIONS' in p for p in pages[1:]))


class POTermsOrderingTests(TestCase):
    """Terms must print in the order the user selected them, not alphabetically."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('tcord', password='pw', role=self.sa_role)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='TCORD-1', vendor_name='V',
            po_issued_by='T', cost_center='projects', created_by=self.user)

    def test_resolved_terms_follow_selection_order_not_alphabetical(self):
        from costing.models import TermsTemplate
        alpha = TermsTemplate.objects.create(
            name='Alpha', category='terms_and_conditions', usage='procurement', content='a')
        zebra = TermsTemplate.objects.create(
            name='Zebra', category='terms_and_conditions', usage='procurement', content='z')
        self.po.selected_terms.add(alpha, zebra)
        # terms_order says Zebra-then-Alpha, which is neither alphabetical nor
        # pk order — resolved_terms must honour it.
        self.po.terms_order = f'{zebra.pk},{alpha.pk}'
        self.po.save(update_fields=['terms_order'])
        names = [e['template'].name for e in self.po.resolved_terms()]
        self.assertEqual(names, ['Zebra', 'Alpha'])

    def test_selected_term_missing_from_order_is_not_dropped(self):
        from costing.models import TermsTemplate
        a = TermsTemplate.objects.create(
            name='A', category='terms_and_conditions', usage='procurement', content='a')
        b = TermsTemplate.objects.create(
            name='B', category='terms_and_conditions', usage='procurement', content='b')
        self.po.selected_terms.add(a, b)
        self.po.terms_order = str(b.pk)      # only B listed; A missing
        self.po.save(update_fields=['terms_order'])
        names = [e['template'].name for e in self.po.resolved_terms()]
        self.assertEqual(names, ['B', 'A'])  # listed first, the rest appended

    def test_ordered_selected_term_ids_respects_hint_and_ignores_unchecked(self):
        from django.http import QueryDict
        from procurement.views import _ordered_selected_term_ids
        post = QueryDict(mutable=True)
        post.setlist('selected_terms', ['5', '3', '9'])   # checked boxes (DOM order)
        post['selected_terms_ordered'] = '9,5,3'          # user's click order
        self.assertEqual(_ordered_selected_term_ids(post), [9, 5, 3])

        # A stale id in the hint that is no longer checked is dropped.
        post['selected_terms_ordered'] = '9,99,5,3'
        self.assertEqual(_ordered_selected_term_ids(post), [9, 5, 3])

        # A checked box missing from the hint (JS off) still appears, at the end.
        post['selected_terms_ordered'] = '9'
        self.assertEqual(_ordered_selected_term_ids(post), [9, 5, 3])
