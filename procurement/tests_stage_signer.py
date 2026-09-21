"""The name printed under each approval stage on a purchase order.

It was a hardcoded constant, so a change of PM needed a code release. It is
now set on the Approval Routing page, with the constant as the fallback -
which means every test here has to pin down what happens when the stored name
is absent, blank, or the same as the default, because those three are the
states a half-configured system is actually in.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from procurement.models import POStageApprover, PurchaseOrder

PM_DEFAULT = dict((k, s) for k, _l, s in PurchaseOrder.APPROVAL_STAGES)['pm']


def a_po(number='PO-SIGN-1', **kw):
    values = dict(po_date=date(2026, 1, 1), po_number=number,
                  vendor_name='ACME', po_issued_by='Tester')
    values.update(kw)
    return PurchaseOrder.objects.create(**values)


def a_super_admin(username='boss'):
    role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
    return User.objects.create_user(username, password='pw', role=role)


def a_user(username, role_name, first='', last=''):
    role, _ = Role.objects.get_or_create(name=role_name)
    return User.objects.create_user(
        username, password='pw', role=role, first_name=first, last_name=last)


class EffectiveNameTests(TestCase):

    def test_the_built_in_name_stands_when_nothing_is_configured(self):
        """The fallback is the whole reason this is safe to deploy: every
        existing purchase order keeps printing exactly what it printed."""
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], PM_DEFAULT)

    def test_a_stored_name_wins(self):
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], 'Nadia Rahman')

    def test_a_blank_stored_name_is_not_a_name(self):
        """A row created for routing alone must not blank the printed name -
        an empty line under a signature is worse than a stale one."""
        POStageApprover.objects.create(
            stage='pm', user=a_super_admin('routed'), signer_name='')
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], PM_DEFAULT)

    def test_every_stage_is_present_in_the_map(self):
        names = POStageApprover.effective_signer_names()
        for key, _label, _default in PurchaseOrder.APPROVAL_STAGES:
            self.assertTrue(names[key])


class PrintedNameTests(TestCase):

    def test_the_po_prints_the_configured_name(self):
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        po = a_po()
        pm = [s for s in po.approval_status if s['key'] == 'pm'][0]
        self.assertEqual(pm['signer'], 'Nadia Rahman')

    def test_the_po_prints_the_default_when_unset(self):
        po = a_po()
        pm = [s for s in po.approval_status if s['key'] == 'pm'][0]
        self.assertEqual(pm['signer'], PM_DEFAULT)

    def test_other_stages_are_untouched(self):
        """Stages are configured one at a time; setting one must not disturb
        the names printed for the rest of the signature block."""
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        po = a_po()
        scm = [s for s in po.approval_status if s['key'] == 'scm'][0]
        self.assertEqual(
            scm['signer'],
            dict((k, s) for k, _l, s in PurchaseOrder.APPROVAL_STAGES)['scm'])


class DesignatedApproverTests(TestCase):
    """The printed name is also the inbox key.

    is_designated_approver() matches the signed-in user against it, so a
    rename has to move the purchase order out of one inbox and into another -
    otherwise the PO would print one person's name while sitting in another
    person's list.
    """

    def setUp(self):
        self.po = a_po()
        self.incumbent = a_user('incumbent', Role.SALES_REP,
                                *PM_DEFAULT.split(' ', 1))
        self.successor = a_user('successor', Role.SALES_REP, 'Nadia', 'Rahman')

    def test_the_default_name_holds_the_stage_before_any_change(self):
        self.assertTrue(self.po.is_designated_approver(self.incumbent, 'pm'))
        self.assertFalse(self.po.is_designated_approver(self.successor, 'pm'))

    def test_a_rename_moves_the_stage_to_the_new_person(self):
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        po = a_po('PO-SIGN-2')
        self.assertTrue(po.is_designated_approver(self.successor, 'pm'))
        self.assertFalse(po.is_designated_approver(self.incumbent, 'pm'))

    def test_a_rename_does_not_grant_permission_to_sign(self):
        """The gate is can_user_approve_stage(), which is role-based. Being
        named must not become a way to hand somebody signing rights."""
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        po = a_po('PO-SIGN-3')
        self.assertFalse(po.can_user_approve_stage(self.successor, 'pm'))


class QueryCountTests(TestCase):
    """The names are read where the sidebar walks every open purchase order
    on every page in the app, so the lookup must not scale with the list."""

    def setUp(self):
        self.user = a_super_admin()
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')

    def _queries_for(self, n):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        PurchaseOrder.objects.all().delete()
        for i in range(n):
            a_po(f'PO-Q-{i}')
        pos = PurchaseOrder.objects.all()
        with CaptureQueriesContext(connection) as ctx:
            for po in PurchaseOrder.prime_signer_names(pos):
                po.approval_status
        return len([q for q in ctx.captured_queries
                    if 'postageapprover' in q['sql'].lower()])

    def test_priming_reads_the_names_once_however_many_pos(self):
        self.assertEqual(self._queries_for(2), 1)
        self.assertEqual(self._queries_for(6), 1)

    def _page_lookups(self, url, n):
        """Signer-name queries issued while rendering a list page.

        Compared across two data sizes rather than pinned: the sidebar badge
        reads the names too, so the absolute number is not 1 and would be a
        magic constant that any unrelated change breaks.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        PurchaseOrder.objects.all().delete()
        for i in range(n):
            a_po(f'PO-PAGE-{i}')
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        return len([q for q in ctx.captured_queries
                    if 'postageapprover' in q['sql'].lower()])

    def test_the_po_list_reads_the_names_once(self):
        """Every row renders workflow_status, which reaches the names. This
        page had no query-count test of its own - the POs-by-project one
        caught the same N+1 first, which is the only reason it was noticed."""
        url = reverse('procurement:po_list')
        self.assertEqual(self._page_lookups(url, 2),
                         self._page_lookups(url, 6))

    def test_the_pos_by_project_page_reads_the_names_once(self):
        url = reverse('procurement:po_by_project')
        self.assertEqual(self._page_lookups(url, 2),
                         self._page_lookups(url, 6))

    def test_a_single_po_memoises_its_own_lookup(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        po = a_po('PO-Q-SINGLE')
        with CaptureQueriesContext(connection) as ctx:
            po.approval_status
            po.approval_status
            po.is_designated_approver(self.user, 'pm')
        self.assertEqual(
            len([q for q in ctx.captured_queries
                 if 'postageapprover' in q['sql'].lower()]), 1)


class POListRendersWithoutACreatorTests(TestCase):
    """created_by is SET_NULL, so deleting a user leaves purchase orders whose
    creator is None. The list rendered that through a `default:` FILTER
    ARGUMENT, and a failed filter argument raises where a failed variable is
    silently blank - so one deleted account took the whole page down with a
    500 for everybody. Found by a query-count test, not by anyone reading it.
    """

    def test_a_po_with_no_creator_still_renders(self):
        a_po('PO-NOCREATOR')
        self.client.force_login(a_super_admin())
        resp = self.client.get(reverse('procurement:po_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'PO-NOCREATOR')

    def test_a_creators_name_is_still_shown(self):
        boss = a_super_admin('named')
        boss.first_name, boss.last_name = 'Nadia', 'Rahman'
        boss.save()
        a_po('PO-CREATOR', created_by=boss)
        self.client.force_login(a_super_admin())
        resp = self.client.get(reverse('procurement:po_list'))
        self.assertContains(resp, 'Nadia Rahman')


class SetSignerViewTests(TestCase):

    def setUp(self):
        self.url = reverse('procurement:po_stage_signer')
        self.page = reverse('procurement:po_stage_approvers')

    def _post(self, user, **data):
        self.client.force_login(user)
        payload = {'stage': 'pm', 'signer_name': 'Nadia Rahman'}
        payload.update(data)
        return self.client.post(self.url, payload)

    def test_a_super_admin_can_set_the_name(self):
        self._post(a_super_admin())
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], 'Nadia Rahman')

    def test_blank_restores_the_built_in_name(self):
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        self._post(a_super_admin(), signer_name='')
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], PM_DEFAULT)

    def test_internal_whitespace_is_collapsed(self):
        """_same_person() normalises the same way, so a double space typed
        here would be invisible on the page and still match - storing it
        normalised keeps what is shown and what is matched identical."""
        self._post(a_super_admin(), signer_name='  Nadia   Rahman  ')
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], 'Nadia Rahman')

    def test_an_over_long_name_is_refused(self):
        """The column is 120 characters; without this the save raises rather
        than telling anyone what was wrong."""
        self._post(a_super_admin(), signer_name='N' * 121)
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], PM_DEFAULT)

    def test_a_name_of_exactly_the_limit_is_accepted(self):
        self._post(a_super_admin(), signer_name='N' * 120)
        self.assertEqual(
            POStageApprover.effective_signer_names()['pm'], 'N' * 120)

    def test_an_unknown_stage_is_refused(self):
        self._post(a_super_admin(), stage='cfo')
        self.assertFalse(POStageApprover.objects.exists())

    def test_an_admin_is_refused(self):
        """Routing beside it is super-admin only for the same reason: this is
        the name on a signed company document."""
        resp = self._post(a_user('adm', Role.ADMIN))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(POStageApprover.objects.exists())

    def test_anonymous_is_refused(self):
        resp = self.client.post(self.url, {'stage': 'pm',
                                           'signer_name': 'Nadia Rahman'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(POStageApprover.objects.exists())

    def test_get_is_refused(self):
        self.client.force_login(a_super_admin())
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_setting_the_name_keeps_the_routed_account(self):
        """The two halves are edited by separate forms on one page; neither
        may quietly discard the other."""
        routed = a_super_admin('routed')
        POStageApprover.objects.create(stage='pm', user=routed)
        self._post(a_super_admin())
        row = POStageApprover.objects.get(stage='pm')
        self.assertEqual(row.user, routed)
        self.assertEqual(row.signer_name, 'Nadia Rahman')

    def test_routing_keeps_the_printed_name(self):
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        boss = a_super_admin()
        self.client.force_login(boss)
        self.client.post(self.page, {'stage': 'pm', 'user': boss.pk})
        row = POStageApprover.objects.get(stage='pm')
        self.assertEqual(row.user, boss)
        self.assertEqual(row.signer_name, 'Nadia Rahman')

    def test_who_changed_it_is_recorded(self):
        boss = a_super_admin()
        self._post(boss)
        self.assertEqual(
            POStageApprover.objects.get(stage='pm').updated_by, boss)


class RoutingPageTests(TestCase):

    def setUp(self):
        self.client.force_login(a_super_admin())
        self.url = reverse('procurement:po_stage_approvers')

    def test_the_page_offers_the_name_box_with_the_default_as_placeholder(self):
        resp = self.client.get(self.url)
        # The type matters: a hidden field would satisfy a looser assertion
        # while leaving the name uneditable, which is the whole feature.
        self.assertContains(resp, '<input type="text" name="signer_name"')
        self.assertContains(resp, f'placeholder="{PM_DEFAULT}"')

    def test_a_configured_name_is_shown_as_the_value(self):
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        resp = self.client.get(self.url)
        self.assertContains(resp, 'value="Nadia Rahman"')
        row = [r for r in resp.context['rows'] if r['key'] == 'pm'][0]
        self.assertFalse(row['is_default'])

    def test_an_unconfigured_stage_reports_itself_as_default(self):
        resp = self.client.get(self.url)
        row = [r for r in resp.context['rows'] if r['key'] == 'pm'][0]
        self.assertTrue(row['is_default'])
        self.assertEqual(row['signer_name'], PM_DEFAULT)

    def test_an_ordinary_admin_cannot_open_the_page(self):
        self.client.force_login(a_user('adm2', Role.ADMIN))
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


class UnroutedStageTests(TestCase):
    """A row may now carry a printed name and no account.

    The email fallback has to treat that as "nobody configured", or naming a
    signer would silently stop telling the role holders about the stage.
    """

    def test_a_name_only_row_does_not_capture_the_notifications(self):
        from procurement.notifications import stage_recipients
        admin = a_user('adm3', Role.ADMIN)
        POStageApprover.objects.create(stage='pm', signer_name='Nadia Rahman')
        self.assertIn(admin, stage_recipients('pm'))

    def test_a_routed_row_still_wins(self):
        from procurement.notifications import stage_recipients
        a_user('adm4', Role.ADMIN)
        routed = a_super_admin('routed2')
        POStageApprover.objects.create(
            stage='pm', user=routed, signer_name='Nadia Rahman')
        self.assertEqual(stage_recipients('pm'), [routed])
