import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from reports.models import SalesCallReport
from .models import ContactDatabase

logger = logging.getLogger(__name__)

# Categories shared between SalesCallReport and ContactDatabase
SHARED_CATEGORIES = {
    'cctv', 'radios', 'acs', 'iot', 'iiot', 'servers',
    'network_security', 'firewall', 'cyber_security', 'windows', 'ot',
}


def _map_category(report):
    """Extract first matching category from the sales call's system_categories."""
    selected = report.get_system_categories_list()
    for cat in selected:
        if cat in SHARED_CATEGORIES:
            return cat
    # Default fallback
    return 'ot'


@receiver(post_save, sender=SalesCallReport)
def sync_contact_from_sales_call(sender, instance, created, **kwargs):
    """Auto-create or update a contact when a sales call report is saved."""
    if not created:
        # On update, just refresh last_contact date on linked contacts
        ContactDatabase.objects.filter(source_report=instance).update(
            last_contact=instance.call_date,
        )
        return

    try:
        report = instance

        # Check for existing contact with same company + email (avoid duplicates)
        existing = None
        if report.email:
            existing = ContactDatabase.objects.filter(
                organisation_name__iexact=report.company_name,
                contact_email__iexact=report.email,
            ).first()

        if not existing and report.contact_name:
            existing = ContactDatabase.objects.filter(
                organisation_name__iexact=report.company_name,
                contact_name__iexact=report.contact_name,
            ).first()

        if existing:
            # Update last_contact date and link
            existing.last_contact = report.call_date
            if not existing.source_report:
                existing.source_report = report
            if report.phone and not existing.contact_telephone:
                existing.contact_telephone = report.phone
            if report.email and not existing.contact_email:
                existing.contact_email = report.email
            existing.save()
            return

        # Build role/title string for comments
        role_parts = []
        if report.title:
            role_parts.append(report.get_title_display())
        if report.role:
            role_parts.append(report.role)
        role_str = ' - '.join(role_parts)

        ContactDatabase.objects.create(
            category=_map_category(report),
            organisation_name=report.company_name,
            contact_name=report.contact_name,
            contact_email=report.email or '',
            contact_telephone=report.phone or '',
            contact_address=report.address or '',
            notice_type='opportunity',
            status='open',
            last_contact=report.call_date,
            comments=f"Auto-created from Sales Call Report.\nRole: {role_str}" if role_str else "Auto-created from Sales Call Report.",
            created_by=report.sales_rep,
            source_report=report,
        )
    except Exception:
        logger.exception('Error syncing contact from sales call report')
