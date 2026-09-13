"""The two duplications the PO subsystem review found, and their guards.

Both were real: the page frame was written out twice per document so the error
fallback could silently drift from the normal build, and the PDF and Excel each
carried their own column headings, which had already drifted on three of them.

Neither fix changed a single rendered byte. These tests are what stops the
duplication coming back.
"""
import io
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from accounts.models import Role
from procurement.models import PurchaseOrder, PurchaseOrderItem
from procurement.po_columns import (PO_ITEM_COLUMNS, divergent_labels,
                                    excel_headers, pdf_columns)

User = get_user_model()


class POColumnSourceTests(TestCase):
    """One table describes the item columns; both documents read it."""

    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('cols', password='pw', role=role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-COLS-1', vendor_name='ACME',
            po_issued_by='T', cost_center='projects', created_by=self.user)
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, serial_number=1, description='Cable tray',
            make_model='Type A', uom='Nos', system='CCTV',
            quantity=Decimal('4'), rate_per_unit=Decimal('137.50'))

    def pdf_text(self, url_name='procurement:po_export_pdf'):
        response = self.client.get(reverse(url_name, kwargs={'pk': self.po.pk}))
        self.assertEqual(response.status_code, 200)
        reader = PdfReader(io.BytesIO(response.content))
        return ' '.join(' '.join((p.extract_text() or '').split())
                        for p in reader.pages)

    def release(self):
        """Sign every required stage.

        The Excel export is locked until a PO is released, so a draft answers
        with a redirect. Worth stating: an earlier version of this test asked
        for the Excel on a draft, got a 302, and would have compared nothing.
        """
        from django.utils import timezone
        now = timezone.now()
        for key, _label, _signer in PurchaseOrder.APPROVAL_STAGES:
            setattr(self.po, '%s_approved_at' % key, now)
        self.po.save()
        self.assertTrue(self.po.is_released)

    def excel_rows(self):
        import openpyxl
        self.release()
        response = self.client.get(
            reverse('procurement:po_export', kwargs={'pk': self.po.pk}))
        self.assertEqual(response.status_code, 200)
        book = openpyxl.load_workbook(io.BytesIO(response.content))
        return [['' if c is None else str(c) for c in row]
                for row in book.active.iter_rows(values_only=True)]

    # ── the table reaches both documents ────────────────────────────────────

    def test_the_priced_pdf_shows_every_column_the_table_gives_it(self):
        text = self.pdf_text()
        for label, _width, _centered in pdf_columns(unpriced=False):
            with self.subTest(column=label):
                self.assertIn(label.format(currency=self.po.currency), text)

    def test_the_unpriced_pdf_drops_exactly_the_priced_columns(self):
        text = self.pdf_text('procurement:po_export_pdf_unpriced')
        for column in PO_ITEM_COLUMNS:
            if column.pdf is None:
                continue
            label = column.pdf.format(currency=self.po.currency)
            with self.subTest(column=column.key):
                if column.priced_only:
                    self.assertNotIn(label, text)
                else:
                    self.assertIn(label, text)

    def test_the_excel_header_row_is_the_table(self):
        expected = excel_headers(self.po.currency)
        rows = self.excel_rows()
        self.assertTrue(
            any(row[:len(expected)] == expected for row in rows),
            'the Excel header row does not match po_columns.excel_headers()')

    def test_the_pdf_widths_fill_the_same_span_either_way(self):
        """The unpriced variant shares the dropped pricing width out between
        Description and Remarks. If it did not, the table would stop short of
        the page edge on one of the two copies."""
        priced = sum(w for _l, w, _c in pdf_columns(unpriced=False))
        unpriced = sum(w for _l, w, _c in pdf_columns(unpriced=True))
        self.assertEqual(priced, unpriced)

    # ── the drift is recorded, not hidden ───────────────────────────────────

    def test_the_known_label_disagreements_are_exactly_these(self):
        """The PDF and Excel name five columns differently. That is preserved
        deliberately — changing what a supplier reads on a purchase order is a
        business decision, not a tidying one — but it is now written down in
        one table instead of being invisible across two files.

        This test fails if a sixth appears, so a new divergence has to be
        chosen rather than drifted into.
        """
        self.assertEqual(
            sorted(key for key, _pdf, _excel in divergent_labels()),
            ['description', 'quantity', 'rate_per_unit', 'serial_number',
             'total_value'])

    def test_columns_the_table_says_agree_really_do(self):
        for column in PO_ITEM_COLUMNS:
            if column.labels_differ or column.pdf is None or column.excel is None:
                continue
            with self.subTest(column=column.key):
                self.assertEqual(column.pdf, column.excel)


