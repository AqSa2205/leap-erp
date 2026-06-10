from django.db import migrations


def reseed(apps, schema_editor):
    # The 0016 seed used an over-restrictive baseline. Correct it to the
    # match-today baseline. Safe to wipe-and-reseed because the permission
    # feature is not yet live, so there are no admin customizations to keep.
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.all().delete()
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def noop(apps, schema_editor):
    # Reverse leaves the (re-seeded) rows in place; 0016's reverse handles wipe.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0016_seed_permissions'),
    ]
    operations = [migrations.RunPython(reseed, noop)]
