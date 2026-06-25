import io
import zipfile
from datetime import date

from django.test import TestCase

from proposals.models import TechnicalProposal
from proposals.docx_export import generate_proposal_docx


class ProposalDocxExportTests(TestCase):
    """Rich-text pasted from Word (tags carry attributes like <p class="Para">,
    <span style="...">) must be rendered as formatted text in the exported DOCX,
    not dumped as literal HTML."""

    def _proposal(self, ref='TP-EXPORT-1', **content):
        return TechnicalProposal.objects.create(
            title='T', proposal_reference=ref, client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ', **content)

    def _part_xml(self, proposal, part='word/document.xml'):
        resp = generate_proposal_docx(proposal)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            return z.read(part).decode('utf-8')

    def _doc_xml(self, proposal):
        return self._part_xml(proposal)

    def test_word_pasted_html_is_rendered_not_dumped_raw(self):
        html = (
            '<p class="Para"><span lang="EN-AU" style="font-family: '
            "'Trebuchet MS',sans-serif; color: #0d0d0d; mso-themetint: 242;\">"
            'The primary reasons for these road blockers include:</span></p>'
            '<p class="Para"><strong>Protection of Critical National Infrastructure</strong></p>')
        xml = self._doc_xml(self._proposal(covering_letter=html))
        # The actual sentence is present as text...
        self.assertIn('The primary reasons for these road blockers include', xml)
        self.assertIn('Protection of Critical National Infrastructure', xml)
        # ...and the raw HTML markup is NOT present as literal text.
        self.assertNotIn('class="Para"', xml)
        self.assertNotIn('mso-themetint', xml)
        self.assertNotIn('&lt;p', xml)

    def test_plain_text_still_renders(self):
        xml = self._doc_xml(self._proposal(covering_letter='Just plain text here.'))
        self.assertIn('Just plain text here.', xml)

    def test_header_company_name_is_arabia_for_ksa(self):
        h = self._part_xml(self._proposal(ref='TP-KSA', region_entity='LNKSA'),
                           'word/header1.xml')
        self.assertIn('Arabia', h)        # LEAP Networks Arabia
        self.assertNotIn('Global', h)     # not the UK entity

    def test_header_company_name_is_global_for_uk(self):
        h = self._part_xml(self._proposal(ref='TP-UK', region_entity='LNUK'),
                           'word/header1.xml')
        self.assertIn('Global', h)        # LEAP Networks Global Ltd. (unchanged)
