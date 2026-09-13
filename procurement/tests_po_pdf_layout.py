"""Layout guarantees of the purchase-order PDF.

This document goes to suppliers, and its layout is not incidental — each rule
below was added because somebody found the document wrong in a specific way:

  #101  the totals block was splitting across a page break
  #105  the price box and the signature block were landing on different pages
  #107  the terms heading size needed to be settable per PO
  #109  a blank page was appearing before Terms & Conditions

None of those were covered by a test. The existing PO tests check the header,
the footer, the MR revision and that the unpriced copy omits pricing — all
content, none of it pagination. So the layout could regress silently and the
first sign would be a supplier receiving a PO with a blank page in it.

These are characterization tests: they pin what the builder does today, so the
PDF-building code can be moved or reorganised and any change in the output is
visible immediately. They are worth having regardless of that work, because
right now nothing stops the blank page coming back.
"""
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from accounts.models import Role
from costing.models import TermsTemplate
from procurement.models import PurchaseOrder, PurchaseOrderItem

User = get_user_model()


class POPdfLayoutTests(TestCase):

    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('po-layout', password='pw', role=role)
        self.client.force_login(self.user)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-LAYOUT-1', vendor_name='ACME',
            po_issued_by='Tester', cost_center='projects', created_by=self.user)

    # ── fixtures ────────────────────────────────────────────────────────────

    def add_items(self, count):
        """Enough line items to push the totals block towards a page boundary.

        The pagination rules only mean anything on a PO long enough to break
        across pages, which is exactly the case the original bugs appeared in.
        """
        for n in range(1, count + 1):
            PurchaseOrderItem.objects.create(
                purchase_order=self.po, serial_number=n,
                description=f'Cable tray section type {n}, hot-dip galvanised',
                quantity=Decimal('4'), rate_per_unit=Decimal('137.50'))

    def reset_items(self, count):
        """Replace the line items, so one test can sweep several lengths."""
        self.po.items.all().delete()
        self.add_items(count)

    def add_terms(self, count=3):
        templates = []
        for n in range(1, count + 1):
            template = TermsTemplate.objects.create(
                name=f'Condition {n}', category='terms_and_conditions',
                usage='procurement',
                content=f'Clause {n}. ' + ('Supplier obligations continue. ' * 12))
            templates.append(template)
        self.po.selected_terms.set(templates)
        self.po.terms_order = ','.join(str(t.pk) for t in templates)
        self.po.save(update_fields=['terms_order'])
        return templates

    # ── reading the rendered document ───────────────────────────────────────

    def pages(self, url_name='procurement:po_export_pdf'):
        """Per-page text, so a test can ask *which* page something landed on."""
        response = self.client.get(reverse(url_name, kwargs={'pk': self.po.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        reader = PdfReader(io.BytesIO(response.content))
        return [' '.join((page.extract_text() or '').split()) for page in reader.pages]

    def page_containing(self, pages, needle):
        for index, text in enumerate(pages):
            if needle in text:
                return index
        self.fail(f'{needle!r} appears on no page of the PDF')

    def is_blank(self, page_text):
        """Whether a page carries nothing but the running footer.

        The footer — company, MR revision, PO number, page label — is drawn on
        every page including an empty one, so asking whether a page has any
        text at all can never detect a blank page. That is not a hypothetical:
        it is why the first version of these tests passed while the blank-page
        bug was reintroduced underneath them.
        """
        remainder = page_text
        for furniture in ('DRAFT', 'Leap Networks Arabia', self.po.po_number,
                          'Page', 'of'):
            remainder = remainder.replace(furniture, ' ')
        remainder = remainder.replace('Material Requisition', ' ')
        # Whatever is left is a revision token and stray digits from "Page 2 of 3".
        remainder = ''.join(c for c in remainder if c.isalpha())
        return len(remainder.replace('R', '')) < 4

    # The page geometry today puts about 17 line items on the first page, so a
    # PO of this length is where the totals block meets a page boundary — the
    # state every one of these rules exists for.
    #
    # Swept rather than pinned to one number: the exact boundary moves with any
    # font or margin change, and a test probing a single length silently stops
    # testing anything when it does. The upper end is not arbitrary — a 12mm
    # spacer before the Terms page break only spills into a blank page from 21
    # items, so a sweep that stopped at 20 (as the first version did) let that
    # regression through while looking thorough.
    BOUNDARY_LENGTHS = range(14, 23)

    def font_sizes_for(self, needle, url_name='procurement:po_export_pdf'):
        """The font sizes actually used to draw a piece of text.

        Extracted through a visitor rather than inferred, because the point of
        the per-PO heading size is the size on the page, not the value stored
        on the model.
        """
        response = self.client.get(reverse(url_name, kwargs={'pk': self.po.pk}))
        reader = PdfReader(io.BytesIO(response.content))
        found = []

        def visit(text, cm, tm, font_dict, font_size):
            if needle.lower() in (text or '').lower():
                found.append(round(font_size))

        for page in reader.pages:
            page.extract_text(visitor_text=visit)
        return found

    # ── #109: the blank page ────────────────────────────────────────────────

    def test_no_page_is_blank_at_any_length(self):
        """The bug this replaces: a spacer emitted between the totals block and
        the page break before Terms pushed an empty page into the middle of the
        document. The builder's own comment says a spacer there "is exactly
        what turns the following page blank" — a rule nothing enforced.

        Swept across the page boundary because the spacer only spills when the
        totals block is already near the foot of a page.
        """
        self.add_terms()
        for count in self.BOUNDARY_LENGTHS:
            with self.subTest(items=count):
                self.reset_items(count)
                for index, text in enumerate(self.pages()):
                    self.assertFalse(
                        self.is_blank(text),
                        f'With {count} items, page {index + 1} of the PO PDF '
                        f'carries nothing but the footer')

    def test_no_blank_last_page_without_terms_at_any_length(self):
        """The same spacer also spilled a blank last page when nothing followed
        the totals — the other half of the fix, and a separate code path."""
        for count in self.BOUNDARY_LENGTHS:
            with self.subTest(items=count):
                self.reset_items(count)
                pages = self.pages()
                self.assertFalse(
                    self.is_blank(pages[-1]),
                    f'With {count} items, the PO PDF ends on a blank page')

    def test_terms_follow_the_totals_with_no_page_in_between(self):
        """The sharpest form of the blank-page rule, and the one that does not
        depend on the page geometry of the day.

        Asking whether a page "looks blank" means guessing how much furniture
        counts as empty, and only catches the bug when the spacer happens to
        overflow. Adjacency is exact: whatever the item count, the terms start
        on the page straight after the price box. A spacer that spills pushes
        an extra page in between and this fails immediately.
        """
        self.add_terms()
        for count in self.BOUNDARY_LENGTHS:
            with self.subTest(items=count):
                self.reset_items(count)
                pages = self.pages()
                totals_page = self.page_containing(pages, 'Amount in words')
                terms_page = self.page_containing(pages, 'TERMS AND CONDITIONS')
                self.assertEqual(
                    terms_page, totals_page + 1,
                    f'With {count} items there are {terms_page - totals_page - 1} '
                    f'page(s) between the price box and the Terms')

    def test_terms_begin_on_a_page_of_their_own(self):
        """Terms are meant to start fresh. If the page break disappeared they
        would run on from the totals, which is a different document."""
        self.add_items(4)
        self.add_terms()
        pages = self.pages()
        terms_page = self.page_containing(pages, 'TERMS AND CONDITIONS')
        self.assertNotIn('Cable tray section type 1', pages[terms_page])

    # ── #101 / #105: the price box and the signature ────────────────────────

    def test_the_totals_block_is_not_split_across_pages(self):
        """Every total belongs to one block. Half a price box at the foot of a
        page and the rest overleaf is what #101 fixed."""
        self.add_items(14)
        pages = self.pages()
        first = self.page_containing(pages, 'Amount in words')
        self.assertIn('Total', pages[first])

    def test_the_price_box_and_the_signature_stay_on_one_page(self):
        """A signature on a page that does not show what is being signed for is
        the failure #105 fixed. They are emitted as one keep-together unit.

        The PO has to be signed for this to mean anything: an unsigned draft
        renders no signature block at all, so the same assertions on a draft
        would pass while testing nothing.

        Swept across the page boundary, because with the items comfortably
        clear of the page foot the two blocks share a page whether they are
        held together or not.
        """
        from django.utils import timezone
        self.po.scm_approved_at = timezone.now()
        self.po.save(update_fields=['scm_approved_at'])
        for count in self.BOUNDARY_LENGTHS:
            with self.subTest(items=count):
                self.reset_items(count)
                pages = self.pages()
                totals_page = self.page_containing(pages, 'Amount in words')
                # The signature block prints a name line per approval column.
                signature_page = self.page_containing(pages, 'Name:')
                self.assertEqual(
                    totals_page, signature_page,
                    f'With {count} items the price box and the '
                    f'approval/signature block landed on different pages')

    # ── #107: the per-PO heading size ───────────────────────────────────────

    def test_the_terms_heading_size_reaches_the_page(self):
        templates = self.add_terms(1)
        self.po.terms_heading_font_pt = 14
        self.po.save(update_fields=['terms_heading_font_pt'])
        sizes = self.font_sizes_for(templates[0].name)
        self.assertTrue(sizes, 'the term heading was not drawn at all')
        self.assertIn(14, sizes)

    def test_a_different_heading_size_actually_changes_the_drawn_size(self):
        """Guards the setting end to end: if the field stopped being read, the
        control would be decorative and nobody would know.

        Compared on the drawn font size rather than the extracted text —
        changing a size does not change a single character, so comparing text
        would pass no matter what the setting did.
        """
        templates = self.add_terms(1)
        self.po.terms_heading_font_pt = 8
        self.po.save(update_fields=['terms_heading_font_pt'])
        small = self.font_sizes_for(templates[0].name)

        self.po.terms_heading_font_pt = 16
        self.po.save(update_fields=['terms_heading_font_pt'])
        large = self.font_sizes_for(templates[0].name)

        self.assertIn(8, small)
        self.assertIn(16, large)
        self.assertNotEqual(small, large)

    # ── the shape of the document as a whole ────────────────────────────────

    def test_a_known_purchase_order_renders_a_stable_number_of_pages(self):
        """A golden figure for a fixed input. Not a rule about what the layout
        should be — a tripwire, so that reorganising the builder cannot change
        the pagination without somebody deciding to.
        """
        self.add_items(12)
        self.add_terms(3)
        self.assertEqual(len(self.pages()), 2)

    def test_the_unpriced_copy_has_the_same_page_structure(self):
        """It omits the totals, so it must not gain or lose pages elsewhere."""
        self.add_items(12)
        self.add_terms(3)
        self.assertEqual(len(self.pages('procurement:po_export_pdf_unpriced')), 2)
        for index, text in enumerate(self.pages('procurement:po_export_pdf_unpriced')):
            with self.subTest(page=index + 1):
                self.assertTrue(text.strip())
