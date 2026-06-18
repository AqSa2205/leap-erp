from django.db import migrations


def seed(apps, schema_editor):
    # Adds the kpis.activity rows for every role at baseline (super_admin ON,
    # rest OFF). Idempotent (get_or_create) — only the missing rows are created.
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.filter(codename='kpis.activity').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0024_restrict_kpis_to_super_admin'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
