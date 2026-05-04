import logging

from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse

from accounts.models import User, Role
from reports.models import SalesCallResponse
from projects.models import ProjectHistory

from .services import notify_users

logger = logging.getLogger(__name__)


def _get_admins(region=None):
    """Admin recipients for a notification.

    Super admins always receive (cross-region oversight). Regional admins
    receive only when their region matches the event's region — passing
    region=None means *no* regional admins are included.
    """
    q = Q(role__name=Role.SUPER_ADMIN)
    if region is not None:
        q |= Q(role__name=Role.ADMIN, region=region)
    return User.objects.filter(is_active=True).filter(q).distinct()


def _get_region_managers(region):
    if not region:
        return User.objects.none()
    return User.objects.filter(role__name=Role.MANAGER, region=region, is_active=True)


# ─── Sales Call Response Added ────────────────────────────────
# Note: creating a sales call report itself no longer triggers a
# notification (in-app or email). Only management replies notify.

@receiver(post_save, sender=SalesCallResponse)
def notify_on_sales_call_response(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        response = instance
        report = response.sales_call
        actor = response.responder
        target_url = reverse('reports:sales_call_detail', kwargs={'pk': report.pk})

        # Recipients: original sales rep + super admins + region admins +
        # region managers — all scoped to the sales rep's region so
        # admins/managers don't receive activity from regions they don't
        # cover.
        rep_region = report.sales_rep.region if report.sales_rep else None
        recipients = set()
        if report.sales_rep:
            recipients.add(report.sales_rep)
        recipients |= set(_get_admins(region=rep_region))
        recipients |= set(_get_region_managers(rep_region))

        notify_users(
            recipients=recipients,
            verb='responded to a sales call report',
            actor=actor,
            target=report,
            target_url=target_url,
            description=f'Response on report for {report.company_name}: {response.message[:100]}',
            level='info',
            send_email=True,
        )
    except Exception:
        logger.exception('Error sending sales call response notification')


# ─── Project Status Changed ──────────────────────────────────

@receiver(post_save, sender=ProjectHistory)
def notify_on_project_status_change(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        history = instance
        project = history.project
        actor = history.changed_by
        target_url = reverse('projects:detail', kwargs={'pk': project.pk})

        old_name = str(history.old_status) if history.old_status else 'None'
        new_name = str(history.new_status) if history.new_status else 'None'

        # Recipients: project owner + region managers
        recipients = set()
        if project.owner:
            recipients.add(project.owner)
        recipients |= set(_get_region_managers(project.region))

        notify_users(
            recipients=recipients,
            verb=f'changed project status from {old_name} to {new_name}',
            actor=actor,
            target=project,
            target_url=target_url,
            description=f'Project "{project.project_name}" status changed from {old_name} to {new_name}',
            level='warning',
            send_email=True,
        )
    except Exception:
        logger.exception('Error sending project status change notification')
