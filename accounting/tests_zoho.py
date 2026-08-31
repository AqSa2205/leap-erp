"""Zoho Books client tests — no network, every response is faked.

The client is the piece that will fail at the worst moment (mid-sync, in
production, an hour after anyone last looked at it), so the cases worth pinning
down are the awkward ones: a token that expires part-way, a 401 on the third
page, a 200 response that is actually an error.
"""
import json
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from accounting.models import ZohoCredentials
from accounting.zoho import ZohoAuthError, ZohoClient, ZohoError


class FakeResponse:
    def __init__(self, payload, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is _INVALID:
            raise ValueError('not json')
        return self._payload


_INVALID = object()


class FakeSession:
    """Records calls and returns queued responses."""

    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.get_calls.append({'url': url, 'headers': headers or {}, 'params': params or {}})
        if not self.get_responses:
            raise AssertionError(f'unexpected GET {url}')
        return self.get_responses.pop(0)

    def post(self, url, data=None, timeout=None):
        self.post_calls.append({'url': url, 'data': data or {}})
        if not self.post_responses:
            raise AssertionError(f'unexpected POST {url}')
        return self.post_responses.pop(0)


def make_credentials(**overrides):
    creds = ZohoCredentials.load()
    creds.client_id = overrides.get('client_id', '1000.CLIENT')
    creds.client_secret = overrides.get('client_secret', 'SECRET')
    creds.organization_id = overrides.get('organization_id', '10234695')
    creds.refresh_token = overrides.get('refresh_token', 'REFRESH')
    creds.access_token = overrides.get('access_token', 'ACCESS')
    creds.access_token_expires_at = overrides.get(
        'access_token_expires_at', timezone.now() + timedelta(hours=1))
    creds.save()
    return creds


class CredentialsTests(TestCase):

    def test_is_configured_requires_all_four(self):
        creds = make_credentials()
        self.assertTrue(creds.is_configured)
        creds.refresh_token = ''
        self.assertFalse(creds.is_configured)

    def test_status_names_the_missing_piece(self):
        creds = ZohoCredentials.load()
        self.assertEqual(creds.status, 'No client credentials')
        creds.client_id, creds.client_secret = 'a', 'b'
        self.assertEqual(creds.status, 'No organization ID')
        creds.organization_id = '1'
        self.assertEqual(creds.status, 'Not connected — needs a Self Client code')
        creds.refresh_token = 'r'
        self.assertEqual(creds.status, 'Connected')

    def test_environment_overrides_stored_values(self):
        """Production sets these on Render, so they never reach a form or git."""
        import os
        make_credentials(client_id='OLD')
        os.environ['ZOHO_CLIENT_ID'] = 'FROM-ENV'
        try:
            self.assertEqual(ZohoCredentials.load().client_id, 'FROM-ENV')
        finally:
            del os.environ['ZOHO_CLIENT_ID']

    def test_stays_a_singleton(self):
        """load() is the accessor; a second row cannot be created by accident."""
        from django.db import IntegrityError, transaction
        first = make_credentials()
        self.assertEqual(first.pk, 1)
        self.assertEqual(ZohoCredentials.load().pk, 1)
        # save() pins pk=1, so a direct create collides rather than quietly
        # producing a second set of credentials nothing would ever read.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ZohoCredentials.objects.create(client_id='second')
        self.assertEqual(ZohoCredentials.objects.count(), 1)


class TokenTests(TestCase):

    def test_valid_token_is_reused_without_a_refresh(self):
        creds = make_credentials()
        session = FakeSession(get_responses=[FakeResponse({'code': 0, 'organizations': []})])
        ZohoClient(creds, session=session).organization()
        self.assertEqual(session.post_calls, [])          # no token call
        self.assertEqual(session.get_calls[0]['headers']['Authorization'], 'Bearer ACCESS')

    def test_expired_token_is_refreshed(self):
        creds = make_credentials(access_token_expires_at=timezone.now() - timedelta(minutes=1))
        session = FakeSession(
            get_responses=[FakeResponse({'code': 0, 'organizations': []})],
            post_responses=[FakeResponse({'access_token': 'NEW', 'expires_in': 3600})])
        ZohoClient(creds, session=session).organization()
        self.assertEqual(len(session.post_calls), 1)
        self.assertEqual(session.post_calls[0]['data']['grant_type'], 'refresh_token')
        self.assertEqual(session.get_calls[0]['headers']['Authorization'], 'Bearer NEW')

    def test_token_about_to_expire_is_refreshed_early(self):
        """A token valid now but stale by the time it lands is a 401 mid-sync."""
        creds = make_credentials(access_token_expires_at=timezone.now() + timedelta(seconds=30))
        session = FakeSession(
            get_responses=[FakeResponse({'code': 0, 'organizations': []})],
            post_responses=[FakeResponse({'access_token': 'NEW', 'expires_in': 3600})])
        ZohoClient(creds, session=session).organization()
        self.assertEqual(len(session.post_calls), 1)

    def test_refresh_failure_is_an_auth_error(self):
        creds = make_credentials(access_token_expires_at=timezone.now() - timedelta(minutes=1))
        session = FakeSession(post_responses=[FakeResponse({'error': 'invalid_code'})])
        with self.assertRaises(ZohoAuthError):
            ZohoClient(creds, session=session).organization()


class GrantExchangeTests(TestCase):

    def test_exchange_stores_refresh_token_and_api_domain(self):
        creds = make_credentials(refresh_token='', access_token='')
        session = FakeSession(post_responses=[FakeResponse({
            'access_token': 'A', 'refresh_token': 'R',
            'api_domain': 'https://www.zohoapis.sa', 'expires_in': 3600})])
        ZohoClient(creds, session=session).exchange_grant_token('1000.CODE')
        creds.refresh_from_db()
        self.assertEqual(creds.refresh_token, 'R')
        # The data centre comes from Zoho rather than being guessed.
        self.assertEqual(creds.api_domain, 'https://www.zohoapis.sa')
        self.assertTrue(creds.is_configured)

    def test_expired_code_explains_itself(self):
        creds = make_credentials(refresh_token='')
        session = FakeSession(post_responses=[FakeResponse({'error': 'invalid_code'})])
        with self.assertRaises(ZohoAuthError) as ctx:
            ZohoClient(creds, session=session).exchange_grant_token('stale')
        self.assertIn('expire', str(ctx.exception).lower())


class RequestTests(TestCase):

    def test_organization_id_is_always_sent(self):
        """Zoho declares it required; omitting it fails obscurely."""
        creds = make_credentials()
        session = FakeSession(get_responses=[FakeResponse({'code': 0, 'chartofaccounts': []})])
        ZohoClient(creds, session=session).get('/chartofaccounts')
        self.assertEqual(session.get_calls[0]['params']['organization_id'], '10234695')

    def test_url_uses_the_stored_api_domain(self):
        creds = make_credentials()
        creds.api_domain = 'https://www.zohoapis.eu'
        creds.save()
        session = FakeSession(get_responses=[FakeResponse({'code': 0})])
        ZohoClient(creds, session=session).get('/journals')
        self.assertEqual(session.get_calls[0]['url'],
                         'https://www.zohoapis.eu/books/v3/journals')

    def test_401_refreshes_once_and_retries(self):
        creds = make_credentials()
        session = FakeSession(
            get_responses=[FakeResponse({'message': 'expired'}, status_code=401),
                           FakeResponse({'code': 0, 'journals': []})],
            post_responses=[FakeResponse({'access_token': 'NEW2', 'expires_in': 3600})])
        ZohoClient(creds, session=session).get('/journals')
        self.assertEqual(len(session.get_calls), 2)
        self.assertEqual(session.get_calls[1]['headers']['Authorization'], 'Bearer NEW2')

    def test_401_twice_gives_up_instead_of_looping(self):
        creds = make_credentials()
        session = FakeSession(
            get_responses=[FakeResponse({'message': 'nope'}, status_code=401),
                           FakeResponse({'message': 'nope'}, status_code=401)],
            post_responses=[FakeResponse({'access_token': 'N', 'expires_in': 3600})])
        with self.assertRaises(ZohoAuthError):
            ZohoClient(creds, session=session).get('/journals')
        self.assertEqual(len(session.get_calls), 2)

    def test_error_code_in_a_200_body_is_still_an_error(self):
        """Zoho signals some failures with HTTP 200 and a non-zero code."""
        creds = make_credentials()
        session = FakeSession(get_responses=[
            FakeResponse({'code': 1002, 'message': 'Invalid value for organization_id'})])
        with self.assertRaises(ZohoError) as ctx:
            ZohoClient(creds, session=session).get('/journals')
        self.assertIn('organization_id', str(ctx.exception))

    def test_rate_limit_says_it_is_resumable(self):
        creds = make_credentials()
        session = FakeSession(get_responses=[FakeResponse({'message': 'too many'}, status_code=429)])
        with self.assertRaises(ZohoError) as ctx:
            ZohoClient(creds, session=session).get('/journals')
        self.assertIn('rate limit', str(ctx.exception).lower())

    def test_non_json_body_does_not_raise_a_bare_valueerror(self):
        creds = make_credentials()
        session = FakeSession(get_responses=[
            FakeResponse(_INVALID, status_code=502, text='<html>gateway</html>')])
        with self.assertRaises(ZohoError) as ctx:
            ZohoClient(creds, session=session).get('/journals')
        self.assertIn('502', str(ctx.exception))


class PaginationTests(TestCase):

    def test_follows_has_more_page_across_pages(self):
        creds = make_credentials()
        session = FakeSession(get_responses=[
            FakeResponse({'code': 0, 'journals': [{'journal_id': '1'}, {'journal_id': '2'}],
                          'page_context': {'has_more_page': True}}),
            FakeResponse({'code': 0, 'journals': [{'journal_id': '3'}],
                          'page_context': {'has_more_page': False}}),
        ])
        client = ZohoClient(creds, session=session, sleep=lambda s: None)
        got = [j['journal_id'] for j in client.journals()]
        self.assertEqual(got, ['1', '2', '3'])
        self.assertEqual(session.get_calls[0]['params']['page'], 1)
        self.assertEqual(session.get_calls[1]['params']['page'], 2)

    def test_stops_on_has_more_page_false_not_on_a_short_page(self):
        """A short page mid-run is normal; only has_more_page ends the loop."""
        creds = make_credentials()
        session = FakeSession(get_responses=[
            FakeResponse({'code': 0, 'contacts': [{'contact_id': 'a'}],
                          'page_context': {'has_more_page': True}}),
            FakeResponse({'code': 0, 'contacts': [{'contact_id': 'b'}],
                          'page_context': {'has_more_page': False}}),
        ])
        client = ZohoClient(creds, session=session, sleep=lambda s: None)
        self.assertEqual(len(list(client.contacts())), 2)

    def test_missing_page_context_ends_cleanly(self):
        creds = make_credentials()
        session = FakeSession(get_responses=[FakeResponse({'code': 0, 'bills': [{'bill_id': 'x'}]})])
        client = ZohoClient(creds, session=session, sleep=lambda s: None)
        self.assertEqual(len(list(client.bills())), 1)

    def test_filters_are_passed_through(self):
        creds = make_credentials()
        session = FakeSession(get_responses=[FakeResponse({'code': 0, 'journals': []})])
        client = ZohoClient(creds, session=session, sleep=lambda s: None)
        list(client.journals(date_start='2026-01-01'))
        self.assertEqual(session.get_calls[0]['params']['date_start'], '2026-01-01')


class ConnectCommandTests(TestCase):

    def test_scopes_are_read_only(self):
        from io import StringIO
        out = StringIO()
        call_command('zoho_connect', '--scopes', stdout=out)
        printed = out.getvalue()
        self.assertIn('ZohoBooks.accountants.READ', printed)
        for write in ('.CREATE', '.UPDATE', '.DELETE', '.ALL'):
            self.assertNotIn(write, printed)

    def test_check_without_credentials_fails_cleanly(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('zoho_connect', '--check')
        self.assertIn('No client credentials', str(ctx.exception))

    def test_connect_without_a_code_explains_what_to_do(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('zoho_connect')
        self.assertIn('--code', str(ctx.exception))

    def test_code_without_client_credentials_names_what_is_missing(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('zoho_connect', '--code', '1000.abc')
        self.assertIn('--client-id', str(ctx.exception))


class AccountMappingTests(TestCase):
    """Zoho accounts are coded INTO the finance team's chart, not over it."""

    def setUp(self):
        from accounting.models import Account
        root = Account.objects.create(code='4000000', name='Cost of Sale',
                                      internal_type='View')
        self.local = Account.objects.create(code='4100006', name='Local Procurement',
                                            internal_type='Regular', parent=root)
        self.intl = Account.objects.create(code='4100007', name='International Procurement',
                                           internal_type='Regular', parent=root)
        self.heading = root

    def _row(self, **kw):
        from accounting.models import ZohoAccountMap
        defaults = dict(zoho_account_id='Z1', zoho_account_code='5001',
                        zoho_account_name='Purchases', zoho_account_type='expense')
        defaults.update(kw)
        return ZohoAccountMap.objects.create(**defaults)

    def test_new_row_is_unmapped_not_an_error(self):
        row = self._row()
        self.assertFalse(row.is_mapped)
        self.assertEqual(row.state, 'needs mapping')

    def test_many_zoho_accounts_can_share_one_erp_account(self):
        """Collapsing several Zoho accounts into one is usually the whole point."""
        self._row(zoho_account_id='Z1', zoho_account_name='Purchases - Local',
                  account=self.local)
        self._row(zoho_account_id='Z2', zoho_account_name='Materials',
                  account=self.local)
        self.assertEqual(self.local.zoho_mappings.count(), 2)

    def test_resolve_prefers_the_zoho_id_over_the_code(self):
        """Codes can be edited in Zoho; ids cannot."""
        from accounting.models import resolve_zoho_account
        self._row(zoho_account_id='Z1', zoho_account_code='5001', account=self.local)
        self._row(zoho_account_id='Z2', zoho_account_code='5001', account=self.intl)
        self.assertEqual(resolve_zoho_account(zoho_account_id='Z2'), self.intl)

    def test_resolve_falls_back_to_the_code(self):
        from accounting.models import resolve_zoho_account
        self._row(zoho_account_code='5001', account=self.local)
        self.assertEqual(resolve_zoho_account(zoho_account_code='5001'), self.local)

    def test_resolve_returns_none_when_unmapped(self):
        from accounting.models import resolve_zoho_account
        self._row()
        self.assertIsNone(resolve_zoho_account(zoho_account_id='Z1'))
        self.assertIsNone(resolve_zoho_account(zoho_account_id='NOPE'))

    def test_ignored_rows_leave_the_worklist_without_pretending_to_be_mapped(self):
        from accounting.models import ZohoAccountMap, resolve_zoho_account
        self._row(is_ignored=True)
        self.assertEqual(ZohoAccountMap.objects.unmapped().count(), 0)
        self.assertIsNone(resolve_zoho_account(zoho_account_id='Z1'))

    def test_erp_account_cannot_be_deleted_while_mapped(self):
        """PROTECT: losing the target would silently orphan coded history."""
        from django.db.models.deletion import ProtectedError
        self._row(account=self.local)
        with self.assertRaises(ProtectedError):
            self.local.delete()


class SyncAccountsCommandTests(TestCase):

    def test_refuses_without_credentials_instead_of_tracebacking(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('sync_zoho_accounts')
        self.assertIn('not configured', str(ctx.exception).lower())


class Utf8ConsoleTests(TestCase):
    """Reporting must not be able to fail the work it reports on.

    Zoho holds this company's names in Arabic. On a Windows console — cp1252 —
    printing one raises UnicodeEncodeError, and because the print happens
    *after* the sync it reports on, a completed run reads as a crash. This is
    the guard that stops that.
    """

    ARABIC = 'شركة لييب نتوركس أرابيا'

    def _cp1252_stream(self):
        import io
        return io.TextIOWrapper(io.BytesIO(), encoding='cp1252')

    def _patched(self, stream):
        from unittest import mock
        import sys
        return mock.patch.multiple(sys, stdout=stream, stderr=stream)

    def test_arabic_kills_a_cp1252_stream(self):
        """Guards the guard. If this ever stops raising, every other test in
        this class passes for the wrong reason."""
        stream = self._cp1252_stream()
        with self.assertRaises(UnicodeEncodeError):
            stream.write(self.ARABIC)
            stream.flush()

    def test_the_stream_is_switched_to_utf8(self):
        from accounting.management.commands._console import use_utf8_console
        stream = self._cp1252_stream()
        with self._patched(stream):
            use_utf8_console()
        self.assertEqual(stream.encoding, 'utf-8')

    def test_arabic_survives_afterwards(self):
        from accounting.management.commands._console import use_utf8_console
        stream = self._cp1252_stream()
        with self._patched(stream):
            use_utf8_console()
            stream.write(self.ARABIC)
            stream.flush()

    def test_a_stream_that_cannot_be_reconfigured_is_left_alone(self):
        """Test runners and pipes replace stdout with objects that have no
        reconfigure(). Losing the run to a failed cosmetic fix would be worse
        than the problem it fixes."""
        from accounting.management.commands._console import use_utf8_console

        class Bare:
            def write(self, text):
                return len(text)

            def flush(self):
                pass

        with self._patched(Bare()):
            use_utf8_console()          # must not raise
