import io
import zipfile
from datetime import date

from django.test import TestCase
from django.urls import reverse

from proposals.models import (
    TechnicalProposal, ProposalSection, SectionHeading, SectionHeadingTemplate,
)
from proposals.docx_export import generate_proposal_docx
from accounts.models import User, Role


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

    def test_pasted_table_is_centered_on_the_page(self):
        # The cover page/template already contains unrelated centered
        # paragraphs (image captions etc.), so a plain substring check for
        # '<w:jc w:val="center"/>' would pass even without the fix. Assert
        # on the table's OWN tblPr/jc specifically.
        from lxml import etree
        WNS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        p = self._proposal()
        html = ('<table><tr><th>Item</th><th>Qty</th></tr>'
                '<tr><td>Widget</td><td>3</td></tr></table>')
        self._section(p, 'Bill of Materials', html)
        xml = self._doc_xml(p)
        root = etree.fromstring(xml.encode('utf-8'))
        tables = root.findall(f'.//{{{WNS}}}tbl')
        self.assertEqual(len(tables), 1)
        jc = tables[0].find(f'{{{WNS}}}tblPr/{{{WNS}}}jc')
        self.assertIsNotNone(jc, 'table has no w:jc alignment set')
        self.assertEqual(jc.get(f'{{{WNS}}}val'), 'center')

    def test_pasted_table_matches_the_body_text_indent(self):
        # Body paragraphs are indented 709 twips to clear the sidebar labels
        # floating in the header (see BODY_LEFT_INDENT_TWIPS). A table with
        # no matching w:tblInd starts flush at the page margin instead and
        # collides with that sidebar.
        from lxml import etree
        from proposals.docx_export import BODY_LEFT_INDENT_TWIPS, BODY_CONTENT_WIDTH_TWIPS
        WNS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        p = self._proposal()
        html = '<table><tr><td>A</td></tr></table>'
        self._section(p, 'Some Section', html)
        xml = self._doc_xml(p)
        root = etree.fromstring(xml.encode('utf-8'))
        tbl = root.find(f'.//{{{WNS}}}tbl')
        tblInd = tbl.find(f'{{{WNS}}}tblPr/{{{WNS}}}tblInd')
        self.assertIsNotNone(tblInd, 'table has no w:tblInd set')
        self.assertEqual(tblInd.get(f'{{{WNS}}}w'), str(BODY_LEFT_INDENT_TWIPS))
        tblW = tbl.find(f'{{{WNS}}}tblPr/{{{WNS}}}tblW')
        self.assertEqual(tblW.get(f'{{{WNS}}}type'), 'dxa')
        self.assertEqual(tblW.get(f'{{{WNS}}}w'), str(BODY_CONTENT_WIDTH_TWIPS))

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

    def test_cover_page_prepared_by_company_name_is_arabia_for_ksa(self):
        # The cover page's "Prepared by:" block is a separate run/casing
        # ("LEAP NETWORKS " + "Global Ltd.") from both the header textbox
        # and the body confidentiality text — must be replaced too.
        xml = self._doc_xml(self._proposal(ref='TP-KSA-COVER', region_entity='LNKSA'))
        idx = xml.find('Prepared by:')
        self.assertNotEqual(idx, -1)
        following = xml[idx:idx + 800]
        self.assertIn('Arabia', following)
        self.assertNotIn('LEAP NETWORKS Global', following)

    def test_cover_page_prepared_by_company_name_is_global_for_uk(self):
        xml = self._doc_xml(self._proposal(ref='TP-UK-COVER', region_entity='LNUK'))
        idx = xml.find('Prepared by:')
        self.assertNotEqual(idx, -1)
        following = xml[idx:idx + 800]
        self.assertIn('Global', following)
        self.assertNotIn('Arabia', following)

    def test_body_confidentiality_notice_says_arabia_for_ksa(self):
        # The Confidentiality Notice sits on the cover page (before the
        # first Heading1), so it survives even for a proposal with no
        # sections yet — unlike the template's hardcoded "Company Overview"
        # boilerplate further down, which _replace_body_sections always
        # discards and rebuilds from the proposal's own ProposalSection rows.
        xml = self._doc_xml(self._proposal(ref='TP-KSA-BODY', region_entity='LNKSA'))
        idx = xml.find('proprietary information of')
        self.assertNotEqual(idx, -1)
        following = xml[idx:idx + 200]
        self.assertIn('Leap Networks Arabia', following)
        self.assertNotIn('Global', following)

    def test_body_confidentiality_notice_says_global_for_uk(self):
        xml = self._doc_xml(self._proposal(ref='TP-UK-BODY', region_entity='LNUK'))
        idx = xml.find('proprietary information of')
        self.assertNotEqual(idx, -1)
        following = xml[idx:idx + 200]
        self.assertIn('Leap Networks Global Ltd.', following)
        self.assertNotIn('Arabia', following)


