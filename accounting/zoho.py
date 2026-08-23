"""Zoho Books API client — read-only mirror of the statutory books.

Zoho Books remains the system of record. This client only ever reads: the
credentials are requested with `.READ` scopes, and nothing here issues a
POST/PUT/DELETE against Zoho. If the ERP ever needs to push data back, that is
a deliberate separate decision requiring new scopes, not something that should
become possible by accident.

Auth is OAuth 2.0 using Zoho's *Self Client* flow, which suits a backend with
no browser: a grant token is generated once by hand in the Zoho developer
console, exchanged here for a refresh token, and the refresh token then lasts
until someone revokes it. Access tokens live an hour and are renewed on demand.

Every request needs `organization_id` as a query parameter — it is declared
`required: true` in Zoho's own OpenAPI specs, and omitting it fails in ways
that are not obviously about the organisation.
"""
import logging
import time
from datetime import timedelta

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# Zoho hosts customers in several regions. The accounts host is where tokens
# are minted; the API host is returned as `api_domain` in the token response,
# so it is stored rather than guessed.
DEFAULT_ACCOUNTS_URL = 'https://accounts.zoho.com'
DEFAULT_API_DOMAIN = 'https://www.zohoapis.com'
API_PATH = '/books/v3'

# Renew a little before expiry: a token that is valid when we check but stale
# by the time it reaches Zoho produces a confusing 401 mid-sync.
EXPIRY_SKEW_SECONDS = 120

READ_SCOPES = ','.join([
    'ZohoBooks.settings.READ',
    'ZohoBooks.contacts.READ',
    'ZohoBooks.invoices.READ',
    'ZohoBooks.bills.READ',
    'ZohoBooks.accountants.READ',   # chart of accounts + journals
])


class ZohoError(RuntimeError):
    """Any failure talking to Zoho, raised with something a human can act on."""


class ZohoAuthError(ZohoError):
    """Credentials are missing, rejected or revoked — needs a person, not a retry."""


