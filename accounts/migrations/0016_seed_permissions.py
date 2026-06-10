from django.db import migrations


def seed(apps, schema_editor):
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    # RolePermission is brand-new as of 0015, so reversing this seed back to an
    # empty table is the correct inverse — there are no pre-seed rows to keep.
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0015_permissionchangelog_rolepermission'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
