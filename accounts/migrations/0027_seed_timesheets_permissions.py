from django.db import migrations


def seed_permissions(apps, schema_editor):
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0026_enable_devtracking_for_ai_head'),
    ]

    operations = [
        migrations.RunPython(seed_permissions, migrations.RunPython.noop),
    ]