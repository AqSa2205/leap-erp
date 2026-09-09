from django.test import TestCase
from django.urls import reverse

from accounts.models import User, Role
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
        to assign a mailbox to themselves or anyone else."""
        self.client.force_login(self.sales_rep)
        resp = self.client.post(reverse('email_assignments:assign'), {'owner': self.sales_rep.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ProposalMailbox.objects.filter(owner=self.sales_rep).exists())


class AssignMailboxTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            'ea_admin2', password='x',
            role=Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0])
        self.employee = User.objects.create_user(
            'ea_employee', password='x', email='employee@leap-arabia.com')
        self.client.force_login(self.admin)

    def test_assign_creates_a_mailbox_using_the_employees_own_email_on_file(self):
        resp = self.client.post(reverse('email_assignments:assign'), {'owner': self.employee.pk})
        self.assertEqual(resp.status_code, 302)
        mailbox = ProposalMailbox.objects.get(owner=self.employee)
        self.assertEqual(mailbox.email_address, 'employee@leap-arabia.com')
        self.assertTrue(mailbox.is_active)
        self.assertEqual(mailbox.assigned_by, self.admin)

    def test_the_form_has_no_free_text_email_field_at_all(self):
        """The whole point of the fix: there is no field an admin could type
        a mismatched address into, even if they wanted to — closing the
        'grant employee A read access to employee B's real mailbox' hole
        rather than just discouraging it."""
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertNotContains(resp, 'name="email_address"')

    def test_a_crafted_post_cannot_smuggle_a_different_email_address(self):
        """Even if a POST includes an email_address field by hand, the form
        doesn't declare it, so Django ignores it — the mailbox still ends up
        using the employee's own address on file."""
        resp = self.client.post(reverse('email_assignments:assign'), {
            'owner': self.employee.pk, 'email_address': 'ceo@leap-arabia.com'})
        self.assertEqual(resp.status_code, 302)
        mailbox = ProposalMailbox.objects.get(owner=self.employee)
        self.assertEqual(mailbox.email_address, 'employee@leap-arabia.com')

    def test_employee_who_already_has_a_mailbox_is_not_offered_again(self):
        ProposalMailbox.objects.create(owner=self.employee, email_address='a@leap-arabia.com')
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertNotContains(resp, f'value="{self.employee.pk}"')

    def test_employee_with_no_email_on_file_cannot_be_assigned(self):
        no_email_employee = User.objects.create_user('ea_noemail', password='x')
        resp = self.client.post(reverse('email_assignments:assign'), {'owner': no_email_employee.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ProposalMailbox.objects.filter(owner=no_email_employee).exists())

    def test_duplicate_email_address_on_file_is_rejected(self):
        """Two User accounts sharing the same email on file (a pre-existing
        HR data issue) must not be able to collide into one ProposalMailbox
        row via a raw IntegrityError — the form should catch it cleanly."""
        other = User.objects.create_user('ea_other', password='x', email='dup@leap-arabia.com')
        ProposalMailbox.objects.create(owner=other, email_address='dup@leap-arabia.com')
        dup_employee = User.objects.create_user('ea_dup', password='x', email='dup@leap-arabia.com')
        resp = self.client.post(reverse('email_assignments:assign'), {'owner': dup_employee.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ProposalMailbox.objects.filter(owner=dup_employee).exists())

    def test_toggle_revokes_then_reactivates(self):
        mailbox = ProposalMailbox.objects.create(owner=self.employee, email_address='a@leap-arabia.com')
        self.client.post(reverse('email_assignments:toggle', kwargs={'pk': mailbox.pk}))
        mailbox.refresh_from_db()
        self.assertFalse(mailbox.is_active)
        self.assertEqual(mailbox.revoked_by, self.admin)
        self.assertIsNotNone(mailbox.revoked_at)
        self.client.post(reverse('email_assignments:toggle', kwargs={'pk': mailbox.pk}))
        mailbox.refresh_from_db()
        self.assertTrue(mailbox.is_active)
        # Reactivating clears the stale revoke record — an active row
        # shouldn't still claim to have been revoked by someone.
        self.assertIsNone(mailbox.revoked_by)
        self.assertIsNone(mailbox.revoked_at)

    def test_delete_removes_the_row(self):
        mailbox = ProposalMailbox.objects.create(owner=self.employee, email_address='a@leap-arabia.com')
        self.client.post(reverse('email_assignments:delete', kwargs={'pk': mailbox.pk}))
        self.assertFalse(ProposalMailbox.objects.filter(pk=mailbox.pk).exists())

    def test_get_is_not_allowed_on_mutating_endpoints(self):
        mailbox = ProposalMailbox.objects.create(owner=self.employee, email_address='a@leap-arabia.com')
        self.assertEqual(self.client.get(reverse('email_assignments:assign')).status_code, 405)
        self.assertEqual(
            self.client.get(reverse('email_assignments:toggle', kwargs={'pk': mailbox.pk})).status_code, 405)
        self.assertEqual(
            self.client.get(reverse('email_assignments:delete', kwargs={'pk': mailbox.pk})).status_code, 405)


class RequireEmailAttachmentToggleTests(TestCase):
    """The 'Require Email Attachment' export-lock widget: same access as
    the rest of the Email Assigning page, ERP Admin + Super Admin — this
    used to live in Django admin's ProposalDepartmentFeatureAdmin."""

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
