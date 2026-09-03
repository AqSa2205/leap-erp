"""Seed the A.4 resource picklist.

The list finance supplied, in their order. Two things about it worth recording,
since both are deliberate rather than transcription slips:

**"Accomodation" and "Food Expense" each appeared twice**, once around the
civil trades and once around the telecom ones. A picklist cannot usefully
offer the same entry twice — you cannot tell which you picked — and nothing is
lost by de-duplicating, because a sheet can carry the same resource on two
lines with different quantities and rates. So each appears once here.

**"Accomodation" keeps finance's spelling.** A.4 is never printed, so this is
an internal label; matching the sheet people are reading from is worth more
than correcting it and having the two disagree.

Idempotent on name, so re-running adds nothing and never disturbs an entry an
administrator has since renamed or deactivated.
"""
from django.db import migrations

RESOURCES = [
    ('Project Manager', 'Nos'),
    ('Site Engineer (Civil)', 'Nos'),
    ('WPR', 'Nos'),
    ('Safety Officer', 'Nos'),
    ('Rigger 3', 'Nos'),
    ('Civil QC Inspector', 'Nos'),
    ('CoatingQC Inspector', 'Nos'),
    ('E&I QC', 'Nos'),
    ('Telecom QC', 'Nos'),
    ('Supervisor', 'Nos'),
    ('Welder', 'Nos'),
    ('Technician/ Carpenter/ Steel Fixer', 'Nos'),
    ('Coating Technician', 'Nos'),
    ('Fire Watcher', 'Nos'),
    ('E& I Technician', 'Nos'),
    ('Helper', 'Nos'),
    ('Accomodation', 'Month'),
    ('Pickup Vehicle/Coaster', 'Month'),
    ('Cover All', 'Nos'),
    ('Food Expense', 'Month'),
    ('Misc  expense', 'LOT'),
    ('Mobilization /Demobilization', 'LOT'),
    ('Aramco Approved Splicer', 'Nos'),
    ('Telecom Technician', 'Nos'),
    ('Pickup Vehicle', 'Month'),
    ('Consumables', 'LOT'),
]


def seed(apps, schema_editor):
    ResourceCatalogueItem = apps.get_model('costing', 'ResourceCatalogueItem')
    for position, (name, uom) in enumerate(RESOURCES, start=1):
        ResourceCatalogueItem.objects.get_or_create(
            name=name,
            defaults={'default_uom': uom, 'order': position, 'is_active': True},
        )


def unseed(apps, schema_editor):
    """Only removes entries nothing is using.

    A catalogue row still referenced by a costing sheet is left alone —
    reversing a migration should not reach into quoted work.
    """
    ResourceCatalogueItem = apps.get_model('costing', 'ResourceCatalogueItem')
    (ResourceCatalogueItem.objects
     .filter(name__in=[name for name, _uom in RESOURCES], lines__isnull=True)
     .delete())


class Migration(migrations.Migration):

    dependencies = [
        ('costing', '0038_resourcecatalogueitem_resourceline'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
