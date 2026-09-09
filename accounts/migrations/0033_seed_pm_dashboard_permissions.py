from django.db import migrations


def seed_permissions(apps, schema_editor):
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0032_create_original_roles'),
    ]

    operations = [
        migrations.RunPython(seed_permissions, migrations.RunPython.noop),
    ]
