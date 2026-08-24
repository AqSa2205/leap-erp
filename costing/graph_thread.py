"""
Talks to microsoft graph for the costing revision client email feature:
sending the initial email (with the pdf attached), then reading back the complete
reply thread by its conversation id.

"""
import base64
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


def list_thread_messages(mailbox, conversation_id):
    """Every message in `conversation_id`, oldest first, as plain dicts:
    id, subject, sender_name, sender_email, to, cc, sent_at, body_html,
    has_attachments, attachments (list of {id, name, content_type, size})."""
    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages'
    params = {
        '$filter': f"conversationId eq '{conversation_id}'",
        '$select': 'id,subject,from,toRecipients,ccRecipients,sentDateTime,body,hasAttachments',
        '$orderby': 'sentDateTime asc',
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GraphThreadError(f'Could not reach the mailbox: {exc}')
    if resp.status_code != 200:
        raise GraphThreadError(f'Graph thread lookup failed ({resp.status_code}): {resp.text}')

    messages = []
    for item in resp.json().get('value', []):
        sender = (item.get('from') or {}).get('emailAddress') or {}
        sender_name, sender_email = sender.get('name', ''), sender.get('address', '')
        if not sender_name:
            sender_name, sender_email = parseaddr(sender_email)
        body = item.get('body') or {}
        attachments = (
            _list_message_attachments(mailbox, item['id'], {'Authorization': f'Bearer {access_token}'})
            if item.get('hasAttachments') else []
        )
        messages.append({
            'id': item.get('id'),
            'subject': item.get('subject') or '',
            'sender_name': sender_name,
            'sender_email': sender_email,
            'to': _recipients_str(item.get('toRecipients')),
            'cc': _recipients_str(item.get('ccRecipients')),
            'sent_at': item.get('sentDateTime'),
            'body_html': body.get('content') or '',
            'has_attachments': bool(item.get('hasAttachments')),
            'attachments': attachments,
        })
    return messages


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


def sync_thread(thread):
    """Pull every message in `thread`'s conversation from Graph, save any
    we haven't seen yet as RevisionEmailMessage rows, and update the
    thread's status/last_synced_at. Safe to call repeatedly — messages are
    matched (and skipped if already stored) by graph_message_id, which is
    a unique column. Returns how many new messages were saved."""
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    from .models import RevisionEmailMessage

    messages = list_thread_messages(thread.mailbox, thread.graph_conversation_id)
    existing_ids = set(
        RevisionEmailMessage.objects.filter(thread=thread).values_list('graph_message_id', flat=True)
    )

    new_count = 0
    for msg in messages:
        if msg['id'] in existing_ids:
            continue
        direction = 'out' if msg['sender_email'].lower() == thread.mailbox.lower() else 'in'
        RevisionEmailMessage.objects.create(
            thread=thread,
            graph_message_id=msg['id'],
            direction=direction,
            sender_name=msg['sender_name'],
            sender_email=msg['sender_email'],
            to_recipients=msg['to'],
            cc_recipients=msg['cc'],
            subject=msg['subject'],
            body_html=msg['body_html'],
            body_text=strip_tags(msg['body_html']),
            sent_at=parse_datetime(msg['sent_at']) if msg['sent_at'] else None,
            has_attachments=msg['has_attachments'],
            attachment_meta=msg['attachments'] or None,
        )
        new_count += 1

    if new_count and RevisionEmailMessage.objects.filter(thread=thread, direction='in').exists():
        thread.status = 'replied'
    thread.last_synced_at = timezone.now()
    thread.save(update_fields=['status', 'last_synced_at'])
    return new_count


def list_recent_messages(mailbox, top=25):
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
    Same dict shape as one entry from list_thread_messages()."""
    access_token = _get_access_token()
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'{GRAPH_BASE}/users/{quote(mailbox, safe="")}/messages/{quote(message_id, safe="")}'
    params = {'$select': 'id,subject,from,toRecipients,ccRecipients,sentDateTime,body,hasAttachments'}
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
