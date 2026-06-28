import io
import zipfile
from datetime import date

from django.test import TestCase
from django.urls import reverse

from proposals.models import (
    TechnicalProposal, ProposalSection, SectionHeading,
)
from proposals.docx_export import generate_proposal_docx
from accounts.models import User


class ProposalDocxExportTests(TestCase):
    """The exported DOCX is built from the proposal's ProposalSection rows:
    one Heading1 + content block per section, in order. Rich text pasted from
    Word (tags with attributes like <p class="Para">) must render as formatted
    text, not dumped as literal HTML."""

    def _proposal(self, ref='TP-EXPORT-1', **kw):
        return TechnicalProposal.objects.create(
            title='T', proposal_reference=ref, client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ', **kw)

    def _section(self, proposal, heading, content='', order=0):
        return ProposalSection.objects.create(
            proposal=proposal, heading=heading, content=content, order=order)

    def _part_xml(self, proposal, part='word/document.xml'):
        resp = generate_proposal_docx(proposal)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            return z.read(part).decode('utf-8')

    def _doc_xml(self, proposal):
        return self._part_xml(proposal)

    def test_word_pasted_html_is_rendered_not_dumped_raw(self):
        p = self._proposal()
        html = (
            '<p class="Para"><span lang="EN-AU" style="font-family: '
            "'Trebuchet MS',sans-serif; color: #0d0d0d; mso-themetint: 242;\">"
            'The primary reasons for these road blockers include:</span></p>'
            '<p class="Para"><strong>Protection of Critical National Infrastructure</strong></p>')
        self._section(p, 'Covering Letter', html)
        xml = self._doc_xml(p)
        self.assertIn('The primary reasons for these road blockers include', xml)
        self.assertIn('Protection of Critical National Infrastructure', xml)
        # ...and the raw HTML markup is NOT present as literal text.
        self.assertNotIn('class="Para"', xml)
        self.assertNotIn('mso-themetint', xml)
        self.assertNotIn('&lt;p', xml)

    def test_plain_text_still_renders(self):
        p = self._proposal()
        self._section(p, 'Covering Letter', 'Just plain text here.')
        self.assertIn('Just plain text here.', self._doc_xml(p))

    def test_section_heading_appears_as_heading1(self):
        p = self._proposal()
        self._section(p, 'Bespoke Custom Heading', 'Body.', order=0)
        xml = self._doc_xml(p)
        self.assertIn('Bespoke Custom Heading', xml)

    def test_sections_export_in_order(self):
        p = self._proposal()
        self._section(p, 'Second Section', 'beta', order=2)
        self._section(p, 'First Section', 'alpha', order=1)
        xml = self._doc_xml(p)
        self.assertLess(xml.index('First Section'), xml.index('Second Section'))

    def test_proposal_with_no_sections_exports_cover_only(self):
        # Should not raise and should produce a valid (openable) docx.
        resp = generate_proposal_docx(self._proposal(ref='TP-EMPTY'))
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            self.assertIn('word/document.xml', z.namelist())

    def test_header_company_name_is_arabia_for_ksa(self):
        h = self._part_xml(self._proposal(ref='TP-KSA', region_entity='LNKSA'),
                           'word/header1.xml')
        self.assertIn('Arabia', h)        # LEAP Networks Arabia
        self.assertNotIn('Global', h)     # not the UK entity

    def test_header_company_name_is_global_for_uk(self):
        h = self._part_xml(self._proposal(ref='TP-UK', region_entity='LNUK'),
                           'word/header1.xml')
        self.assertIn('Global', h)        # LEAP Networks Global Ltd. (unchanged)


class AddSectionViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='boss', email='boss@x.com', password='pw')
        self.client.force_login(self.user)
        self.proposal = TechnicalProposal.objects.create(
            title='T', proposal_reference='TP-ADD', client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            created_by=self.user)

    def _url(self):
        return reverse('proposals:add_section', kwargs={'pk': self.proposal.pk})

    def test_add_library_heading_prefills_default_content(self):
        SectionHeading.objects.update_or_create(
            name='Executive Summary',
            defaults={'default_content': '<p>Default body.</p>'})
        self.client.post(self._url(), {'heading': 'Executive Summary'})
        sec = self.proposal.sections.get()
        self.assertEqual(sec.heading, 'Executive Summary')
        self.assertEqual(sec.content, '<p>Default body.</p>')

    def test_add_custom_heading_is_accepted(self):
        self.client.post(self._url(), {'heading': 'My Bespoke Heading'})
        sec = self.proposal.sections.get()
        self.assertEqual(sec.heading, 'My Bespoke Heading')
        self.assertEqual(sec.content, '')

    def test_library_match_is_case_insensitive(self):
        SectionHeading.objects.update_or_create(
            name='Risk Management', defaults={'default_content': 'X'})
        self.client.post(self._url(), {'heading': 'risk management'})
        sec = self.proposal.sections.get()
        self.assertEqual(sec.heading, 'Risk Management')  # canonical casing

    def test_blank_heading_is_rejected(self):
        self.client.post(self._url(), {'heading': '   '})
        self.assertEqual(self.proposal.sections.count(), 0)

    def test_added_sections_get_increasing_order(self):
        self.client.post(self._url(), {'heading': 'A'})
        self.client.post(self._url(), {'heading': 'B'})
        orders = list(self.proposal.sections.values_list('heading', 'order'))
        self.assertEqual(orders, [('A', 1), ('B', 2)])

    def test_multiple_headings_added_in_one_post(self):
        self.client.post(self._url(), {'heading': ['One', 'Two', 'Three']})
        orders = list(self.proposal.sections.values_list('heading', 'order'))
        self.assertEqual(orders, [('One', 1), ('Two', 2), ('Three', 3)])

    def test_checkboxes_plus_custom_in_one_post(self):
        self.client.post(self._url(),
                         {'heading': ['One', 'Two'], 'custom_heading': 'Bespoke'})
        headings = list(self.proposal.sections.values_list('heading', flat=True))
        self.assertEqual(headings, ['One', 'Two', 'Bespoke'])

    def test_nothing_selected_is_rejected(self):
        self.client.post(self._url(), {'custom_heading': '  '})
        self.assertEqual(self.proposal.sections.count(), 0)
