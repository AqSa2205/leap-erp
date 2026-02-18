import logging
import threading

from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.conf import settings

from .models import Notification

logger = logging.getLogger(__name__)


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
    """Send email in a background thread (non-blocking, no Celery needed)."""
    def _send():
        try:
            send_mail(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                [notification.recipient.email],
                fail_silently=True,
            )
            notification.email_sent = True
            notification.save(update_fields=['email_sent'])
        except Exception:
            logger.exception('Failed to send notification email to %s', notification.recipient.email)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