class AddSectionViewTests(TestCase):
    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user(
            username='boss', email='boss@x.com', password='pw', role=self.sa_role)
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

    def test_super_admin_custom_heading_joins_library(self):
        self.assertFalse(SectionHeading.objects.filter(name='Brand New Heading').exists())
        self.client.post(self._url(), {'custom_heading': 'Brand New Heading'})
        self.assertTrue(
            SectionHeading.objects.filter(name='Brand New Heading').exists())

    def test_existing_library_heading_not_duplicated(self):
        SectionHeading.objects.update_or_create(name='Covering Letter')
        before = SectionHeading.objects.filter(name__iexact='covering letter').count()
        self.client.post(self._url(), {'custom_heading': 'covering letter'})
        after = SectionHeading.objects.filter(name__iexact='covering letter').count()
        self.assertEqual(before, after)  # no duplicate created

    def test_non_super_admin_custom_heading_not_added_to_library(self):
        rep_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_REP)
        rep = User.objects.create_user('rep', password='x', role=rep_role)
        proposal = TechnicalProposal.objects.create(
            title='T2', proposal_reference='TP-REP', client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            created_by=rep)
        self.client.force_login(rep)
        url = reverse('proposals:add_section', kwargs={'pk': proposal.pk})
        self.client.post(url, {'custom_heading': 'Rep Only Heading'})
        # Section is added to the proposal, but the library is untouched.
        self.assertTrue(proposal.sections.filter(heading='Rep Only Heading').exists())
        self.assertFalse(
            SectionHeading.objects.filter(name='Rep Only Heading').exists())


