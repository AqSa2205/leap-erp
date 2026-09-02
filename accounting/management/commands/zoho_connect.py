"""One-time Zoho Books connection: exchange a Self Client code for a refresh token.

The code Zoho generates is valid for minutes and can only be spent once, so
this command is meant to be run immediately after finance produces it. Once it
succeeds the refresh token is stored and lasts until somebody revokes it — this
never needs running again.

    python manage.py zoho_connect --code 1000.abc123... \
        --client-id 1000.XXX --client-secret YYY --organization-id 10234695

    python manage.py zoho_connect --check      # verify an existing connection

Client id/secret/organization id can also come from the environment
(ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_ORGANIZATION_ID), which is how
production should set them — the same way the R2 and Anthropic keys are handled.
"""
from django.core.management.base import BaseCommand, CommandError

from accounting.management.commands._console import use_utf8_console
from accounting.models import ZohoCredentials
from accounting.zoho import READ_SCOPES, ZohoClient, ZohoError


class Command(BaseCommand):
    help = 'Connect Zoho Books by exchanging a Self Client code for a refresh token.'

    def add_arguments(self, parser):
        parser.add_argument('--code', help='The Self Client grant token (expires in minutes).')
        parser.add_argument('--client-id')
        parser.add_argument('--client-secret')
        parser.add_argument('--organization-id')
        parser.add_argument('--accounts-url',
                            help='Region host, e.g. https://accounts.zoho.sa. '
                                 'Defaults to https://accounts.zoho.com.')
        parser.add_argument('--check', action='store_true',
                            help='Test the stored credentials without changing them.')
        parser.add_argument('--scopes', action='store_true',
                            help='Print the scopes to paste into the Zoho console, then exit.')

    def handle(self, *args, **options):
        use_utf8_console()   # Zoho names are Arabic; cp1252 cannot print them
        if options['scopes']:
            self.stdout.write('Paste these scopes into the Self Client "Generate Code" tab:\n')
            self.stdout.write(self.style.SUCCESS(READ_SCOPES))
            self.stdout.write('\nRead-only: the ERP can read Zoho Books but cannot change it.')
            return

        creds = ZohoCredentials.load()

        for field, key in (('client_id', 'client_id'),
                           ('client_secret', 'client_secret'),
                           ('organization_id', 'organization_id'),
                           ('accounts_url', 'accounts_url')):
            value = options.get(key)
            if value:
                setattr(creds, field, value)
        creds.save()

        if options['check']:
            return self._check(creds)

        code = options['code']
        if not code:
            raise CommandError(
                'Nothing to do. Pass --code to connect, or --check to test an '
                'existing connection. Run with --scopes to print the scope string.')

        missing = [name for name, value in (
            ('--client-id', creds.client_id),
            ('--client-secret', creds.client_secret),
            ('--organization-id', creds.organization_id)) if not value]
        if missing:
            raise CommandError(
                f'Missing {", ".join(missing)} — provide them as arguments or in the '
                f'environment (ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_ORGANIZATION_ID).')

        client = ZohoClient(creds)
        self.stdout.write(f'Exchanging the code at {creds.accounts_url} …')
        try:
            client.exchange_grant_token(code)
        except ZohoError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS('Refresh token stored — this is the last time a code is needed.'))
        self.stdout.write(f'API domain reported by Zoho: {creds.api_domain}')
        self._check(creds)

    def _check(self, creds):
        """Prove the whole chain: credentials, organisation, and data centre."""
        if not creds.is_configured:
            raise CommandError(f'Not connected: {creds.status}.')
        try:
            payload = ZohoClient(creds).organization()
        except ZohoError as exc:
            raise CommandError(f'Connection test failed: {exc}')

        orgs = payload.get('organizations') or []
        self.stdout.write(self.style.SUCCESS(f'\nConnected. {len(orgs)} organization(s) visible:'))
        for org in orgs:
            marker = '  <- configured' if str(org.get('organization_id')) == str(creds.organization_id) else ''
            self.stdout.write(
                f"   {org.get('organization_id')}  {org.get('name')}  "
                f"[{org.get('currency_code')}]{marker}")
        if not any(str(o.get('organization_id')) == str(creds.organization_id) for o in orgs):
            self.stdout.write(self.style.WARNING(
                f'\n  ! Configured organization_id {creds.organization_id} is not in that '
                f'list. Requests will fail until it matches one above.'))
