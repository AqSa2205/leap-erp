from django.db import migrations

# The original fixed sections, in order: (field name, heading label).
LEGACY_SECTIONS = [
    ('covering_letter', 'Covering Letter'),
    ('executive_summary', 'Executive Summary'),
    ('company_overview', 'Company Overview'),
    ('understanding_of_requirements', 'Understanding of Requirements'),
    ('proposed_technical_solution', 'Proposed Technical Solution'),
    ('delivery_implementation', 'Delivery & Implementation'),
    ('risk_management', 'Risk Management'),
    ('service_management', 'Service Management'),
    ('data_protection', 'Data Protection'),
    ('assumptions_constraints', 'Assumptions & Constraints'),
]


def seed_and_migrate(apps, schema_editor):
    SectionHeading = apps.get_model('proposals', 'SectionHeading')
    ProposalSection = apps.get_model('proposals', 'ProposalSection')
    TechnicalProposal = apps.get_model('proposals', 'TechnicalProposal')

    # Seed the heading library from the original fixed sections.
    for i, (_field, label) in enumerate(LEGACY_SECTIONS):
        SectionHeading.objects.get_or_create(
            name=label, defaults={'order': i})

    # Migrate each proposal's existing field content into ProposalSection rows,
    # preserving order. Only non-empty fields become sections.
    for proposal in TechnicalProposal.objects.all():
        if proposal.sections.exists():
            continue  # already migrated
        order = 0
        for field, label in LEGACY_SECTIONS:
            content = getattr(proposal, field, '') or ''
            if content.strip():
                ProposalSection.objects.create(
                    proposal=proposal, heading=label,
                    content=content, order=order)
                order += 1


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('proposals', '0005_sectionheading_proposalsection')]
    operations = [migrations.RunPython(seed_and_migrate, noop)]