class ZohoClient:
    """Thin read-only wrapper over the Zoho Books v3 API.

    Construct from the stored credentials with `ZohoClient.from_settings()`.
    """

    def __init__(self, credentials, session=None, sleep=time.sleep):
        self.credentials = credentials
        self.session = session or requests.Session()
        # Injectable so tests exercise the retry path without actually waiting.
        self._sleep = sleep

    @classmethod
    def from_settings(cls, **kwargs):
        from accounting.models import ZohoCredentials
        creds = ZohoCredentials.load()
        if not creds.is_configured:
            raise ZohoAuthError(
                'Zoho is not configured. Add the client ID, client secret, '
                'organization ID and refresh token in Accounting settings.')
        return cls(creds, **kwargs)

    # ── auth ─────────────────────────────────────────────────────────────
    def exchange_grant_token(self, code):
        """Trade a one-time Self Client code for a refresh token.

        Run once, at setup. The code is valid for minutes and single-use, so a
        failure here almost always means it expired or was already spent —
        generating a fresh one is the fix, not retrying this call.
        """
        creds = self.credentials
        response = self.session.post(
            f'{creds.accounts_url}/oauth/v2/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'code': code,
            },
            timeout=30,
        )
        payload = self._json(response)
        if 'refresh_token' not in payload:
            raise ZohoAuthError(
                f'Zoho did not return a refresh token: {payload}. '
                'Self Client codes expire within minutes and can only be used '
                'once — generate a new one and try again.')
        creds.refresh_token = payload['refresh_token']
        creds.access_token = payload.get('access_token', '')
        creds.api_domain = payload.get('api_domain') or DEFAULT_API_DOMAIN
        creds.access_token_expires_at = timezone.now() + timedelta(
            seconds=int(payload.get('expires_in', 3600)))
        creds.save(update_fields=['refresh_token', 'access_token', 'api_domain',
                                  'access_token_expires_at', 'updated_at'])
        return creds

    def refresh_access_token(self):
        creds = self.credentials
        response = self.session.post(
            f'{creds.accounts_url}/oauth/v2/token',
            data={
                'grant_type': 'refresh_token',
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'refresh_token': creds.refresh_token,
            },
            timeout=30,
        )
        payload = self._json(response)
        if 'access_token' not in payload:
            raise ZohoAuthError(
                f'Could not refresh the Zoho access token: {payload}. '
                'If the refresh token was revoked, a new Self Client code is needed.')
        creds.access_token = payload['access_token']
        creds.access_token_expires_at = timezone.now() + timedelta(
            seconds=int(payload.get('expires_in', 3600)))
        if payload.get('api_domain'):
            creds.api_domain = payload['api_domain']
        creds.save(update_fields=['access_token', 'access_token_expires_at',
                                  'api_domain', 'updated_at'])
        return creds.access_token

    def _valid_access_token(self):
        creds = self.credentials
        expires_at = creds.access_token_expires_at
        if (creds.access_token and expires_at
                and expires_at - timedelta(seconds=EXPIRY_SKEW_SECONDS) > timezone.now()):
            return creds.access_token
        return self.refresh_access_token()

    # ── requests ─────────────────────────────────────────────────────────
    def get(self, path, params=None, _retry_auth=True):
        """GET one Zoho endpoint. `path` is relative, e.g. '/chartofaccounts'."""
        creds = self.credentials
        params = dict(params or {})
        params['organization_id'] = creds.organization_id

        url = f'{creds.api_domain}{API_PATH}{path}'
        response = self.session.get(
            url,
            headers={'Authorization': f'Bearer {self._valid_access_token()}'},
            params=params,
            timeout=60,
        )

        # A 401 mid-run usually means the token was revoked or expired early
        # rather than that the credentials are wrong, so force one refresh and
        # retry before giving up on the whole sync.
        if response.status_code == 401 and _retry_auth:
            logger.info('Zoho returned 401; refreshing the access token and retrying once.')
            self.refresh_access_token()
            return self.get(path, params=params, _retry_auth=False)

        if response.status_code == 429:
            raise ZohoError(
                'Zoho rate limit reached. Wait for the window to reset and re-run; '
                'the sync is resumable, so nothing is lost.')
        return self._json(response)

    def paginate(self, path, key, params=None, page_size=200):
        """Yield every record from a list endpoint, following Zoho's paging.

        Zoho returns `page_context.has_more_page`; trusting the record count
        instead would stop early whenever a page came back short.
        """
        page = 1
        while True:
            payload = self.get(path, params={**(params or {}),
                                             'page': page, 'per_page': page_size})
            records = payload.get(key) or []
            for record in records:
                yield record
            context = payload.get('page_context') or {}
            if not context.get('has_more_page'):
                return
            page += 1
            # Zoho throttles per minute; a short pause keeps a large first sync
            # from tripping the limit halfway through.
            self._sleep(0.2)

    # ── endpoints we actually read ───────────────────────────────────────
    def accounts(self):
        return self.paginate('/chartofaccounts', 'chartofaccounts')

    def contacts(self):
        return self.paginate('/contacts', 'contacts')

    def journals(self, **filters):
        return self.paginate('/journals', 'journals', params=filters)

    def journal(self, journal_id):
        return self.get(f'/journals/{journal_id}').get('journal') or {}

    def invoices(self, **filters):
        return self.paginate('/invoices', 'invoices', params=filters)

    def bills(self, **filters):
        return self.paginate('/bills', 'bills', params=filters)

    def organization(self):
        """Used by the connection check — the cheapest call that proves the
        credentials, the organisation id and the data centre are all right."""
        return self.get('/organizations')

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _json(response):
        try:
            payload = response.json()
        except ValueError:
            raise ZohoError(
                f'Zoho returned {response.status_code} with a non-JSON body: '
                f'{response.text[:200]!r}')
        # Zoho signals failure both by HTTP status and by a non-zero `code` in
        # a 200 body, so checking the status alone lets errors through.
        if response.status_code >= 400 or (payload.get('code') not in (None, 0)):
            message = payload.get('message') or response.text[:200]
            error = ZohoAuthError if response.status_code in (401, 403) else ZohoError
            raise error(f'Zoho API error {response.status_code}: {message}')
        return payload
