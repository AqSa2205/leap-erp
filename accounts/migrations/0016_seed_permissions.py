from django.db import migrations


def seed(apps, schema_editor):
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0015_permissionchangelog_rolepermission'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
