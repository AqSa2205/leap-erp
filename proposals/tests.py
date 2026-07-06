import io
import zipfile
from datetime import date

from django.test import TestCase
from django.urls import reverse

from proposals.models import (
    TechnicalProposal, ProposalSection, SectionHeading,
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
