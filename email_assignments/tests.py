from django.test import TestCase
from django.urls import reverse

from accounts.models import User, Role
from costing.models import RevisionMailbox
from projects.models import MonitoredMailbox
from proposals.models import ProposalMailbox, ProposalDepartmentFeature


class EmailAssigningAccessTests(TestCase):
    """Only ERP Admin and Super Admin can reach the Email Assigning screen —
    same two roles the sidebar's Administration section is already gated
    on. Everyone else, including the employee whose own mailbox this is,
    gets redirected rather than a 403 page."""

    def setUp(self):
        self.super_admin = User.objects.create_user(
            'ea_super', password='x',
            role=Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0])
        self.erp_admin = User.objects.create_user(
            'ea_erpadmin', password='x',
            role=Role.objects.get_or_create(name=Role.ERP_ADMIN)[0])
        self.sales_rep = User.objects.create_user(
            'ea_sales', password='x',
            role=Role.objects.get_or_create(name=Role.SALES_REP)[0])

    def test_super_admin_can_view_the_list(self):
        self.client.force_login(self.super_admin)
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertEqual(resp.status_code, 200)

    def test_erp_admin_can_view_the_list(self):
        self.client.force_login(self.erp_admin)
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertEqual(resp.status_code, 200)

    def test_other_roles_are_redirected_away(self):
        self.client.force_login(self.sales_rep)
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertEqual(resp.status_code, 302)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login', resp.url)

    def test_non_admin_cannot_assign_via_a_crafted_post(self):
        """Access control has to hold on the mutating endpoints too, not
        just the list page — a sales rep POSTing directly must not be able
        to assign a mailbox to themselves or anyone else. Checked against
        the Technical Proposals endpoint as the representative case — the
        other two tabs share the exact same permission check, verified
        individually in their own test classes below."""
        self.client.force_login(self.sales_rep)
        resp = self.client.post(reverse('email_assignments:assign_proposal'), {'owner': self.sales_rep.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ProposalMailbox.objects.filter(owner=self.sales_rep).exists())

    def test_all_three_tabs_are_present_on_one_page(self):
        """The whole point of this app: one screen, three tabs, instead of
        three separate Django admin pages."""
        self.client.force_login(self.super_admin)
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertContains(resp, 'Technical Proposals')
        self.assertContains(resp, 'Commercial Pipeline')
        self.assertContains(resp, 'Costing')


class _MailboxAssignTestsBase:
    """Shared test bodies for the three per-feature mailbox-assignment
    screens. All three route through the same shared implementation
    (email_assignments.views._assign_mailbox/_toggle_mailbox/_delete_mailbox
    and forms._MailboxAssignFormBase) — one parameterised suite here rather
    than tripling near-identical test methods for logic that is already
    deliberately shared, not copy-pasted, in the app itself.

    Subclasses set: model, username_prefix, assign_url, toggle_url,
    delete_url (the last three as 'email_assignments:...' url names)."""

    model = None
    username_prefix = None
    form_prefix = None  # matches the auto_id given to this tab's form in views.py
    assign_url = None
    toggle_url = None
    delete_url = None

    def _owner_select_html(self, resp):
        """Isolates just this tab's own <select> — all three tabs' markup
        is present in the DOM at once (only CSS/JS hides the inactive
        ones), so a plain substring check for an option value could match
        a DIFFERENT tab's dropdown instead of this one's."""
        content = resp.content.decode()
        select_id = f'id="id_{self.form_prefix}_owner"'
        start = content.index(select_id)
        end = content.index('</select>', start)
        return content[start:end]

    def setUp(self):
        self.admin = User.objects.create_user(
            f'{self.username_prefix}_admin', password='x',
            role=Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0])
        self.employee = User.objects.create_user(
            f'{self.username_prefix}_employee', password='x',
            email=f'{self.username_prefix}@leap-arabia.com')
        self.client.force_login(self.admin)

    def test_assign_creates_a_mailbox_using_the_employees_own_email_on_file(self):
        resp = self.client.post(reverse(self.assign_url), {'owner': self.employee.pk})
        self.assertEqual(resp.status_code, 302)
        mailbox = self.model.objects.get(owner=self.employee)
        self.assertEqual(mailbox.email_address, self.employee.email)
        self.assertTrue(mailbox.is_active)
        self.assertEqual(mailbox.assigned_by, self.admin)

    def test_a_crafted_post_cannot_smuggle_a_different_email_address(self):
        """Even if a POST includes an email_address field by hand, the form
        doesn't declare it, so Django ignores it — the mailbox still ends up
        using the employee's own address on file."""
        resp = self.client.post(reverse(self.assign_url), {
            'owner': self.employee.pk, 'email_address': 'ceo@leap-arabia.com'})
        self.assertEqual(resp.status_code, 302)
        mailbox = self.model.objects.get(owner=self.employee)
        self.assertEqual(mailbox.email_address, self.employee.email)

    def test_employee_who_already_has_a_mailbox_is_not_offered_again(self):
        self.model.objects.create(owner=self.employee, email_address=self.employee.email)
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertNotIn(f'value="{self.employee.pk}"', self._owner_select_html(resp))

    def test_employee_with_no_email_on_file_cannot_be_assigned(self):
        no_email_employee = User.objects.create_user(f'{self.username_prefix}_noemail', password='x')
        resp = self.client.post(reverse(self.assign_url), {'owner': no_email_employee.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.model.objects.filter(owner=no_email_employee).exists())

    def test_two_accounts_can_no_longer_share_an_address_at_all(self):
        """This used to assert the form caught two User accounts sharing an
        address - "a pre-existing HR data issue". User.email is unique now,
        case-insensitively, so the database refuses that state before any
        form runs - a stronger guarantee than catching it afterwards, and now
        asserted for every mailbox type rather than one.

        It matters here specifically: the mailbox is read from this address
        through app-only Mail.Read, which reads whichever mailbox it is
        handed. Two accounts claiming one address is the shape of a
        colleague's inbox reachable from the wrong account.
        """
        from django.db import IntegrityError, transaction
        other = User.objects.create_user(
            f'{self.username_prefix}_other', password='x',
            email=f'dup_{self.username_prefix}@leap-arabia.com')
        self.model.objects.create(owner=other, email_address=other.email)
        for variant in (other.email, other.email.upper()):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    User.objects.create_user(
                        f'{self.username_prefix}_dup', password='x',
                        email=variant)
        self.assertEqual(
            User.objects.filter(email__iexact=other.email).count(), 1)

    def test_toggle_revokes_then_reactivates(self):
        mailbox = self.model.objects.create(owner=self.employee, email_address=self.employee.email)
        self.client.post(reverse(self.toggle_url, kwargs={'pk': mailbox.pk}))
        mailbox.refresh_from_db()
        self.assertFalse(mailbox.is_active)
        self.assertEqual(mailbox.revoked_by, self.admin)
        self.assertIsNotNone(mailbox.revoked_at)
        self.client.post(reverse(self.toggle_url, kwargs={'pk': mailbox.pk}))
        mailbox.refresh_from_db()
        self.assertTrue(mailbox.is_active)
        # Reactivating clears the stale revoke record — an active row
        # shouldn't still claim to have been revoked by someone.
        self.assertIsNone(mailbox.revoked_by)
        self.assertIsNone(mailbox.revoked_at)

    def test_delete_removes_the_row(self):
        mailbox = self.model.objects.create(owner=self.employee, email_address=self.employee.email)
        self.client.post(reverse(self.delete_url, kwargs={'pk': mailbox.pk}))
        self.assertFalse(self.model.objects.filter(pk=mailbox.pk).exists())

    def test_get_is_not_allowed_on_mutating_endpoints(self):
        mailbox = self.model.objects.create(owner=self.employee, email_address=self.employee.email)
        self.assertEqual(self.client.get(reverse(self.assign_url)).status_code, 405)
        self.assertEqual(
            self.client.get(reverse(self.toggle_url, kwargs={'pk': mailbox.pk})).status_code, 405)
        self.assertEqual(
            self.client.get(reverse(self.delete_url, kwargs={'pk': mailbox.pk})).status_code, 405)

    def test_non_admin_cannot_assign(self):
        outsider = User.objects.create_user(
            f'{self.username_prefix}_outsider', password='x',
            role=Role.objects.get_or_create(name=Role.SALES_REP)[0])
        self.client.force_login(outsider)
        resp = self.client.post(reverse(self.assign_url), {'owner': self.employee.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.model.objects.filter(owner=self.employee).exists())


class ProposalMailboxAssignTests(_MailboxAssignTestsBase, TestCase):
    model = ProposalMailbox
    username_prefix = 'tp'
    form_prefix = 'proposal'
    assign_url = 'email_assignments:assign_proposal'
    toggle_url = 'email_assignments:toggle_proposal'
    delete_url = 'email_assignments:delete_proposal'

    def test_the_form_has_no_free_text_email_field_at_all(self):
        """The whole point of the fix this app shipped with: there is no
        field an admin could type a mismatched address into, even if they
        wanted to — closing the 'grant employee A read access to employee
        B's real mailbox' hole rather than just discouraging it."""
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertNotContains(resp, 'name="email_address"')


class RevisionMailboxAssignTests(_MailboxAssignTestsBase, TestCase):
    model = RevisionMailbox
    username_prefix = 'costing'
    form_prefix = 'revision'
    assign_url = 'email_assignments:assign_revision'
    toggle_url = 'email_assignments:toggle_revision'
    delete_url = 'email_assignments:delete_revision'


class MonitoredMailboxAssignTests(_MailboxAssignTestsBase, TestCase):
    model = MonitoredMailbox
    username_prefix = 'pipeline'
    form_prefix = 'monitored'
    assign_url = 'email_assignments:assign_monitored'
    toggle_url = 'email_assignments:toggle_monitored'
    delete_url = 'email_assignments:delete_monitored'


class MailboxTypesDontCollideTests(TestCase):
    """The three mailbox tables are independent — the same employee can
    have a mailbox for one feature but not another, and the same email
    address can be reused across features (each table's uniqueness is its
    own, not global) since that's how a real employee's one inbox naturally
    gets used by whichever of these features they actually touch."""

    def setUp(self):
        self.admin = User.objects.create_user(
            'cross_admin', password='x',
            role=Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0])
        self.client.force_login(self.admin)

    def test_same_email_address_can_be_used_across_all_three_features(self):
        employee = User.objects.create_user(
            'cross_employee', password='x', email='cross@leap-arabia.com')
        ProposalMailbox.objects.create(owner=employee, email_address=employee.email)
        RevisionMailbox.objects.create(owner=employee, email_address=employee.email)
        MonitoredMailbox.objects.create(owner=employee, email_address=employee.email)
        self.assertEqual(ProposalMailbox.objects.get(owner=employee).email_address, employee.email)
        self.assertEqual(RevisionMailbox.objects.get(owner=employee).email_address, employee.email)
        self.assertEqual(MonitoredMailbox.objects.get(owner=employee).email_address, employee.email)

    def test_employee_can_be_assigned_for_one_feature_without_the_others(self):
        employee = User.objects.create_user(
            'partial_employee', password='x', email='partial@leap-arabia.com')
        resp = self.client.post(
            reverse('email_assignments:assign_revision'), {'owner': employee.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(RevisionMailbox.objects.filter(owner=employee).exists())
        self.assertFalse(ProposalMailbox.objects.filter(owner=employee).exists())
        self.assertFalse(MonitoredMailbox.objects.filter(owner=employee).exists())
        # Still offered on the other two tabs' dropdowns, since only this
        # feature's own mailbox is taken.
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertContains(resp, f'value="{employee.pk}"')


class RequireEmailAttachmentToggleTests(TestCase):
    """The 'Require Email Attachment' export-lock widget: same access as
    the rest of the Email Assigning page, ERP Admin + Super Admin — this
    used to live in Django admin's ProposalDepartmentFeatureAdmin. Technical
    Proposal specific — Costing and Commercial Pipeline have no equivalent
    concept, so this only ever appears on that one tab."""

    def setUp(self):
        self.super_admin = User.objects.create_user(
            'ea_lock_super', password='x',
            role=Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0])
        self.erp_admin = User.objects.create_user(
            'ea_lock_erpadmin', password='x',
            role=Role.objects.get_or_create(name=Role.ERP_ADMIN)[0])
        self.sales_rep = User.objects.create_user(
            'ea_lock_sales', password='x',
            role=Role.objects.get_or_create(name=Role.SALES_REP)[0])

    def test_super_admin_sees_the_widget(self):
        self.client.force_login(self.super_admin)
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertContains(resp, 'Require Email Attachment')

    def test_erp_admin_also_sees_the_widget(self):
        self.client.force_login(self.erp_admin)
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertContains(resp, 'Require Email Attachment')

    def test_super_admin_can_toggle_a_department_lock_on_then_off(self):
        self.client.force_login(self.super_admin)
        self.client.post(reverse('email_assignments:toggle_department_lock', kwargs={'department': 'ai'}))
        row = ProposalDepartmentFeature.objects.get(department='ai')
        self.assertTrue(row.requires_client_email_to_export)
        self.assertEqual(row.updated_by, self.super_admin)
        self.client.post(reverse('email_assignments:toggle_department_lock', kwargs={'department': 'ai'}))
        row.refresh_from_db()
        self.assertFalse(row.requires_client_email_to_export)

    def test_erp_admin_can_also_toggle_a_department_lock(self):
        self.client.force_login(self.erp_admin)
        self.client.post(reverse('email_assignments:toggle_department_lock', kwargs={'department': 'ai'}))
        row = ProposalDepartmentFeature.objects.get(department='ai')
        self.assertTrue(row.requires_client_email_to_export)
        self.assertEqual(row.updated_by, self.erp_admin)

    def test_other_roles_cannot_toggle_a_department_lock(self):
        self.client.force_login(self.sales_rep)
        resp = self.client.post(
            reverse('email_assignments:toggle_department_lock', kwargs={'department': 'ai'}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ProposalDepartmentFeature.objects.filter(department='ai').exists())

    def test_other_department_can_be_toggled_too(self):
        """'Other' is a real, lockable department like AI/Telecom/Security —
        same on/off switch for it."""
        self.client.force_login(self.super_admin)
        self.client.post(reverse('email_assignments:toggle_department_lock', kwargs={'department': 'other'}))
        row = ProposalDepartmentFeature.objects.get(department='other')
        self.assertTrue(row.requires_client_email_to_export)

    def test_a_made_up_department_is_still_refused(self):
        self.client.force_login(self.super_admin)
        self.client.post(reverse('email_assignments:toggle_department_lock', kwargs={'department': 'bogus'}))
        self.assertFalse(ProposalDepartmentFeature.objects.filter(department='bogus').exists())
