"""Sends outgoing mail through the Microsoft Graph API instead of SMTP.

Microsoft 365's Security Defaults block plain SMTP AUTH tenant-wide, which
made the standard django.core.mail SMTP backend unusable here (see the
535/5.7.139 "Authentication unsuccessful... security defaults policy" error
this project hit). Graph API calls authenticate as an Azure AD app
registration (client-credentials OAuth2 flow via MSAL) rather than as a
mailbox login, so they are unaffected by that policy.

Drop-in replacement: every existing send_mail()/EmailMultiAlternatives call
in the codebase is unchanged — only settings.EMAIL_BACKEND points here.
"""
import logging
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
                timeout=15,
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

        # If this email has a file attached (e.g. a PO PDF), convert it into
        # the format Microsoft Graph expects and add it to the message.
        # If there's no attachment, this does nothing — existing plain-text
        # emails are unaffected.
        attachments = _build_attachments(message)
        if attachments:
            graph_message['attachments'] = attachments

        return {'message': graph_message, 'saveToSentItems': 'false'}

# Converts a normal Django email attachment (filename + file bytes + type)
# into the JSON format Microsoft Graph's sendMail API expects. Graph wants
# the file's contents encoded as base64 text, embedded directly in the
# message payload — this function does that conversion.

def _build_attachments(message):
    """Converts Django EmailMessage.attachments into Graph API fileAttachment
    dicts. Supports the (filename, content, mimetype) tuple form used by
    EmailMessage.attach() — the only form this codebase currently needs."""
    import base64

    graph_attachments = []
    for attachment in getattr(message, 'attachments', []):
        if not isinstance(attachment, tuple):
            # MIMEBase attachments aren't used anywhere in this app yet;
            # skip rather than guess at their structure.
            continue
        filename, content, mimetype = attachment
        if isinstance(content, str):
            content = content.encode('utf-8')
        graph_attachments.append({
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': filename,
            'contentType': mimetype or 'application/octet-stream',
            'contentBytes': base64.b64encode(content).decode('ascii'),
        })
    return graph_attachments


def _address_only(formatted_address):
    """'Name <addr@example.com>' -> 'addr@example.com' (parseaddr also
    passes a bare 'addr@example.com' through unchanged)."""
    return parseaddr(formatted_address)[1] or formatted_address
