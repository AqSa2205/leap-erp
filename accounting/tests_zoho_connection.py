"""Connecting Zoho Books from the browser.

This screen exists because the web service runs on a plan with no shell, so the
`zoho_connect` and `sync_zoho_accounts` commands can be run on a laptop and
nowhere else. That is how the live system ended up with no Zoho data at all
while a developer's machine had all of it.

The awkward cases are the ones worth pinning: a secret that must never be
rendered back, a grant code that is single-use, an organisation id that can be
valid-looking and still wrong, and a sync that must never touch the ERP's own
chart.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role
from accounting.models import Account, ZohoAccountMap, ZohoCredentials
from accounting.zoho import ZohoAuthError, ZohoError

User = get_user_model()

ZOHO_ACCOUNTS = [
    {'account_id': '1001', 'account_code': '', 'account_name': 'Petty Cash',
     'account_type': 'cash', 'is_active': True},
    {'account_id': '1002', 'account_code': '', 'account_name': 'Trade Debtors',
     'account_type': 'accounts_receivable', 'is_active': True},
]


class ZohoConnectionScreenTests(TestCase):

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.finance = User.objects.create_user(
            'zc-super', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.outsider = User.objects.create_user(
            'zc-out', password='x',
            role=Role.objects.get(name=Role.DOCUMENT_CONTROLLER))
        self.url = reverse('accounting:zoho_connection')

    def _configured(self, **overrides):
        creds = ZohoCredentials.load()
        values = dict(client_id='1000.ABC', client_secret='s3cr3t-do-not-render',
                      organization_id='768283719', refresh_token='1000.refresh')
        values.update(overrides)
        for field, value in values.items():
            setattr(creds, field, value)
        creds.save()
        return creds

    def _as_finance(self):
        self.client.force_login(self.finance)

    # ── access ──────────────────────────────────────────────────────────────

    def test_someone_outside_finance_cannot_open_it(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_someone_outside_finance_cannot_change_credentials(self):
        self.client.force_login(self.outsider)
        self.client.post(self.url, {'action': 'credentials', 'client_id': 'theirs'})
        self.assertNotEqual(ZohoCredentials.load().client_id, 'theirs')

    def test_someone_outside_finance_cannot_trigger_a_sync(self):
        self._configured()
        self.client.force_login(self.outsider)
        with mock.patch('accounting.views.ZohoClient') as client:
            self.client.post(self.url, {'action': 'sync'})
        client.assert_not_called()

    # ── the secret ──────────────────────────────────────────────────────────

    def test_the_client_secret_is_never_rendered_back(self):
        """It would otherwise sit in every browser cache and screenshot that
        ever touched this page."""
        self._configured()
        self._as_finance()
        body = self.client.get(self.url).content.decode()
        self.assertNotIn('s3cr3t-do-not-render', body)

    def test_the_page_still_says_whether_a_secret_is_on_file(self):
        """Hiding it must not turn into 'is it even set?'."""
        self._configured()
        self._as_finance()
        self.assertTrue(self.client.get(self.url).context['has_secret'])

    def test_a_blank_secret_keeps_the_stored_one(self):
        """So an organisation id can be corrected without re-typing a secret
        that is no longer displayed anywhere."""
        self._configured()
        self._as_finance()
        self.client.post(self.url, {'action': 'credentials',
                                    'client_id': '1000.ABC',
                                    'organization_id': '999'})
        creds = ZohoCredentials.load()
        self.assertEqual(creds.client_secret, 's3cr3t-do-not-render')
        self.assertEqual(creds.organization_id, '999')

    def test_a_supplied_secret_replaces_the_stored_one(self):
        self._configured()
        self._as_finance()
        self.client.post(self.url, {'action': 'credentials',
                                    'client_secret': 'rotated'})
        self.assertEqual(ZohoCredentials.load().client_secret, 'rotated')

    # ── connecting ──────────────────────────────────────────────────────────

    def test_connecting_without_credentials_says_which_are_missing(self):
        self._as_finance()
        response = self.client.post(self.url, {'action': 'connect',
                                               'code': '1000.grant'}, follow=True)
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('Client ID', text)

    def test_connecting_without_a_code_does_not_call_zoho(self):
        self._configured(refresh_token='')
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            self.client.post(self.url, {'action': 'connect', 'code': '  '})
        client.assert_not_called()

    def test_a_successful_exchange_reports_the_organisation(self):
        self._configured(refresh_token='')
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.organization.return_value = {
                'organizations': [{'organization_id': '768283719',
                                   'name': 'LEAP NETWORKS ARABIA',
                                   'currency_code': 'SAR'}]}
            response = self.client.post(self.url, {'action': 'connect',
                                                   'code': '1000.grant'}, follow=True)
        client.return_value.exchange_grant_token.assert_called_once_with('1000.grant')
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('LEAP NETWORKS ARABIA', text)

    def test_a_mismatched_organisation_id_is_called_out(self):
        """A stored refresh token only proves the exchange worked. Requests
        still fail if the organisation id is not one Zoho serves, and that
        failure looks nothing like a configuration mistake."""
        self._configured(refresh_token='', organization_id='111111')
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.organization.return_value = {
                'organizations': [{'organization_id': '768283719',
                                   'name': 'Someone Else', 'currency_code': 'SAR'}]}
            response = self.client.post(self.url, {'action': 'connect',
                                                   'code': '1000.grant'}, follow=True)
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('not one Zoho serves', text)

    def test_a_rejected_code_is_reported_not_tracebacked(self):
        """The usual case: the code expired or was already spent."""
        self._configured(refresh_token='')
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.exchange_grant_token.side_effect = ZohoAuthError(
                'Zoho did not return a refresh token')
            response = self.client.post(self.url, {'action': 'connect',
                                                   'code': '1000.stale'}, follow=True)
        self.assertEqual(response.status_code, 200)
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('did not return a refresh token', text)

    def test_the_grant_code_is_never_stored(self):
        """Single-use and short-lived — keeping it serves nothing and leaks."""
        self._configured(refresh_token='')
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient'):
            self.client.post(self.url, {'action': 'connect', 'code': '1000.grant'})
        creds = ZohoCredentials.load()
        self.assertNotIn('1000.grant', ' '.join(
            str(getattr(creds, f.name)) for f in creds._meta.fields))

    # ── syncing ─────────────────────────────────────────────────────────────

    def test_syncing_before_connecting_is_refused(self):
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            response = self.client.post(self.url, {'action': 'sync'}, follow=True)
        client.assert_not_called()
        self.assertIn('Not connected', ' '.join(
            str(m) for m in response.context['messages']))

    def test_syncing_records_every_zoho_account(self):
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = ZOHO_ACCOUNTS
            self.client.post(self.url, {'action': 'sync'})
        self.assertEqual(ZohoAccountMap.objects.count(), 2)
        self.assertEqual(
            set(ZohoAccountMap.objects.values_list('zoho_account_name', flat=True)),
            {'Petty Cash', 'Trade Debtors'})

    def test_syncing_never_touches_the_erp_chart(self):
        """The chart is the structure finance designed; Zoho data is coded into
        it, never over it."""
        Account.objects.create(code='1000000', name='Assets', internal_type='View')
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = ZOHO_ACCOUNTS
            self.client.post(self.url, {'action': 'sync'})
        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(Account.objects.get(code='1000000').name, 'Assets')

    def test_resyncing_updates_rather_than_duplicating(self):
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = ZOHO_ACCOUNTS
            self.client.post(self.url, {'action': 'sync'})
            renamed = [dict(ZOHO_ACCOUNTS[0], account_name='Petty Cash (SAR)'),
                       ZOHO_ACCOUNTS[1]]
            client.return_value.accounts.return_value = renamed
            self.client.post(self.url, {'action': 'sync'})
        self.assertEqual(ZohoAccountMap.objects.count(), 2)
        self.assertEqual(
            ZohoAccountMap.objects.get(zoho_account_id='1001').zoho_account_name,
            'Petty Cash (SAR)')

    def test_resyncing_does_not_disturb_an_existing_mapping(self):
        """Finance's decisions outrank anything a re-sync brings back."""
        target = Account.objects.create(code='1110001', name='Cash in hand',
                                        internal_type='Regular')
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = ZOHO_ACCOUNTS
            self.client.post(self.url, {'action': 'sync'})
            row = ZohoAccountMap.objects.get(zoho_account_id='1001')
            row.account = target
            row.save()
            self.client.post(self.url, {'action': 'sync'})
        self.assertEqual(
            ZohoAccountMap.objects.get(zoho_account_id='1001').account, target)

    def test_an_empty_response_is_reported_rather_than_wiping_nothing_in(self):
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = []
            response = self.client.post(self.url, {'action': 'sync'}, follow=True)
        self.assertIn('no accounts', ' '.join(
            str(m) for m in response.context['messages']))

    def test_a_zoho_failure_is_reported_not_tracebacked(self):
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.side_effect = ZohoError('rate limit reached')
            response = self.client.post(self.url, {'action': 'sync'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('rate limit', ' '.join(
            str(m) for m in response.context['messages']))

    def test_automap_explains_itself_when_there_are_no_codes(self):
        """Silence would read as 'automap is broken'. It is not — this
        organisation has no account codes in Zoho to match on."""
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = ZOHO_ACCOUNTS
            response = self.client.post(self.url, {'action': 'sync', 'automap': 'on'},
                                        follow=True)
        self.assertIn('no account codes', ' '.join(
            str(m) for m in response.context['messages']))

    def test_a_blank_zoho_code_is_never_matched_against_a_blank_erp_code(self):
        """Both sides empty must not count as a match — that would map an
        arbitrary account to whichever ERP account happened to sort first."""
        Account.objects.create(code='', name='Odd one', internal_type='Regular')
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = ZOHO_ACCOUNTS
            self.client.post(self.url, {'action': 'sync', 'automap': 'on'})
        self.assertEqual(ZohoAccountMap.objects.mapped().count(), 0)

    def test_the_sync_timestamp_is_recorded(self):
        self._configured()
        self._as_finance()
        with mock.patch('accounting.views.ZohoClient') as client:
            client.return_value.accounts.return_value = ZOHO_ACCOUNTS
            self.client.post(self.url, {'action': 'sync'})
        self.assertIsNotNone(ZohoCredentials.load().last_synced_at)

    # ── page ────────────────────────────────────────────────────────────────

    def test_the_scope_string_is_shown_for_copying(self):
        """It has to be pasted into Zoho's console exactly."""
        self._as_finance()
        self.assertContains(self.client.get(self.url), 'ZohoBooks.accountants.READ')

    def test_every_scope_offered_is_read_only(self):
        """The token Zoho issues must have no power to write, enforced at
        Zoho's end rather than only by our own restraint."""
        self._as_finance()
        scopes = self.client.get(self.url).context['read_scopes'].split(',')
        self.assertTrue(scopes)
        for scope in scopes:
            self.assertTrue(scope.endswith('.READ'), scope)

    def test_an_unknown_action_changes_nothing(self):
        self._configured()
        self._as_finance()
        response = self.client.post(self.url, {'action': 'drop-everything'},
                                    follow=True)
        self.assertIn('Unknown action', ' '.join(
            str(m) for m in response.context['messages']))
