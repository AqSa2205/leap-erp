from django.db import migrations

# Sensible default headings for the prequalification PDF library. Admin can
# rename / reorder / deactivate these and upload the matching PDF into each.
DEFAULT_HEADINGS = [
    'Company Profile',
    'Commercial Registration (CR)',
    'VAT Certificate',
    'Chamber of Commerce Certificate',
    'GOSI Certificate',
    'Zakat & Tax Certificate',
    'Saudization (Nitaqat) Certificate',
    'Municipality License',
    'ISO 9001 — Quality',
    'ISO 14001 — Environment',
    'ISO 45001 — Health & Safety',
    'ISO 27001 — Information Security',
    'Achilles Certificate',
    'Aramco Registration',
    'SEC Registration',
    'Bank Letter / Financial Standing',
    'Audited Financial Statements',
    'Organization Chart',
    'Key Personnel CVs',
    'Project Experience / Track Record',
    'Product Catalogues',
    'Manufacturer Authorizations',
    'HSE Policy',
    'Quality Policy',
    'List of Major Clients',
]


def seed(apps, schema_editor):
    PrequalLibraryItem = apps.get_model('proposals', 'PrequalLibraryItem')
    if PrequalLibraryItem.objects.exists():
        return
    for i, heading in enumerate(DEFAULT_HEADINGS):
        PrequalLibraryItem.objects.get_or_create(
            heading=heading, defaults={'order': i})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('proposals', '0007_prequallibraryitem_prequalsubmission')]
    operations = [migrations.RunPython(seed, noop)]
