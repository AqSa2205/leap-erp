import logging
import threading

from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.db import connections

from .models import Notification

logger = logging.getLogger(__name__)


def send_email_in_background(subject, body, to_email, html_body=None, on_success=None):
    """Send an email on a background daemon thread.

    - daemon=True so it never blocks process shutdown
    - Logs all exceptions via logger.exception (no silent swallowing)
    - Closes Django DB connections opened inside the thread to prevent
      connection leaks (each thread gets its own connection pool)
    - Uses fail_silently=False so SMTP errors are caught and logged

    Args:
        subject: email subject
        body: plain text body
        to_email: recipient email address (string)
        html_body: optional HTML alternative
        on_success: optional callable run inside the thread on successful send
    """
    if not to_email:
        return

    def _send():
        try:
            if html_body:
                msg = EmailMultiAlternatives(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email])
                msg.attach_alternative(html_body, 'text/html')
                msg.send(fail_silently=False)
            else:
                send_mail(
                    subject,
                    body,
                    settings.DEFAULT_FROM_EMAIL,
                    [to_email],
                    fail_silently=False,
                )
            if on_success:
                try:
                    on_success()
                except Exception:
                    logger.exception('on_success callback failed for email to %s', to_email)
        except Exception:
            logger.exception('Failed to send email to %s', to_email)
        finally:
            # Critical: close any DB connection this thread opened so we
            # don't leak connections from the pool. Django opens a new
            # connection per thread that must be closed manually.
            connections.close_all()

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


def create_notification(
    recipient,
    verb,
    actor=None,
    target=None,
    target_url='',
    description='',
    level='info',
    send_email=False,
):
    """Create a single notification. Optionally sends an email in a background thread."""
    kwargs = {
        'recipient': recipient,
        'actor': actor,
        'verb': verb,
        'description': description,
        'level': level,
        'target_url': target_url,
    }
    if target is not None:
        kwargs['target_content_type'] = ContentType.objects.get_for_model(target)
        kwargs['target_object_id'] = target.pk

    notification = Notification.objects.create(**kwargs)

    if send_email and recipient.email:
        actor_name = str(actor) if actor else 'System'
        subject = f'[Leap ERP] {actor_name} {verb}'
        body = description or f'{actor_name} {verb}'
        if target_url:
            body += f'\n\nView details: {target_url}'
        _send_email_async(notification, subject, body)

    return notification


def notify_users(
    recipients,
    verb,
    actor=None,
    target=None,
    target_url='',
    description='',
    level='info',
    send_email=False,
):
    """Create notifications for multiple users, auto-excluding the actor."""
    notifications = []
    for user in recipients:
        if actor and user.pk == actor.pk:
            continue
        n = create_notification(
            recipient=user,
            verb=verb,
            actor=actor,
            target=target,
            target_url=target_url,
            description=description,
            level=level,
            send_email=send_email,
        )
        notifications.append(n)
    return notifications


def _send_email_async(notification, subject, body):
    """Send notification email in a background thread, marking the
    notification as sent on success."""
    notification_pk = notification.pk
    recipient_email = notification.recipient.email

    def _mark_sent():
        # Re-fetch in the worker thread so we update via that thread's
        # connection (which is closed in the helper's finally block).
        Notification.objects.filter(pk=notification_pk).update(email_sent=True)

    send_email_in_background(
        subject=subject,
        body=body,
        to_email=recipient_email,
        on_success=_mark_sent,
    )
