"""
Live read access to one monitored mailbox via Microsoft Graph, for the
"Add Emails" live-inbox flow on Commercial Pipeline entries (see
projects/views.py: link_pipeline_email).

Same auth approach as notifications/graph_email_backend.py (Microsoft 365's
security-defaults policy blocks basic-auth IMAP, so this authenticates as
an Azure AD app registration via MSAL client-credentials, not as a mailbox
login) and the same MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET settings —
that Graph app already has tenant-wide Mail.Read application permission.

No caching, no persistence, no demo mode: every call is a live round trip,
by design, and an unconfigured/unreachable mailbox surfaces as a
GraphMailError for the view to show inline rather than crashing the page.
"""
import base64
from email.utils import parseaddr
from urllib.parse import quote

import requests
from django.conf import settings

GRAPH_SCOPE = ['https://graph.microsoft.com/.default']
GRAPH_BASE = 'https://graph.microsoft.com/v1.0'
REQUEST_TIMEOUT = 15

# message_id/attachment_id below ultimately trace back to user input (a POST
# body field, or JSON riding through the create-form draft) with no format
# validation before it gets here — every one of them is quote(value, safe='')
# before being placed in a Graph request URL's path. Without that, a crafted
# id containing '/' or '..' segments could make this server-side, app-only-
# authenticated request resolve to a completely different Graph resource
# (e.g. another mailbox's messages) than the one PIPELINE_EMAIL_MAILBOX is
# meant to restrict this feature to — a path-injection/confused-deputy risk,
# not just a cosmetic one, since Mail.Read here is an application permission
# that (absent an Exchange Application Access Policy) can reach any mailbox
# in the tenant.


class GraphMailError(Exception):
    """Raised when the live mailbox can't be reached or isn't configured."""


def _get_access_token():
    import msal

    tenant_id = settings.MS_TENANT_ID
    client_id = settings.MS_CLIENT_ID
    client_secret = settings.MS_CLIENT_SECRET
    if not (tenant_id and client_id and client_secret):
        raise GraphMailError(
            'MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET must all be set to read the live inbox.')

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f'https://login.microsoftonline.com/{tenant_id}',
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    access_token = result.get('access_token')
    if not access_token:
        error_detail = result.get('error_description', result.get('error', 'unknown error'))
        raise GraphMailError(f'Could not acquire a Microsoft Graph access token: {error_detail}')
    return access_token


def _recipients_str(addresses):
    """Graph's ccRecipients/toRecipients -> 'Name <addr>, Name <addr>'."""
    out = []
    for r in addresses or []:
        email_addr = (r.get('emailAddress') or {})
        name, addr = email_addr.get('name', ''), email_addr.get('address', '')
        out.append(f'{name} <{addr}>' if name else addr)
    return ', '.join(out)


_ATTACHMENTS_EXPAND = 'attachments($select=id,name,contentType,size,isInline)'