class ProposalTeamVisibilityTests(TestCase):
    """Proposal head + reps see every technical proposal in their region."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        self.r1 = Region.objects.create(name='RegA', code='RGA')
        self.r2 = Region.objects.create(name='RegB', code='RGB')
        self.st = ProjectStatus.objects.create(name='Open', category='open')
        self.head_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_HEAD)
        self.rep_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_REP)
        self.head = User.objects.create_user('phead', password='x', role=self.head_role, region=self.r1)
        self.author = User.objects.create_user('author', password='x')
        self.pa = Project.objects.create(project_name='PA', region=self.r1, status=self.st, proposal_reference='RA-1')
        self.pb = Project.objects.create(project_name='PB', region=self.r2, status=self.st, proposal_reference='RB-1')
        TechnicalProposal.objects.create(
            title='InRegion', proposal_reference='TP-A', client_name='C',
            revision_date=date(2026, 1, 1), prepared_by_initials='A',
            project=self.pa, created_by=self.author)
        TechnicalProposal.objects.create(
            title='OtherRegion', proposal_reference='TP-B', client_name='C',
            revision_date=date(2026, 1, 1), prepared_by_initials='A',
            project=self.pb, created_by=self.author)

    def test_proposal_head_sees_region_proposals(self):
        from proposals.views import ProposalPermissionMixin
        class _Req: pass
        req = _Req(); req.user = self.head
        m = ProposalPermissionMixin(); m.request = req
        titles = set(m.get_queryset().values_list('title', flat=True))
        self.assertIn('InRegion', titles)
        self.assertNotIn('OtherRegion', titles)

    def test_proposal_rep_sees_region_proposals(self):
        from proposals.views import ProposalPermissionMixin
        rep = User.objects.create_user('prep', password='x', role=self.rep_role, region=self.r1)
        class _Req: pass
        req = _Req(); req.user = rep
        m = ProposalPermissionMixin(); m.request = req
        titles = set(m.get_queryset().values_list('title', flat=True))
        self.assertEqual(titles, {'InRegion'})


class DuplicateProposalTests(TestCase):
    """Duplicating a proposal makes an independent editable draft (metadata +
    sections + engineering docs) for reuse on another project."""

    def setUp(self):
        from proposals.models import EngineeringDocument
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user(
            username='boss', email='boss@x.com', password='pw', role=self.sa_role)
        self.client.force_login(self.user)

        from projects.models import Region, ProjectStatus, Project
        self.region = Region.objects.create(name='RegA', code='RGA')
        self.status = ProjectStatus.objects.create(name='Open', category='open')
        self.project = Project.objects.create(
            project_name='PA', region=self.region, status=self.status,
            proposal_reference='RA-1')

        self.source = TechnicalProposal.objects.create(
            title='Highway CCTV', proposal_reference='TP-DUP', client_name='ACME',
            project_description='Original desc', region_entity='LNKSA',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            executive_summary='<p>Summary body.</p>', status='final',
            project=self.project, created_by=self.user)
        ProposalSection.objects.create(
            proposal=self.source, heading='Scope', content='<p>Scope.</p>', order=1)
        ProposalSection.objects.create(
            proposal=self.source, heading='Pricing', content='<p>Price.</p>', order=2)
        EngineeringDocument.objects.create(
            proposal=self.source, doc_type='Drawing', doc_number='D-01',
            doc_title='Layout', order=1)

    def _url(self, proposal=None):
        return reverse('proposals:duplicate',
                       kwargs={'pk': (proposal or self.source).pk})

    def test_duplicate_creates_new_proposal_with_copied_content(self):
        resp = self.client.post(self._url())
        self.assertEqual(TechnicalProposal.objects.count(), 2)
        clone = TechnicalProposal.objects.exclude(pk=self.source.pk).get()
        # redirected to the metadata editor of the new copy
        self.assertRedirects(resp, reverse('proposals:edit', kwargs={'pk': clone.pk}))
        # metadata carried over
        self.assertEqual(clone.client_name, 'ACME')
        self.assertEqual(clone.project_description, 'Original desc')
        self.assertEqual(clone.region_entity, 'LNKSA')
        self.assertEqual(clone.executive_summary, '<p>Summary body.</p>')
        # sections + engineering docs copied faithfully
        self.assertEqual(
            list(clone.sections.values_list('heading', 'content', 'order')),
            [('Scope', '<p>Scope.</p>', 1), ('Pricing', '<p>Price.</p>', 2)])
        self.assertEqual(clone.engineering_documents.count(), 1)
        # source untouched
        self.source.refresh_from_db()
        self.assertEqual(self.source.sections.count(), 2)

    def test_duplicate_resets_reference_title_status_project_and_owner(self):
        other = User.objects.create_user('other', password='pw', role=self.sa_role)
        self.client.force_login(other)
        self.client.post(self._url())
        clone = TechnicalProposal.objects.exclude(pk=self.source.pk).get()
        self.assertEqual(clone.title, 'Highway CCTV (Copy)')
        self.assertEqual(clone.proposal_reference, 'TP-DUP-COPY')
        self.assertEqual(clone.status, 'draft')
        self.assertIsNone(clone.project)         # detached for a new project
        self.assertEqual(clone.created_by, other)  # owned by whoever duplicated

    def test_reference_stays_unique_across_repeated_duplication(self):
        self.client.post(self._url())
        self.client.post(self._url())
        refs = list(TechnicalProposal.objects.values_list('proposal_reference', flat=True))
        self.assertEqual(len(refs), len(set(refs)))  # all unique
        self.assertIn('TP-DUP-COPY', refs)
        self.assertIn('TP-DUP-COPY-2', refs)

    def test_get_is_not_allowed(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(TechnicalProposal.objects.count(), 1)

    def test_cannot_duplicate_a_proposal_you_cannot_see(self):
        from projects.models import Region, ProjectStatus, Project
        r2 = Region.objects.create(name='RegB', code='RGB')
        author = User.objects.create_user('author2', password='x')
        hidden = TechnicalProposal.objects.create(
            title='Hidden', proposal_reference='TP-HID', client_name='C',
            revision_date=date(2026, 1, 1), prepared_by_initials='A',
            created_by=author)
        rep_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_REP)
        rep = User.objects.create_user('prep2', password='x', role=rep_role, region=r2)
        self.client.force_login(rep)
        resp = self.client.post(self._url(hidden))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(TechnicalProposal.objects.count(), 2)  # nothing cloned


class PrequalificationTests(TestCase):
    """Prequalification v2: a library of PDFs, selected and merged into one PDF."""

    def setUp(self):
        import io
        from reportlab.pdfgen import canvas
        from django.core.files.base import ContentFile
        from proposals.models import PrequalLibraryItem, PrequalSubmission
        self.sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('pq', password='pw', role=self.sa)
        self.client.force_login(self.user)

        def mkpdf(txt):
            b = io.BytesIO(); c = canvas.Canvas(b); c.drawString(100, 750, txt)
            c.showPage(); c.save(); return b.getvalue()

        self.a = PrequalLibraryItem.objects.create(heading='Doc A', order=0)
        self.a.pdf.save('a.pdf', ContentFile(mkpdf('A')), save=True)
        self.b = PrequalLibraryItem.objects.create(heading='Doc B', order=1)
        self.b.pdf.save('b.pdf', ContentFile(mkpdf('B')), save=True)
        self.nopdf = PrequalLibraryItem.objects.create(heading='No PDF', order=2)
        self.sub = PrequalSubmission.objects.create(title='S1', created_by=self.user)

    def test_merge_combines_selected_pdfs_in_order_skipping_missing(self):
        import io
        from pypdf import PdfReader
        from proposals.prequal_views import merge_prequal_pdfs
        self.sub.selected_items.set([self.b, self.a, self.nopdf])
        data, skipped = merge_prequal_pdfs(self.sub)
        reader = PdfReader(io.BytesIO(data))
        self.assertEqual(len(reader.pages), 2)        # nopdf skipped
        self.assertEqual(skipped, [])                  # nopdf has no pdf -> not even attempted
        # order follows library order (A before B), not selection order
        self.assertEqual([i.heading for i in self.sub.selected_in_order()], ['Doc A', 'Doc B'])

    def test_branded_combined_has_cover_toc_and_dividers(self):
        import io
        from pypdf import PdfReader
        from proposals.prequal_export import build_prequal_combined_pdf
        self.sub.title = 'PQ'; self.sub.client_name = 'Client'; self.sub.reference = 'R1'; self.sub.save()
        self.sub.selected_items.set([self.a, self.b])  # 1 page each
        data, skipped = build_prequal_combined_pdf(self.sub)
        reader = PdfReader(io.BytesIO(data))
        # cover(1) + toc(1) + [divider+1]*2 = 6 pages
        self.assertEqual(len(reader.pages), 6)
        self.assertIn('Table of Contents', reader.pages[1].extract_text() or '')
        self.assertIn('SECTION 1', reader.pages[2].extract_text() or '')

    def test_export_returns_pdf(self):
        from django.urls import reverse
        self.sub.selected_items.set([self.a])
        r = self.client.get(reverse('proposals:prequal_export', kwargs={'pk': self.sub.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_edit_saves_selection(self):
        from django.urls import reverse
        r = self.client.post(reverse('proposals:prequal_edit', kwargs={'pk': self.sub.pk}),
                             {'title': 'S1', 'items': [str(self.a.pk), str(self.b.pk)]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(set(self.sub.selected_items.values_list('pk', flat=True)),
                         {self.a.pk, self.b.pk})

    def test_library_upload_replaces_pdf(self):
        import io
        from django.urls import reverse
        from django.core.files.uploadedfile import SimpleUploadedFile
        from reportlab.pdfgen import canvas
        b = io.BytesIO(); c = canvas.Canvas(b); c.drawString(100, 750, 'NEW'); c.showPage(); c.save()
        up = SimpleUploadedFile('new.pdf', b.getvalue(), content_type='application/pdf')
        r = self.client.post(
            reverse('proposals:prequal_library_save', kwargs={'pk': self.nopdf.pk}),
            {'heading': 'No PDF', 'order': '2', 'is_active': '1', 'pdf': up})
        self.assertEqual(r.status_code, 302)
        self.nopdf.refresh_from_db()
        self.assertTrue(self.nopdf.pdf)

    def test_erp_admin_full_access(self):
        """ERP Admin gets company-wide view/create/edit/delete on submissions and
        can manage the library (the Administration section it owns)."""
        from django.urls import reverse
        from proposals.models import PrequalSubmission
        erp, _ = Role.objects.get_or_create(name=Role.ERP_ADMIN)
        erp_user = User.objects.create_user('erp_pq', password='pw', role=erp)
        self.client.force_login(erp_user)

        # View — and see ALL submissions (incl. one created by another user).
        r = self.client.get(reverse('proposals:prequal_list'))
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.sub, list(r.context['submissions']))

        # Create.
        r = self.client.post(reverse('proposals:prequal_create'), {'title': 'By ERP'})
        self.assertEqual(r.status_code, 302)
        new = PrequalSubmission.objects.get(title='By ERP')
        self.assertEqual(new.created_by, erp_user)

        # Edit another user's submission (visible company-wide).
        r = self.client.post(reverse('proposals:prequal_edit', kwargs={'pk': self.sub.pk}),
                             {'title': 'Edited by ERP', 'items': [str(self.a.pk)]})
        self.assertEqual(r.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.title, 'Edited by ERP')

        # Manage the library.
        self.assertEqual(
            self.client.get(reverse('proposals:prequal_library')).status_code, 200)

        # Delete.
        r = self.client.post(reverse('proposals:prequal_delete', kwargs={'pk': new.pk}))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(PrequalSubmission.objects.filter(pk=new.pk).exists())

    def test_sales_and_proposal_team_can_use_prequal(self):
        """Sales reps and proposal-team members can open and create
        prequalifications (they do not manage the shared library)."""
        from django.urls import reverse
        from proposals.models import PrequalSubmission
        for role_name, uname in ((Role.SALES_REP, 'sales_pq'),
                                  (Role.PROPOSAL_REP, 'prop_pq')):
            role, _ = Role.objects.get_or_create(name=role_name)
            u = User.objects.create_user(uname, password='pw', role=role)
            self.client.force_login(u)
            # Can open the list.
            self.assertEqual(
                self.client.get(reverse('proposals:prequal_list')).status_code, 200,
                f'{role_name} should reach the prequal list')
            # Can create a submission.
            r = self.client.post(reverse('proposals:prequal_create'),
                                  {'title': f'By {role_name}'})
            self.assertEqual(r.status_code, 302)
            self.assertTrue(
                PrequalSubmission.objects.filter(title=f'By {role_name}', created_by=u).exists())
            # But NOT the admin-only library management.
            self.assertEqual(
                self.client.get(reverse('proposals:prequal_library')).status_code, 302,
                f'{role_name} should not manage the library')


class AIProposalAccessTests(TestCase):
    """The AI department can create a technical proposal and link it to an
    existing project (even though AI users usually have no region)."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        self.role, _ = Role.objects.get_or_create(name=Role.AI_ENGINEER)
        self.ai = User.objects.create_user('ai_eng', password='pw', role=self.role, region=None)
        self.region = Region.objects.create(name='KSA', code='LNKSA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='P', proposal_reference='P-1', status=self.status, region=self.region)

    def test_ai_user_sees_all_projects_in_form(self):
        from proposals.forms import ProposalMetadataForm
        self.assertTrue(self.ai.is_ai_team_user)
        f = ProposalMetadataForm(user=self.ai)
        self.assertIn(self.project, list(f.fields['project'].queryset))

    def test_ai_user_creates_proposal_linked_to_project(self):
        from proposals.models import TechnicalProposal
        self.client.force_login(self.ai)
        self.assertEqual(self.client.get(reverse('proposals:create')).status_code, 200)
        r = self.client.post(reverse('proposals:create'), {
            'title': 'AI Proposal', 'project': str(self.project.pk),
            'proposal_reference': 'AI-1', 'document_type': 'Technical Proposal',
            'client_name': 'ACME', 'region_entity': 'LNKSA', 'revision': 'R00',
            'revision_date': '2026-07-06', 'prepared_by_initials': 'AI', 'status': 'draft'})
        self.assertEqual(r.status_code, 302)
        tp = TechnicalProposal.objects.get(created_by=self.ai)
        self.assertEqual(tp.project_id, self.project.pk)


class ProjectAutofillTests(TestCase):
    """New/Edit Proposal form: picking a project fills in Proposal Reference
    and Client Name via JS. Server-side, that's just the view handing the
    template a {project_id: {reference, client}} map — verify that plumbing
    (view -> context -> rendered page) is correct; the JS behaviour itself
    (overwrite vs. leave-alone) isn't exercisable from a Django TestCase."""

    def setUp(self):
        from projects.models import Region, ProjectStatus, Project
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user(
            'autofill_boss', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.region = Region.objects.create(name='RegA', code='RGA')
        self.status = ProjectStatus.objects.create(name='Open', category='open')
        self.project = Project.objects.create(
            project_name='Highway CCTV', region=self.region, status=self.status,
            proposal_reference='RA-42', customer='Acme Corp')

    def test_create_view_context_has_autofill_map_with_reference_and_client(self):
        resp = self.client.get(reverse('proposals:create'))
        self.assertEqual(resp.status_code, 200)
        amap = resp.context['project_autofill_map']
        self.assertEqual(
            amap[self.project.pk], {'reference': 'RA-42', 'client': 'Acme Corp'})

    def test_update_view_context_has_autofill_map(self):
        proposal = TechnicalProposal.objects.create(
            title='T', proposal_reference='TP-AF', client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            created_by=self.user)
        resp = self.client.get(reverse('proposals:edit', kwargs={'pk': proposal.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.project.pk, resp.context['project_autofill_map'])

    def test_autofill_data_is_embedded_in_the_page_for_js_to_read(self):
        resp = self.client.get(reverse('proposals:create'))
        self.assertContains(resp, 'id="project-autofill-data"')
        self.assertContains(resp, 'RA-42')
        self.assertContains(resp, 'Acme Corp')

    def test_project_select_and_target_fields_are_present_for_the_js_to_bind_to(self):
        resp = self.client.get(reverse('proposals:create'))
        content = resp.content.decode()
        self.assertIn('id="id_project"', content)
        self.assertIn('id="id_proposal_reference"', content)
        self.assertIn('id="id_client_name"', content)

    def test_regular_user_only_sees_their_own_region_projects_in_the_map(self):
        from projects.models import Region, Project
        other_region = Region.objects.create(name='RegB', code='RGB')
        other_project = Project.objects.create(
            project_name='Other Region Job', region=other_region, status=self.status,
            proposal_reference='RB-1', customer='Other Client')
        rep_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_REP)
        rep = User.objects.create_user(
            'region_rep', password='pw', role=rep_role, region=self.region)
        self.client.force_login(rep)

        resp = self.client.get(reverse('proposals:create'))
        amap = resp.context['project_autofill_map']
        # Same-region project: visible, matches the dropdown.
        self.assertIn(self.project.pk, amap)
        # Other-region project: not in the map, same as it's not in the
        # dropdown's queryset — no company-wide reference/client leakage.
        self.assertNotIn(other_project.pk, amap)

    def test_ai_team_user_sees_all_regions_in_the_map(self):
        # AI works cross-region and usually has no region set — the map
        # should follow the same "sees everything" rule as the dropdown.
        from projects.models import Region, Project
        other_region = Region.objects.create(name='RegB', code='RGB')
        other_project = Project.objects.create(
            project_name='Other Region Job', region=other_region, status=self.status,
            proposal_reference='RB-1', customer='Other Client')
        ai_role, _ = Role.objects.get_or_create(name=Role.AI_ENGINEER)
        ai_user = User.objects.create_user('ai_autofill', password='pw', role=ai_role, region=None)
        self.client.force_login(ai_user)

        resp = self.client.get(reverse('proposals:create'))
        amap = resp.context['project_autofill_map']
        self.assertIn(self.project.pk, amap)
        self.assertIn(other_project.pk, amap)

    def test_autofill_map_keys_always_match_the_dropdown_exactly(self):
        # The core invariant the fix relies on: the map is built FROM the
        # dropdown's own queryset, so the two can never drift apart —
        # whoever the user is, whatever set of projects they can pick from.
        from projects.models import Region, Project
        other_region = Region.objects.create(name='RegB', code='RGB')
        Project.objects.create(
            project_name='Other Region Job', region=other_region, status=self.status,
            proposal_reference='RB-1', customer='Other Client')
        rep_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_REP)
        rep = User.objects.create_user(
            'region_rep2', password='pw', role=rep_role, region=self.region)
        self.client.force_login(rep)

        resp = self.client.get(reverse('proposals:create'))
        dropdown_pks = set(resp.context['form'].fields['project'].queryset.values_list('pk', flat=True))
        map_pks = set(resp.context['project_autofill_map'].keys())
        self.assertEqual(map_pks, dropdown_pks)
        self.assertGreater(len(dropdown_pks), 0)  # sanity: didn't vacuously pass on empty sets

    def test_update_view_also_scopes_the_map_to_the_users_region(self):
        from projects.models import Region, Project
        other_region = Region.objects.create(name='RegB', code='RGB')
        other_project = Project.objects.create(
            project_name='Other Region Job', region=other_region, status=self.status,
            proposal_reference='RB-1', customer='Other Client')
        rep_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_REP)
        rep = User.objects.create_user(
            'region_rep3', password='pw', role=rep_role, region=self.region)
        proposal = TechnicalProposal.objects.create(
            title='T', proposal_reference='TP-AF2', client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            created_by=rep)
        self.client.force_login(rep)

        resp = self.client.get(reverse('proposals:edit', kwargs={'pk': proposal.pk}))
        amap = resp.context['project_autofill_map']
        self.assertIn(self.project.pk, amap)
        self.assertNotIn(other_project.pk, amap)


DEPARTMENTS = ['ai', 'telecom', 'procurement']


class DeptTemplateSeedMigrationTests(TestCase):
    """0011_seed_dept_section_templates ships the real AI/Telecom/Procurement
    content (that used to only exist in one person's local admin) as part of
    the migration, so it lands automatically on every environment that runs
    `migrate` — no manual re-entry. These tests confirm the migration
    actually landed real content, not just an empty table."""

    def test_seed_migration_created_the_expected_row_count_per_department(self):
        counts = {
            dept: SectionHeadingTemplate.objects.filter(department=dept).count()
            for dept in DEPARTMENTS
        }
        self.assertEqual(counts, {'ai': 13, 'telecom': 4, 'procurement': 17})

    def test_seeded_rows_have_real_non_empty_content(self):
        tmpl = SectionHeadingTemplate.objects.get(
            heading__name='Introduction', department='ai')
        self.assertGreater(len(tmpl.content), 100)

    def test_seeded_images_are_referenced_and_present_in_storage(self):
        from django.core.files.storage import default_storage
        tmpl = SectionHeadingTemplate.objects.get(
            heading__name='System Architecture', department='ai')
        self.assertIn('proposal_templates/ai/', tmpl.content)
        self.assertTrue(default_storage.exists('proposal_templates/ai/image3.png'))

    def test_seed_migration_is_idempotent(self):
        # Re-running the seed function (e.g. re-applying against an
        # environment that already has some/all of this content) must not
        # duplicate rows or clobber existing ones.
        import importlib
        from django.apps import apps as real_apps
        mod = importlib.import_module(
            'proposals.migrations.0011_seed_dept_section_templates')
        before = SectionHeadingTemplate.objects.count()
        mod.seed(real_apps, None)
        self.assertEqual(SectionHeadingTemplate.objects.count(), before)


class SectionHeadingTemplateModelTests(TestCase):
    """The department-template row itself: one (heading, department) pair,
    at most one per combination."""

    def setUp(self):
        self.heading, _ = SectionHeading.objects.update_or_create(name='Company Profile')

    def test_str_includes_heading_and_department_label(self):
        tmpl = SectionHeadingTemplate.objects.create(
            heading=self.heading, department='ai', content='<p>AI body.</p>')
        self.assertIn('Company Profile', str(tmpl))
        self.assertIn('AI', str(tmpl))

    def test_same_heading_and_department_cannot_be_created_twice(self):
        from django.db import IntegrityError, transaction
        SectionHeadingTemplate.objects.create(
            heading=self.heading, department='telecom', content='First.')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SectionHeadingTemplate.objects.create(
                    heading=self.heading, department='telecom', content='Second.')

    def test_same_heading_different_departments_is_allowed(self):
        for dept in DEPARTMENTS:
            SectionHeadingTemplate.objects.create(
                heading=self.heading, department=dept, content=f'{dept} body')
        self.assertEqual(self.heading.dept_templates.count(), 3)


class LoadHeadingTemplateViewTests(TestCase):
    """ajax_load_heading_template: returns a department's composed content
    for a heading, to load into a section's editor."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.author = User.objects.create_user(
            'owner', password='pw', role=self.sa_role)
        self.proposal = TechnicalProposal.objects.create(
            title='T', proposal_reference='TP-TMPL', client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            created_by=self.author)
        self.heading, _ = SectionHeading.objects.update_or_create(
            name='Executive Summary',
            defaults={'default_content': '<p>Generic default.</p>'})
        self.templates = {}
        for dept in DEPARTMENTS:
            self.templates[dept] = SectionHeadingTemplate.objects.create(
                heading=self.heading, department=dept,
                content=f'<p>{dept.upper()} composed content.</p>')
        self.client.force_login(self.author)

    def _url(self, proposal=None):
        return reverse('proposals:load_heading_template',
                        kwargs={'pk': (proposal or self.proposal).pk})

    def test_returns_composed_content_for_each_department(self):
        for dept in DEPARTMENTS:
            resp = self.client.get(
                self._url(), {'heading_id': self.heading.pk, 'department': dept})
            self.assertEqual(resp.status_code, 200, dept)
            self.assertEqual(
                resp.json()['content'], self.templates[dept].content, dept)

    def test_missing_heading_id_returns_400(self):
        resp = self.client.get(self._url(), {'department': 'ai'})
        self.assertEqual(resp.status_code, 400)

    def test_missing_department_returns_400(self):
        resp = self.client.get(self._url(), {'heading_id': self.heading.pk})
        self.assertEqual(resp.status_code, 400)

    def test_heading_without_a_template_for_that_department_returns_404(self):
        bare = SectionHeading.objects.create(name='No Templates Here')
        resp = self.client.get(
            self._url(), {'heading_id': bare.pk, 'department': 'ai'})
        self.assertEqual(resp.status_code, 404)

    def test_unknown_department_value_returns_404_not_500(self):
        resp = self.client.get(
            self._url(), {'heading_id': self.heading.pk, 'department': 'finance'})
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_proposal_returns_404(self):
        resp = self.client.get(
            reverse('proposals:load_heading_template', kwargs={'pk': 999999}),
            {'heading_id': self.heading.pk, 'department': 'ai'})
        self.assertEqual(resp.status_code, 404)

    def test_user_who_cannot_edit_the_proposal_is_denied(self):
        rep_role, _ = Role.objects.get_or_create(name=Role.PROPOSAL_REP)
        outsider = User.objects.create_user('outsider', password='pw', role=rep_role)
        self.client.force_login(outsider)
        resp = self.client.get(
            self._url(), {'heading_id': self.heading.pk, 'department': 'ai'})
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_load_template_for_a_proposal_they_do_not_own(self):
        admin_role, _ = Role.objects.get_or_create(name=Role.ADMIN)
        admin = User.objects.create_user('admin1', password='pw', role=admin_role)
        self.client.force_login(admin)
        resp = self.client.get(
            self._url(), {'heading_id': self.heading.pk, 'department': 'ai'})
        self.assertEqual(resp.status_code, 200)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get(
            self._url(), {'heading_id': self.heading.pk, 'department': 'ai'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)


class AddSectionDepartmentTemplateTests(TestCase):
    """add_proposal_section's optional 'department' field: pre-fills a
    checked heading with that department's composed content instead of the
    heading's generic default_content."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user(
            'boss2', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.proposal = TechnicalProposal.objects.create(
            title='T', proposal_reference='TP-DEPT', client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            created_by=self.user)
        self.heading, _ = SectionHeading.objects.update_or_create(
            name='Company Profile',
            defaults={'default_content': '<p>Generic default.</p>'})
        for dept in DEPARTMENTS:
            SectionHeadingTemplate.objects.create(
                heading=self.heading, department=dept,
                content=f'<p>{dept.upper()} composed content.</p>')

    def _url(self):
        return reverse('proposals:add_section', kwargs={'pk': self.proposal.pk})

    def test_department_template_content_is_used_when_present(self):
        for dept in DEPARTMENTS:
            self.proposal.sections.all().delete()
            self.client.post(self._url(), {
                'heading': 'Company Profile', 'department': dept})
            sec = self.proposal.sections.get()
            self.assertEqual(sec.content, f'<p>{dept.upper()} composed content.</p>')

    def test_falls_back_to_default_content_when_heading_has_no_template_for_that_department(self):
        ai_only = SectionHeading.objects.create(
            name='AI-Only Heading', default_content='<p>Generic fallback.</p>')
        SectionHeadingTemplate.objects.create(
            heading=ai_only, department='ai', content='<p>AI composed.</p>')
        self.client.post(self._url(), {
            'heading': 'AI-Only Heading', 'department': 'procurement'})
        sec = self.proposal.sections.get()
        self.assertEqual(sec.content, '<p>Generic fallback.</p>')

    def test_department_omitted_behaves_exactly_as_before(self):
        self.client.post(self._url(), {'heading': 'Company Profile'})
        sec = self.proposal.sections.get()
        self.assertEqual(sec.content, '<p>Generic default.</p>')

    def test_unknown_department_value_is_ignored_not_a_500(self):
        resp = self.client.post(self._url(), {
            'heading': 'Company Profile', 'department': 'not-a-real-department'})
        self.assertEqual(resp.status_code, 302)
        sec = self.proposal.sections.get()
        self.assertEqual(sec.content, '<p>Generic default.</p>')

    def test_custom_heading_with_department_does_not_error(self):
        # Custom (non-library) headings have no template to look up at all.
        resp = self.client.post(self._url(), {
            'custom_heading': 'Bespoke One-Off', 'department': 'ai'})
        self.assertEqual(resp.status_code, 302)
        sec = self.proposal.sections.get()
        self.assertEqual(sec.content, '')

    def test_post_without_csrf_token_is_rejected(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        resp = csrf_client.post(self._url(), {
            'heading': 'Company Profile', 'department': 'ai'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.proposal.sections.count(), 0)


class EditContentViewDepartmentGroupingTests(TestCase):
    """The content editor splits the heading library into the original
    generic group and one group per department, so the department toggle
    shows the right headings without disturbing the existing generic list."""

    def setUp(self):
        self.sa_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user(
            'boss3', password='pw', role=self.sa_role)
        self.client.force_login(self.user)
        self.proposal = TechnicalProposal.objects.create(
            title='T', proposal_reference='TP-GROUP', client_name='ACME',
            revision_date=date(2026, 1, 1), prepared_by_initials='AJ',
            created_by=self.user)

        self.generic = SectionHeading.objects.create(
            name='Generic Only', is_active=True)
        self.ai_only = SectionHeading.objects.create(
            name='AI Only', is_active=True)
        SectionHeadingTemplate.objects.create(heading=self.ai_only, department='ai', content='x')
        self.multi_dept = SectionHeading.objects.create(
            name='Telecom And Procurement', is_active=True)
        SectionHeadingTemplate.objects.create(heading=self.multi_dept, department='telecom', content='x')
        SectionHeadingTemplate.objects.create(heading=self.multi_dept, department='procurement', content='x')
        self.inactive_with_template = SectionHeading.objects.create(
            name='Inactive AI Heading', is_active=False)
        SectionHeadingTemplate.objects.create(
            heading=self.inactive_with_template, department='ai', content='x')

    def _get(self):
        return self.client.get(reverse('proposals:content', kwargs={'pk': self.proposal.pk}))

    def test_generic_list_excludes_headings_that_have_any_department_template(self):
        names = set(self._get().context['headings'].values_list('name', flat=True))
        self.assertIn('Generic Only', names)
        self.assertNotIn('AI Only', names)
        self.assertNotIn('Telecom And Procurement', names)

    def test_each_department_group_only_shows_its_own_templated_headings(self):
        # Membership checks, not exact-set equality: the seed migration
        # (0011_seed_dept_section_templates) ships real AI/Telecom/
        # Procurement headings into every test database's baseline, same as
        # the generic library seeded by 0006 already does — so these three
        # groups are never empty/exact to begin with.
        dept_headings = self._get().context['dept_headings']
        ai_names = set(dept_headings['ai'].values_list('name', flat=True))
        telecom_names = set(dept_headings['telecom'].values_list('name', flat=True))
        procurement_names = set(dept_headings['procurement'].values_list('name', flat=True))

        self.assertIn('AI Only', ai_names)
        self.assertNotIn('Telecom And Procurement', ai_names)

        self.assertIn('Telecom And Procurement', telecom_names)
        self.assertNotIn('AI Only', telecom_names)

        self.assertIn('Telecom And Procurement', procurement_names)
        self.assertNotIn('AI Only', procurement_names)

    def test_inactive_heading_excluded_from_generic_and_department_groups(self):
        resp = self._get()
        self.assertNotIn(
            'Inactive AI Heading',
            set(resp.context['headings'].values_list('name', flat=True)))
        self.assertNotIn(
            'Inactive AI Heading',
            set(resp.context['dept_headings']['ai'].values_list('name', flat=True)))
