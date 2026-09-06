"""
Talks to microsoft graph for the costing revision client email feature:
sending the initial email (with the pdf attached), then reading back the complete
reply thread by its conversation id.

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
GRAPH_BASE= 'https://graph.microsoft.com/v1.0'
REQUEST_TIMEOUT= 15


class GraphThreadError(Exception):
    """Raised when Graph can't send or read back the revision email thread."""


def _get_access_token():
    import msal
    tenant_id = settings.MS_TENANT_ID
    client_id = settings.MS_CLIENT_ID
    client_secret = settings.MS_CLIENT_SECRET

    if not (tenant_id and client_id and client_secret):
        raise GraphThreadError(
            'MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET must all be set to use Microsoft Graph.')

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f'https://login.microsoftonline.com/{tenant_id}',
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    access_token = result.get('access_token')

    if not access_token:
        error_detail = result.get('error_description', result.get('error', 'unknown error'))
        raise GraphThreadError(f'Could not acquire a Microsoft Graph access token: {error_detail}')
    return access_token


def send_revision_email(mailbox, to, cc, subject, body_text, attachment_bytes, attachment_filename):
    """Send `body_text` to `to` with the revision file attached, from
    `mailbox`. Returns (message_id, conversation_id) so the caller can
    save a RevisionEmailThread that reply-sync can find later.

    Creates a draft first (so Graph hands back an id/conversationId
    immediately), then sends that draft — NOT the /sendMail shortcut,
    which is fire-and-forget and returns nothing usable for tracking."""

    access_token = _get_access_token()

    headers= {
        'Authorization' : f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }

    draft_payload = {
        'subject' : subject,
        'body' : {'contentType': 'Text', 'content': body_text},
        'toRecipients':[{'emailAddress':{'address': addr}} for addr in to],
        'attachments': [{
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': attachment_filename,
            'contentType': 'application/octet-stream',
            'contentBytes': base64.b64encode(attachment_bytes).decode('ascii'),
        }],
    }
    if cc:
        draft_payload['ccRecipients'] = [{'emailAddress': {'address': addr}} for addr in cc]

    create_url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages'
    try:
        resp = requests.post(create_url, headers=headers, json=draft_payload, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphThreadError(f'Could not create the draft email: {exc}')
    if resp.status_code != 201:
        raise GraphThreadError(f'Graph draft creation failed ({resp.status_code}): {resp.text}')

    draft = resp.json()
    message_id = draft['id']
    conversation_id = draft['conversationId']

    send_url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}/send'
    try:
        send_resp = requests.post(send_url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphThreadError(f'Could not send the drafted email: {exc}')
    if send_resp.status_code != 202:
        raise GraphThreadError(f'Graph send failed ({send_resp.status_code}): {send_resp.text}')

    return message_id, conversation_id


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
    where a line break used to be — this converts block-level breaks to
    real newlines first, then strips tags and unescapes entities
    (&nbsp;, &amp;, ...). Not a full HTML renderer, just enough for a
    readable plain-text reading pane."""
    if not html_content:
        return ''
    text = _BLOCK_BREAK_RE.sub('\n', html_content)
    text = strip_tags(text)
    text = _html_entities.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    text = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _list_message_attachments(mailbox, message_id, headers):
    """Metadata only (no bytes) for one message's real attachments —
    inline content (signature logos etc.) is left out."""
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}/attachments'
    params = {'$select': 'id,name,contentType,size,isInline'}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    return [
        {
            'id': a.get('id'),
            'name': a.get('name') or 'attachment',
            'content_type': a.get('contentType') or 'application/octet-stream',
            'size': a.get('size') or 0,
        }
        for a in resp.json().get('value', [])
        if not a.get('isInline')
    ]


def fetch_attachment_bytes(mailbox, message_id, attachment_id):
    """One specific attachment's bytes + metadata, for the download-proxy
    view — never persisted locally. Returns (filename, content_type, bytes)."""
    access_token = _get_access_token()
    url = (f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}'
           f'/attachments/{quote(attachment_id, safe="")}')
    try:
        resp = requests.get(
            url, headers={'Authorization': f'Bearer {access_token}'}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphThreadError(f'Could not fetch that attachment: {exc}')
    if resp.status_code != 200:
        raise GraphThreadError(f'Graph attachment fetch failed ({resp.status_code}): {resp.text}')

    data = resp.json()
    content_bytes = data.get('contentBytes')
    if content_bytes is None:
        raise GraphThreadError('That attachment has no downloadable content.')
    return (
        data.get('name') or 'attachment',
        data.get('contentType') or 'application/octet-stream',
        base64.b64decode(content_bytes),
    )


def list_recent_messages(mailbox, top=50):
    """The most recent messages sitting in `mailbox`'s Inbox, regardless of
    conversation — used by the manual 'Attach a reply' fallback for when a
    client's reply didn't thread automatically (e.g. they composed a fresh
    email instead of hitting Reply). Plain dicts: id, subject, sender_name,
    sender_email, received_at, body_preview."""
    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/mailFolders/Inbox/messages'
    params = {
        '$top': top,
        '$orderby': 'receivedDateTime desc',
        '$select': 'id,subject,from,receivedDateTime,bodyPreview',
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphThreadError(f'Could not reach the mailbox: {exc}')
    if resp.status_code != 200:
        raise GraphThreadError(f'Graph inbox listing failed ({resp.status_code}): {resp.text}')

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
        })
    return messages


def get_message_detail(mailbox, message_id):
    """One specific message's full detail (not just the inbox preview),
    for after a user has picked it from list_recent_messages() to attach.
    Same dict shape as one entry from list_thread_messages(), plus
    conversation_id (needed to start a RevisionEmailThread from a message
    that was composed and sent outside the ERP — see link_revision_email
    in costing/views.py)."""
    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}'
    params = {'$select': 'id,conversationId,subject,from,toRecipients,ccRecipients,sentDateTime,body,hasAttachments'}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphThreadError(f'Could not fetch that message: {exc}')
    if resp.status_code != 200:
        raise GraphThreadError(f'Graph message fetch failed ({resp.status_code}): {resp.text}')

    item = resp.json()
    sender = (item.get('from') or {}).get('emailAddress') or {}
    sender_name, sender_email = sender.get('name', ''), sender.get('address', '')
    if not sender_name:
        sender_name, sender_email = parseaddr(sender_email)
    body = item.get('body') or {}
    attachments = (
        _list_message_attachments(mailbox, message_id, headers)
        if item.get('hasAttachments') else []
    )
    return {
        'id': item.get('id'),
        'conversation_id': item.get('conversationId'),
        'subject': item.get('subject') or '',
        'sender_name': sender_name,
        'sender_email': sender_email,
        'to': _recipients_str(item.get('toRecipients')),
        'cc': _recipients_str(item.get('ccRecipients')),
        'sent_at': item.get('sentDateTime'),
        'body_html': body.get('content') or '',
        'has_attachments': bool(item.get('hasAttachments')),
        'attachments': attachments,
    }


def list_recent_sent_messages(mailbox, top=50):
    """The most recent messages in `mailbox`'s Sent Items — half of the
    unified 'link a sent/received email' picker (see
    browse_link_revision_email in costing/views.py, which merges this with
    list_recent_messages()'s Inbox listing into one Outlook-style list).
    Plain dicts: id, subject, to, sent_at, body_preview."""
    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/mailFolders/SentItems/messages'
    params = {
        '$top': top,
        '$orderby': 'sentDateTime desc',
        '$select': 'id,subject,toRecipients,sentDateTime,bodyPreview',
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphThreadError(f'Could not reach the mailbox: {exc}')
    if resp.status_code != 200:
        raise GraphThreadError(f'Graph sent-items listing failed ({resp.status_code}): {resp.text}')

    return [
        {
            'id': item.get('id'),
            'subject': item.get('subject') or '(no subject)',
            'to': _recipients_str(item.get('toRecipients')),
            'sent_at': item.get('sentDateTime'),
            'body_preview': item.get('bodyPreview') or '',
        }
        for item in resp.json().get('value', [])
    ]
