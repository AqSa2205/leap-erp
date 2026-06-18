from django.db import migrations


def seed(apps, schema_editor):
    # The kpis module adds three capabilities (kpis.access, kpis.nav,
    # kpis.manage). seed_default_permissions is idempotent (get_or_create), so
    # this only ADDS the missing kpis rows for every role at their baseline;
    # prior rows are left exactly as the admin set them.
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.filter(codename__startswith='kpis.').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0022_silo_ai_roles'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