class POPdfFallbackBuildTests(TestCase):
    """The build that runs when decorating the canvas fails.

    It had its own copy of the page frame, so a margin changed in the normal
    build did not reach it — and because it only runs on an error path, nothing
    would have shown that until a supplier received a differently laid-out
    document. No test had ever exercised this path at all.
    """

    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('fallback', password='pw', role=role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-FALLBACK-1', vendor_name='ACME',
            po_issued_by='T', cost_center='projects', created_by=self.user)
        for n in range(1, 19):
            PurchaseOrderItem.objects.create(
                purchase_order=self.po, serial_number=n,
                description='Cable tray section type %d' % n, uom='Nos',
                quantity=Decimal('4'), rate_per_unit=Decimal('137.50'))

    def render(self, break_canvas=False):
        if not break_canvas:
            response = self.client.get(
                reverse('procurement:po_export_pdf', kwargs={'pk': self.po.pk}))
        else:
            class Exploding:
                def __init__(self, *args, **kwargs):
                    raise RuntimeError('canvas decoration failed')

            with mock.patch('procurement.po_pdf._make_numbered_canvas',
                            return_value=Exploding):
                response = self.client.get(
                    reverse('procurement:po_export_pdf', kwargs={'pk': self.po.pk}))
        self.assertEqual(response.status_code, 200)
        return PdfReader(io.BytesIO(response.content))

    def test_the_document_still_renders_when_the_canvas_fails(self):
        self.assertGreater(len(self.render(break_canvas=True).pages), 0)

    def test_the_fallback_uses_the_same_page_geometry(self):
        """The point of the shared frame. If the fallback kept its own margins,
        a change to the normal build would leave the two rendering to different
        page boxes and only the error path would be wrong.
        """
        normal = self.render().pages[0].mediabox
        fallback = self.render(break_canvas=True).pages[0].mediabox
        self.assertEqual(
            (round(float(normal.width)), round(float(normal.height))),
            (round(float(fallback.width)), round(float(fallback.height))))

    def test_the_fallback_carries_the_same_content(self):
        """It drops the page-number decoration, not the document."""
        def text(reader):
            return ' '.join(' '.join((p.extract_text() or '').split())
                            for p in reader.pages)

        self.assertIn('Cable tray section type 1', text(self.render(break_canvas=True)))
        self.assertIn('Amount in words', text(self.render(break_canvas=True)))


class POPageGeometryTests(TestCase):
    """Every table in the purchase order fits the page it is drawn on.

    The audit found three different assumptions about the usable width living
    side by side: the page header summed to 180mm, the info block to 169mm, and
    the item table to 185mm — five millimetres wider than the frame. Nothing
    caught it because nothing derived from anything.
    """

    def test_the_usable_width_is_derived_not_asserted(self):
        from procurement.page_geometry import (CONTENT_WIDTH_MM, MARGIN_MM,
                                               PAGE_WIDTH_MM)
        self.assertEqual(CONTENT_WIDTH_MM, PAGE_WIDTH_MM - 2 * MARGIN_MM)

    def test_the_item_tables_fill_the_frame_exactly(self):
        """Exactly, not merely within it: a table narrower than the frame
        leaves a ragged right edge on a document that goes to a supplier."""
        from procurement.page_geometry import CONTENT_WIDTH_MM
        for unpriced in (False, True):
            with self.subTest(unpriced=unpriced):
                total = sum(w for _l, w, _c in pdf_columns(unpriced=unpriced))
                self.assertAlmostEqual(total, CONTENT_WIDTH_MM, places=3)

    def test_the_column_proportions_are_unchanged(self):
        """The fix rescaled the table; it did not redesign it. Each column
        keeps the share of the width it had before."""
        for unpriced, weights in (
                (False, [12, 25, 55, 15, 14, 22, 22, 20]),
                (True, [12, 30, 80, 16, 16, 31])):
            with self.subTest(unpriced=unpriced):
                widths = [w for _l, w, _c in pdf_columns(unpriced=unpriced)]
                scale = sum(weights)
                for width, weight in zip(widths, weights):
                    self.assertAlmostEqual(width / sum(widths), weight / scale,
                                           places=6)

    def test_no_table_in_the_builder_is_wider_than_the_frame(self):
        """Catches the header tables too, and anything added later.

        Reads the literal colWidths lists out of the builder — the derived item
        table is checked above, and this is what stops a new hand-written table
        being drawn wider than the page, which is exactly how the item table
        got to 185mm.
        """
        import os
        import re

        from django.conf import settings

        from procurement.page_geometry import CONTENT_WIDTH_MM

        path = os.path.join(str(settings.BASE_DIR), 'procurement', 'po_pdf.py')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()

        offenders = []
        for literal in re.findall(r'colWidths=\[([^\]]*)\]', source):
            values = re.findall(r'([0-9]+(?:\.[0-9]+)?)\s*\*\s*mm', literal)
            if not values:
                continue                      # derived at runtime, checked above
            total = sum(float(v) for v in values)
            if total > CONTENT_WIDTH_MM + 0.01:
                offenders.append('%s = %.2fmm' % (literal.strip(), total))
        self.assertEqual(
            offenders, [],
            'these tables are wider than the %.0fmm frame: %s'
            % (CONTENT_WIDTH_MM, offenders))
