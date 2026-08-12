"""Sends outgoing mail through the Microsoft Graph API instead of SMTP.

Microsoft 365's Security Defaults block plain SMTP AUTH tenant-wide, which
made the standard django.core.mail SMTP backend unusable here (see the
535/5.7.139 "Authentication unsuccessful... security defaults policy" error
this project hit). Graph API calls authenticate as an Azure AD app
registration (client-credentials OAuth2 flow via MSAL) rather than as a
mailbox login, so they are unaffected by that policy.

Drop-in replacement: every existing send_mail()/EmailMultiAlternatives call
in the codebase is unchanged — only settings.EMAIL_BACKEND points here.
File attachments on EmailMessage are mapped to Graph fileAttachment objects.
"""
import base64
import logging
from email.mime.base import MIMEBase
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

GRAPH_SCOPE = ['https://graph.microsoft.com/.default']
TOKEN_URL_TEMPLATE = 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'
SEND_MAIL_URL_TEMPLATE = 'https://graph.microsoft.com/v1.0/users/{sender}/sendMail'


class MicrosoftGraphEmailBackend(BaseEmailBackend):
    """Django email backend that delivers via Microsoft Graph's sendMail
    endpoint for a single fixed mailbox (settings.MS_GRAPH_SENDER)."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        access_token = self._get_access_token()
        if access_token is None:
            return 0

        sent_count = 0
        for message in email_messages:
            if self._send_one(message, access_token):
                sent_count += 1
        return sent_count

    def _get_access_token(self):
        import msal

        tenant_id = settings.MS_TENANT_ID
        client_id = settings.MS_CLIENT_ID
        client_secret = settings.MS_CLIENT_SECRET
        if not (tenant_id and client_id and client_secret):
            if not self.fail_silently:
                raise RuntimeError(
                    'MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET must all be set to send email via Microsoft Graph.')
            logger.error('Microsoft Graph email: missing tenant/client credentials.')
            return None

        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f'https://login.microsoftonline.com/{tenant_id}',
            client_credential=client_secret,
        )
        result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        access_token = result.get('access_token')
        if not access_token:
            error_detail = result.get('error_description', result.get('error', 'unknown error'))
            if not self.fail_silently:
                raise RuntimeError(f'Could not acquire a Microsoft Graph access token: {error_detail}')
            logger.error('Microsoft Graph email: token acquisition failed: %s', error_detail)
            return None
        return access_token

    def _send_one(self, message, access_token):
        try:
            payload = self._build_payload(message)
            sender = settings.MS_GRAPH_SENDER
            resp = requests.post(
                SEND_MAIL_URL_TEMPLATE.format(sender=sender),
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=60,
            )
            if resp.status_code not in (200, 202):
                raise RuntimeError(f'Graph sendMail failed ({resp.status_code}): {resp.text}')
            return True
        except Exception:
            logger.exception('Failed to send email via Microsoft Graph')
            if not self.fail_silently:
                raise
            return False

    def _build_payload(self, message):
        html_body = None
        for content, mimetype in getattr(message, 'alternatives', []):
            if mimetype == 'text/html':
                html_body = content
                break

        if html_body is not None:
            content_type, content = 'HTML', html_body
        else:
            content_type, content = 'Text', message.body

        graph_message = {
            'subject': message.subject,
            'body': {'contentType': content_type, 'content': content},
            'toRecipients': [{'emailAddress': {'address': _address_only(addr)}} for addr in message.to],
        }
        if message.cc:
            graph_message['ccRecipients'] = [
                {'emailAddress': {'address': _address_only(addr)}} for addr in message.cc]
        if message.bcc:
            graph_message['bccRecipients'] = [
                {'emailAddress': {'address': _address_only(addr)}} for addr in message.bcc]
        reply_to = getattr(message, 'reply_to', None) or []
        if reply_to:
            graph_message['replyTo'] = [
                {'emailAddress': {'address': _address_only(addr)}} for addr in reply_to]

        attachments = _graph_attachments(message)
        if attachments:
            graph_message['attachments'] = attachments

        return {'message': graph_message, 'saveToSentItems': 'false'}


def _graph_attachments(message):
    """Map Django EmailMessage.attachments to Graph fileAttachment dicts."""
    result = []
    for item in getattr(message, 'attachments', []) or []:
        if isinstance(item, MIMEBase):
            payload = item.get_payload(decode=True) or b''
            filename = item.get_filename() or 'attachment'
            content_type = item.get_content_type() or 'application/octet-stream'
        else:
            # Django stores attach() as (filename, content, mimetype)
            filename, content, content_type = item[0], item[1], item[2] if len(item) > 2 else 'application/octet-stream'
            if isinstance(content, str):
                content = content.encode('utf-8')
            payload = content or b''
            content_type = content_type or 'application/octet-stream'
        result.append({
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': filename,
            'contentType': content_type,
            'contentBytes': base64.b64encode(payload).decode('ascii'),
        })
    return result


def _address_only(formatted_address):
    """'Name <addr@example.com>' -> 'addr@example.com' (parseaddr also
    passes a bare 'addr@example.com' through unchanged)."""
    return parseaddr(formatted_address)[1] or formatted_address
