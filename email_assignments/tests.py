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
        resp = self.client.post(reverse('email_assignments:assign'), {
            'owner': self.sales_rep.pk, 'email_address': 'sneaky@leap-arabia.com'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ProposalMailbox.objects.filter(owner=self.sales_rep).exists())


class AssignMailboxTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            'ea_admin2', password='x',
            role=Role.objects.get_or_create(name=Role.SUPER_ADMIN)[0])
        self.employee = User.objects.create_user('ea_employee', password='x')
        self.client.force_login(self.admin)

    def test_assign_creates_a_mailbox(self):
        resp = self.client.post(reverse('email_assignments:assign'), {
            'owner': self.employee.pk, 'email_address': 'employee@leap-arabia.com'})
        self.assertEqual(resp.status_code, 302)
        mailbox = ProposalMailbox.objects.get(owner=self.employee)
        self.assertEqual(mailbox.email_address, 'employee@leap-arabia.com')
        self.assertTrue(mailbox.is_active)
        self.assertEqual(mailbox.assigned_by, self.admin)

    def test_employee_who_already_has_a_mailbox_is_not_offered_again(self):
        ProposalMailbox.objects.create(owner=self.employee, email_address='a@leap-arabia.com')
        resp = self.client.get(reverse('email_assignments:list'))
        self.assertNotContains(resp, f'value="{self.employee.pk}"')

    def test_duplicate_email_address_is_rejected(self):
        other = User.objects.create_user('ea_other', password='x')
        ProposalMailbox.objects.create(owner=other, email_address='dup@leap-arabia.com')
        resp = self.client.post(reverse('email_assignments:assign'), {
            'owner': self.employee.pk, 'email_address': 'dup@leap-arabia.com'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ProposalMailbox.objects.filter(owner=self.employee).exists())

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
