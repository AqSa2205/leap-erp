"""
Live read access to one employee's own mailbox via Microsoft Graph, for
linking a client email to a Technical Proposal (see proposals/views.py:
link_proposal_email). Read-only - Mail.Read only, no sending, no writing -
the same Azure AD app registration already used by projects/graph_mail.py
and costing/graph_thread.py, which already has tenant-wide Mail.Read
application permission, so no new Azure AD setup is needed for this feature.

Self-contained rather than importing projects/graph_mail.py, matching the
same reasoning already documented on ProposalMailbox: keeping this app
independent of another app's still-evolving branch.
"""
import base64
import html as _html_entities
import re
from email.utils import parseaddr
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils.html import strip_tags

GRAPH_SCOPE = ['https://graph.microsoft.com/.default']
GRAPH_BASE = 'https://graph.microsoft.com/v1.0'
REQUEST_TIMEOUT = 15

_ATTACHMENTS_EXPAND = 'attachments($select=id,name,contentType,size,isInline)'


class GraphMailError(Exception):
    """Raised when the mailbox can't be reached or a message can't be fetched."""


_msal_app = None  # module-level so MSAL's own token cache survives across calls


def _get_access_token():
    global _msal_app
    import msal

    tenant_id = settings.MS_TENANT_ID
    client_id = settings.MS_CLIENT_ID
    client_secret = settings.MS_CLIENT_SECRET
    if not (tenant_id and client_id and client_secret):
        raise GraphMailError(
            'MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET must all be set to read a linked mailbox.')

    if _msal_app is None:
        _msal_app = msal.ConfidentialClientApplication(
            client_id,
            authority=f'https://login.microsoftonline.com/{tenant_id}',
            client_credential=client_secret,
        )
    result = _msal_app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    access_token = result.get('access_token')
    if not access_token:
        error_detail = result.get('error_description', result.get('error', 'unknown error'))
        raise GraphMailError(f'Could not acquire a Microsoft Graph access token: {error_detail}')
    return access_token


def _recipients_str(addresses):
    """Graph's toRecipients/ccRecipients -> 'Name <addr>, Name <addr>'."""
    out = []
    for r in addresses or []:
        email_addr = (r.get('emailAddress') or {})
        name, addr = email_addr.get('name', ''), email_addr.get('address', '')
        out.append(f'{name} <{addr}>' if name else addr)
    return ', '.join(out)


_BLOCK_BREAK_RE = re.compile(r'(?i)<\s*(br\s*/?|/p|/div|/li|/tr|/h[1-6])\s*>')


def html_to_text(html_content):
    """Best-effort HTML->plain-text for a message body. strip_tags() alone
    collapses an entire paragraph-formatted email onto one unreadable
    run-on line, since it removes tags without leaving anything behind
    where a line break used to be - this converts block-level breaks to
    real newlines first, then strips tags and unescapes entities
    (&nbsp;, &amp;, ...). Not a full HTML renderer, just enough for a
    readable plain-text reading pane. (Same fix already proven on the
    costing-revision-email feature.)"""
    if not html_content:
        return ''
    text = _BLOCK_BREAK_RE.sub('\n', html_content)
    text = strip_tags(text)
    text = _html_entities.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    text = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _real_attachments(raw_attachments):
    """Graph's $expand=attachments(...) embeds every attachment straight on
    the message, including inline content (signature logos etc.) - keep
    only the real ones."""
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


def list_recent_messages(mailbox, top=50):
    """The most recent messages in `mailbox`'s Inbox, newest first - every
    message is included regardless of whether it has attachments (a
    client's plain-text reply is still valid evidence to link), each
    carrying its real (non-inline) attachments if any. Plain dicts: id,
    subject, sender_name, sender_email, received_at, body_preview,
    attachments.

    Attachments come back via $expand on this same request rather than a
    separate GET per message - a per-message /attachments call for up to
    `top` messages would be that many extra sequential HTTPS round trips
    (same reasoning as projects/graph_mail.py's identical approach)."""
    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/mailFolders/Inbox/messages'
    params = {
        '$top': top,
        '$orderby': 'receivedDateTime desc',
        '$select': 'id,subject,from,receivedDateTime,hasAttachments,bodyPreview',
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
        sender = (item.get('from') or {}).get('emailAddress') or {}
        sender_name, sender_email = sender.get('name', ''), sender.get('address', '')
        if not sender_name:
            sender_name, sender_email = parseaddr(sender_email)
        messages.append({
            'id': item.get('id'),
            'subject': item.get('subject') or '(no subject)',
            'sender_name': sender_name,
            'sender_email': sender_email,
            'received_at': item.get('receivedDateTime'),
            'body_preview': item.get('bodyPreview') or '',
            'attachments': _real_attachments(item.get('attachments')),
        })
    return messages


def get_message_detail(mailbox, message_id):
    """One specific message's full detail (not just the inbox preview), for
    after a user has picked it from list_recent_messages() to link."""
    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}'
    params = {
        '$select': 'id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments',
        '$expand': _ATTACHMENTS_EXPAND,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphMailError(f'Could not fetch that email: {exc}')
    if resp.status_code != 200:
        raise GraphMailError(f'Graph message fetch failed ({resp.status_code}): {resp.text}')

    item = resp.json()
    sender = (item.get('from') or {}).get('emailAddress') or {}
    sender_name, sender_email = sender.get('name', ''), sender.get('address', '')
    if not sender_name:
        sender_name, sender_email = parseaddr(sender_email)
    body = item.get('body') or {}
    return {
        'id': item.get('id'),
        'subject': item.get('subject') or '',
        'sender_name': sender_name,
        'sender_email': sender_email,
        'to': _recipients_str(item.get('toRecipients')),
        'cc': _recipients_str(item.get('ccRecipients')),
        'sent_at': item.get('receivedDateTime'),
        'body_html': body.get('content') or '',
        'has_attachments': bool(item.get('hasAttachments')),
        'attachments': _real_attachments(item.get('attachments')),
    }


def fetch_attachment_bytes(mailbox, message_id, attachment_id):
    """One specific attachment's bytes + metadata, for the download-proxy
    view - never persisted locally. Returns (filename, content_type, bytes)."""
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
