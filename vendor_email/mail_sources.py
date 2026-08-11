import uuid
from datetime import datetime, timezone

from django.conf import settings


class DemoMailReader:
    """Returns one canned message per call, for click-through demos.
    No network access, no credentials involved."""

    SCENARIOS = {
        'matched': dict(
            sender_email='syed.muhammad.asadullah9@gmail.com',
            sender_name='Syed Muhammad Asadullah',
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
        base = dict(self.SCENARIOS.get(self.scenario, self.SCENARIOS['matched']))
        filename = base.pop('attachment_filename', '')
        attachments = [(filename, b'%PDF-1.4 demo placeholder only')] if filename else []
        return [{
            'message_id': f'demo-{uuid.uuid4()}',
            'received_at': datetime.now(timezone.utc),
            'attachments': attachments,
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

    def _fetch_all_attachments(self, headers, message_id):
        """Return a list of (filename, bytes) for every real file attachment
        on a message. Skips inline attachments (signature logos, tracking
        pixels, etc.) — those show up as attachments too but aren't the
        vendor's actual quote documents."""
        import base64
        import requests

        url = f"https://graph.microsoft.com/v1.0/users/{self.mailbox}/messages/{message_id}/attachments"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        results = []
        for att in resp.json().get('value', []):
            if att.get('@odata.type') != '#microsoft.graph.fileAttachment':
                continue
            if att.get('isInline'):
                continue
            content_bytes = att.get('contentBytes')
            if content_bytes:
                results.append((att.get('name', 'attachment'), base64.b64decode(content_bytes)))
        return results

    def fetch_new_messages(self):
        import requests
        from .models import VendorEmailMessage

        token = self._get_access_token()
        headers = {'Authorization': f'Bearer {token}'}
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self.mailbox}/mailFolders/Inbox/messages"
            f"?$top=25&$orderby=receivedDateTime desc"
            f"&$select=id,subject,receivedDateTime,from,bodyPreview,hasAttachments"
        )
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        # Skip anything already captured — this is the slow part fixed:
        # only download attachments for messages we've never seen before.
        messages = resp.json().get('value', [])
        already_known = set(
            VendorEmailMessage.objects.filter(
                message_id__in=[m['id'] for m in messages]
            ).values_list('message_id', flat=True)
        )

        out = []
        for m in messages:
            if m['id'] in already_known:
                continue
            attachments = []
            if m.get('hasAttachments'):
                attachments = self._fetch_all_attachments(headers, m['id'])
            out.append({
                'message_id': m['id'],
                'received_at': m['receivedDateTime'],
                'sender_email': m.get('from', {}).get('emailAddress', {}).get('address', ''),
                'sender_name': m.get('from', {}).get('emailAddress', {}).get('name', ''),
                'subject': m.get('subject', ''),
                'body': m.get('bodyPreview', ''),
                'attachments': attachments,  # list of (filename, bytes)
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