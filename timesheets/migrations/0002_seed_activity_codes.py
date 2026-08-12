from django.db import migrations

DEFAULTS = [
    ('COS_0001', 'Regular Maintenance activities'),
    ('COS_0002', 'Emergency call'),
    ('COS_0003', 'Offshore trip'),
    ('COS_0004', 'Commissioning activities'),
    ('COS_0005', 'Site Visit trip'),
    ('COS_0006', 'Project execution activities'),
    ('COS_0007', 'Site Survey'),
    ('COS_0008', 'Attending meeting'),
    ('COS_0009', 'Office'),
    ('COS_0010', 'PM support'),
    ('COS_0011', 'Procurement'),
    ('COS_0012', 'Logistic Support'),
    ('COS_0013', 'Administration'),
    ('COS_0014', 'Finance'),
    ('COS_0015', 'Accounts'),
    ('COS_0016', 'Sales activities'),
    ('COS_0017', 'Document Controller'),
    ('COS_0018', 'Engineers Mobilization & De Mobilization Plan'),
    ('COS_0019', 'JobEx meeting'),
    ('COS_0020', 'Bidding meeting'),
    ('COS_0021', 'Sales Meeting'),
    ('COS_0022', 'Commercial Proposal'),
    ('COS_0023', 'Vendor Meeting'),
    ('COS_0024', 'Kick Off meeting'),
]


def seed(apps, schema_editor):
    ActivityCode = apps.get_model('timesheets', 'ActivityCode')
    for code, label in DEFAULTS:
        ActivityCode.objects.get_or_create(code=code, defaults={'label': label})


def unseed(apps, schema_editor):
    ActivityCode = apps.get_model('timesheets', 'ActivityCode')
    ActivityCode.objects.filter(code__in=[d[0] for d in DEFAULTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('timesheets', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]