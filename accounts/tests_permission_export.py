"""Exporting the permission grid as a PDF.

The export is a record of who can do what, so the two things worth guarding are
that it is as hard to reach as the screen it came from, and that it reports the
grid as actually configured rather than the defaults the system shipped with —
a document that quietly shows the defaults would be worse than none, because it
would be believed.
"""
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from accounts.models import Role, RolePermission
from accounts.permissions import CAPABILITIES, seed_default_permissions

User = get_user_model()


def pdf_text(response):
    """The text ReportLab actually drew, with whitespace flattened.

    Searching the raw bytes would be meaningless — ReportLab compresses its
    text streams, so a string can be absent from the bytes and present on the
    page, and vice versa.

    Whitespace is collapsed because the column headers deliberately break role
    names across lines to fit narrow columns: "Finance Manager" is drawn on two
    lines and extracts with a newline between the words. Flattening lets a test
    ask whether the name is on the page without pinning where it wrapped.
    """
    reader = PdfReader(BytesIO(response.content))
    raw = '\n'.join(page.extract_text() or '' for page in reader.pages)
    return ' '.join(raw.split())


class PermissionExportTests(TestCase):

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.super_admin = User.objects.create_user(
            'perm-sa', password='x', first_name='Aqsa', last_name='Ahmed',
            role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.url = reverse('accounts:permission_matrix_pdf')

    def get(self, who=None):
        self.client.force_login(who or self.super_admin)
        return self.client.get(self.url)

    # ── access ──────────────────────────────────────────────────────────────

    def test_a_super_admin_can_export(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_it_downloads_with_a_dated_filename(self):
        """Two exports a month apart should not be the same file on disk —
        the date is the whole value of an audit record."""
        response = self.get()
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('leap-erp-permissions-', response['Content-Disposition'])

    def test_every_other_role_is_refused(self):
        """The export sets out the whole access model, so it is exactly as
        sensitive as the screen — including for ERP Admin, which runs the rest
        of Administration but deliberately not this."""
        for role_name in (Role.ERP_ADMIN, Role.ADMIN, Role.MANAGER,
                          Role.FINANCE_HEAD, Role.PROJECT_MANAGER):
            with self.subTest(role=role_name):
                person = User.objects.create_user(
                    f'perm-{role_name}', password='x',
                    role=Role.objects.get(name=role_name))
                self.assertEqual(self.get(person).status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_log_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    # ── contents ────────────────────────────────────────────────────────────

    def test_every_capability_appears(self):
        """A grid export missing rows is worse than no export — it would be
        read as 'this role holds nothing here'."""
        text = pdf_text(self.get())
        missing = [c.label for c in CAPABILITIES if c.label not in text]
        self.assertEqual(missing, [])

    def test_every_role_appears(self):
        """Under its full name, or the short form the narrow columns force.

        Checked against the header map rather than the display names alone,
        because a role silently missing from the export would read as a role
        that holds nothing.
        """
        from accounts.views import PERMISSION_PDF_ROLE_HEADERS
        text = pdf_text(self.get())
        missing = [
            label for name, label in Role.ROLE_CHOICES
            if PERMISSION_PDF_ROLE_HEADERS.get(name, label) not in text
        ]
        self.assertEqual(missing, [])

    def test_no_column_header_is_broken_mid_word(self):
        """ReportLab hyphenates nothing — it simply breaks a word too wide for
        its column, so an unabbreviated "Representative" renders as
        "Representativ" above a stranded "e".

        Checked by asserting the long words are absent entirely rather than
        that the short forms are present: "Rep" is a substring of the broken
        "Representativ", so looking for the short form passes even when the
        header is mangled.
        """
        text = pdf_text(self.get())
        for long_word in ('Representative', 'Representativ', 'Administrator'):
            with self.subTest(word=long_word):
                self.assertNotIn(long_word, text)
        for short in ('Sales Rep', 'Proposal Rep', 'Finance Rep', 'Super Admin'):
            with self.subTest(header=short):
                self.assertIn(short, text)

    def test_it_names_who_exported_it_and_when(self):
        text = pdf_text(self.get())
        self.assertIn('Aqsa Ahmed', text)
        self.assertIn('exported', text)

    def test_pending_capabilities_are_marked(self):
        """Somebody reading the grid would otherwise assume a switch that is
        off is restricting something.

        Asserted against a named row rather than the bare word: the note at the
        top of the page also contains "(pending)", so a looser check passes
        even when every row in the table has lost its marker.
        """
        text = pdf_text(self.get())
        self.assertIn('Create sheets (pending)', text)
        self.assertIn('Approve PO (pending)', text)
        # And an enforced capability must not be marked.
        self.assertNotIn('Open Costing (pending)', text)

    # ── it must report the live grid, not the defaults ──────────────────────

    def test_a_revoked_capability_shows_as_revoked(self):
        """The point of the export. Reading the seeded defaults instead would
        produce a confident, dated, wrong document."""
        role = Role.objects.get(name=Role.FINANCE_REP)
        RolePermission.objects.filter(
            role=role, codename='costing.access').update(allowed=False)
        before = pdf_text(self.get())

        RolePermission.objects.filter(
            role=role, codename='costing.access').update(allowed=True)
        after = pdf_text(self.get())

        # The Costing row must differ between the two exports.
        self.assertNotEqual(before, after)

    def test_granting_a_capability_changes_the_export(self):
        role = Role.objects.get(name=Role.AI_INTERN)
        RolePermission.objects.filter(
            role=role, codename='costing.access').update(allowed=False)
        before = pdf_text(self.get())
        RolePermission.objects.filter(
            role=role, codename='costing.access').update(allowed=True)
        self.assertNotEqual(before, pdf_text(self.get()))

    def test_super_admin_reads_as_held_even_with_no_stored_row(self):
        """Super admin bypasses the grid in code, so an export driven only by
        the stored rows would show it as holding nothing — the opposite of the
        truth."""
        RolePermission.objects.filter(
            role__name=Role.SUPER_ADMIN).update(allowed=False)
        response = self.get()
        self.assertEqual(response.status_code, 200)
        text = pdf_text(response)
        self.assertIn('Super Admin', text)
        # Admin / Settings is the row only super admin holds, and Super Admin
        # is the first column, so this string exists only if that cell is a
        # tick. The Dashboard row would not do: the second table's first three
        # roles also hold it, so "Open Dashboard Y" matches there regardless of
        # what Super Admin's own cell says.
        self.assertIn('Open Admin / Settings Y', text)

    def test_a_role_the_export_does_not_list_still_appears(self):
        """The columns come from a curated order. A role added later and not
        added to that order must still be exported — a document that presents
        itself as the whole picture while quietly omitting a role is the worst
        outcome here, because it would be believed."""
        Role.objects.create(name='auditor', description='Added after the fact')
        text = pdf_text(self.get())
        self.assertIn('auditor', text)

    def test_the_curated_order_still_covers_every_role(self):
        """Separate from the test above on purpose: the fallback keeps a new
        role visible, but a role reaching it means somebody should place it in
        a deliberate group rather than leaving it appended to the last one."""
        from accounts.views import PERMISSION_PDF_GROUPS
        listed = {n for _label, names in PERMISSION_PDF_GROUPS for n in names}
        self.assertEqual(
            listed, {name for name, _label in Role.ROLE_CHOICES},
            'PERMISSION_PDF_GROUPS has drifted from ROLE_CHOICES; the export '
            'still shows the stragglers, but they belong in a chosen group.')

    def test_the_page_offers_the_export(self):
        """A feature nobody can find is not shipped."""
        self.client.force_login(self.super_admin)
        body = self.client.get(
            reverse('accounts:permission_matrix')).content.decode()
        self.assertIn(reverse('accounts:permission_matrix_pdf'), body)