def list_inbox_messages(mailbox, top=50):
    """Return the most recent messages in `mailbox`'s Inbox that have at
    least one real (non-inline) attachment, newest first — this feature is
    document-first, so messages with nothing to attach are left out. Each
    entry is a plain dict: id, subject, sender_name, sender_email,
    received_at, body_preview, cc, attachments (list of {id, name,
    content_type, size}).

    Attachments come back via $expand on this same request rather than a
    separate GET per message — with up to `top` messages, a per-message
    /attachments call would be up to 50 extra sequential HTTPS round trips
    (each with its own timeout), enough to hang the page for minutes if
    Graph is slow. $expand fetches everything in one request."""
    if not mailbox:
        raise GraphMailError('PIPELINE_EMAIL_MAILBOX is not configured.')

    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/mailFolders/Inbox/messages'
    params = {
        '$top': top,
        '$orderby': 'receivedDateTime desc',
        '$select': 'id,subject,from,ccRecipients,receivedDateTime,hasAttachments,bodyPreview',
        '$expand': _ATTACHMENTS_EXPAND,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphMailError(f'Could not reach the mailbox: {exc}')

    if resp.status_code != 200:
        raise GraphMailError(f'Graph inbox listing failed ({resp.status_code}): {resp.text}')

    messages = []
    for item in resp.json().get('value', []):
        if not item.get('hasAttachments'):
            continue
        entry = _message_item_to_dict(item)
        if not entry['attachments']:
            # Only inline images (e.g. a signature logo) — nothing to attach.
            continue
        messages.append(entry)
    return messages


def _message_item_to_dict(item):
    """One Graph message item (from the list endpoint or a single-message
    fetch, both requested with $expand=attachments) -> the plain dict shape
    used throughout this module."""
    sender = (item.get('from') or {}).get('emailAddress') or {}
    sender_name, sender_email = sender.get('name', ''), sender.get('address', '')
    if not sender_name:
        sender_name, sender_email = parseaddr(sender_email)

    return {
        'id': item.get('id'),
        'subject': item.get('subject') or '(no subject)',
        'sender_name': sender_name,
        'sender_email': sender_email,
        'cc': _recipients_str(item.get('ccRecipients')),
        'received_at': item.get('receivedDateTime'),
        'body_preview': item.get('bodyPreview') or '',
        'attachments': _real_attachments(item.get('attachments')),
    }


def get_message_summary(mailbox, message_id):
    """One message's metadata + attachment list (no bytes) — same shape as
    one entry from list_inbox_messages(), for redisplaying a previously
    picked email (e.g. on the pipeline-entry create form) without a project
    to attach it to yet."""
    if not mailbox:
        raise GraphMailError('PIPELINE_EMAIL_MAILBOX is not configured.')

    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}'
    params = {
        '$select': 'id,subject,from,ccRecipients,receivedDateTime,bodyPreview',
        '$expand': _ATTACHMENTS_EXPAND,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphMailError(f'Could not reach the mailbox: {exc}')

    if resp.status_code != 200:
        raise GraphMailError(f'Graph message fetch failed ({resp.status_code}): {resp.text}')

    return _message_item_to_dict(resp.json())


def _real_attachments(raw_attachments):
    """Graph's $expand=attachments(...) embeds every attachment straight
    on the message, including inline content (signature logos etc.) —
    this keeps only the real ones and reshapes them to the dict shape
    this module uses."""
    return [
        {
            'id': a.get('id'),
            'name': a.get('name') or 'attachment',
            'content_type': a.get('contentType') or 'application/octet-stream',
            'size': a.get('size') or 0,
        }
        for a in raw_attachments or []
        if not a.get('isInline')
    ]


def fetch_attachment_bytes(mailbox, message_id, attachment_id):
    """One specific attachment's bytes + metadata, for opening/previewing a
    document from the live inbox without attaching the whole email yet.
    Returns (filename, content_type, bytes)."""
    if not mailbox:
        raise GraphMailError('PIPELINE_EMAIL_MAILBOX is not configured.')

    access_token = _get_access_token()
    url = (f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}'
           f'/attachments/{quote(attachment_id, safe="")}')
    try:
        resp = requests.get(
            url, headers={'Authorization': f'Bearer {access_token}'}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphMailError(f'Could not fetch that attachment: {exc}')

    if resp.status_code != 200:
        raise GraphMailError(f'Graph attachment fetch failed ({resp.status_code}): {resp.text}')

    data = resp.json()
    content_bytes = data.get('contentBytes')
    if content_bytes is None:
        raise GraphMailError('That attachment has no downloadable content.')

    return (
        data.get('name') or 'attachment',
        data.get('contentType') or 'application/octet-stream',
        base64.b64decode(content_bytes),
    )


def fetch_raw_message_bytes(mailbox, message_id):
    """Return one message's raw MIME (.eml) bytes via Graph's documented
    $value export, ready to hand to email_parsing.parse_eml_file()."""
    if not mailbox:
        raise GraphMailError('PIPELINE_EMAIL_MAILBOX is not configured.')

    access_token = _get_access_token()
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}/$value'
    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GraphMailError(f'Could not fetch that email: {exc}')

    if resp.status_code != 200:
        raise GraphMailError(f'Graph message fetch failed ({resp.status_code}): {resp.text}')

    return resp.content
