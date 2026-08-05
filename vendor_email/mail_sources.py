import uuid
from datetime import datetime, timezone

from django.conf import settings


class DemoMailReader:
    """Returns one canned message per call, for click-through demos.
    No network access, no credentials involved."""

    SCENARIOS = {
        'matched': dict(
            sender_email='sales@gulfcables.sa',
            sender_name='Gulf Cables & Electrical Industries',
            subject='RE: Quotation Request — Ref# CS-2091',
            body='Please find attached our best offer under Ref# CS-2091.',
            attachment_filename='GulfCables_Quote_Rev2.pdf',
        ),
        'no_reference': dict(
            sender_email='info@alfanar.com',
            sender_name='Alfanar Electrical Systems',
            subject='Your requested pricing',
            body='Hi, attached is the pricing you asked for.',
            attachment_filename='Quote.pdf',
        ),
        'unknown_reference': dict(
            sender_email='procurement@nesma-trading.com',
            sender_name='Nesma Trading Est.',
            subject='Quotation Ref# CS-1987',
            body='Kindly find our quotation attached for reference CS-1987.',
            attachment_filename='Nesma_Quotation.pdf',
        ),
    }

    def __init__(self, scenario='matched'):
        self.scenario = scenario

    def fetch_new_messages(self):
        base = self.SCENARIOS.get(self.scenario, self.SCENARIOS['matched'])
        return [{
            'message_id': f'demo-{uuid.uuid4()}',
            'received_at': datetime.now(timezone.utc),
            **base,
        }]


class GraphMailReader:
    """Reads unread messages from the one monitored mailbox via Microsoft
    Graph (Mail.Read, application permission, scoped to this mailbox only
    via an Application Access Policy). Not wired to a live mailbox yet —
    this is Step 8, kept here so the rest of the app never has to change
    when we flip it on."""

    def __init__(self, mailbox=None):
        self.mailbox = mailbox or settings.VENDOR_EMAIL_MAILBOX

    def _get_access_token(self):
        import msal
        app = msal.ConfidentialClientApplication(
            settings.MS_CLIENT_ID,
            authority=f'https://login.microsoftonline.com/{settings.MS_TENANT_ID}',
            client_credential=settings.MS_CLIENT_SECRET,
        )
        result = app.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])
        token = result.get('access_token')
        if not token:
            raise RuntimeError(result.get('error_description', 'Could not acquire Graph token'))
        return token

    def fetch_new_messages(self):
        import requests
        token = self._get_access_token()
        headers = {'Authorization': f'Bearer {token}'}
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self.mailbox}/mailFolders/Inbox/messages"
            f"?$filter=isRead eq false&$top=25&$select=id,subject,receivedDateTime,from,bodyPreview"
        )
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        out = []
        for m in resp.json().get('value', []):
            out.append({
                'message_id': m['id'],
                'received_at': m['receivedDateTime'],
                'sender_email': m.get('from', {}).get('emailAddress', {}).get('address', ''),
                'sender_name': m.get('from', {}).get('emailAddress', {}).get('name', ''),
                'subject': m.get('subject', ''),
                'body': m.get('bodyPreview', ''),
                'attachment_filename': '',  # TODO: fetch attachments separately if hasAttachments
            })
        return out


def get_reader(scenario='matched'):
    """Auto-selects: live mailbox if fully configured, demo fixtures otherwise."""
    live_ready = all([
        getattr(settings, 'MS_TENANT_ID', ''),
        getattr(settings, 'MS_CLIENT_ID', ''),
        getattr(settings, 'MS_CLIENT_SECRET', ''),
        getattr(settings, 'VENDOR_EMAIL_MAILBOX', ''),
    ])
    if live_ready:
        return GraphMailReader()
    return DemoMailReader(scenario=scenario)