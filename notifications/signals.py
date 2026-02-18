import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse

from accounts.models import User, Role
from reports.models import SalesCallReport, SalesCallResponse
from projects.models import ProjectHistory

from .services import notify_users

logger = logging.getLogger(__name__)


def _get_admins():
    return User.objects.filter(role__name=Role.ADMIN, is_active=True)


def _get_region_managers(region):
    if not region:
        return User.objects.none()
    return User.objects.filter(role__name=Role.MANAGER, region=region, is_active=True)


# ─── Sales Call Report Created ────────────────────────────────

@receiver(post_save, sender=SalesCallReport)
def notify_on_sales_call_report(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        report = instance
        actor = report.sales_rep
        target_url = reverse('reports:sales_call_detail', kwargs={'pk': report.pk})

        # Recipients: admins + managers in the same region as the sales rep
        admins = set(_get_admins())
        region_managers = set(_get_region_managers(actor.region if actor else None))
        recipients = admins | region_managers

        notify_users(
            recipients=recipients,
            verb='submitted a new sales call report',
            actor=actor,
            target=report,
            target_url=target_url,
            description=f'Sales call report for {report.company_name} ({report.get_action_type_display()})',
            level='info',
            send_email=True,
        )
    except Exception:
        logger.exception('Error sending sales call report notification')


# ─── Sales Call Response Added ────────────────────────────────

@receiver(post_save, sender=SalesCallResponse)
def notify_on_sales_call_response(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        response = instance
        report = response.sales_call
        actor = response.responder
        target_url = reverse('reports:sales_call_detail', kwargs={'pk': report.pk})

        # Recipients: the original sales rep + admins + managers (excluding responder)
        recipients = set()
        if report.sales_rep:
            recipients.add(report.sales_rep)
        recipients |= set(_get_admins())
        if report.sales_rep and report.sales_rep.region:
            recipients |= set(_get_region_managers(report.sales_rep.region))

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
