from django.db import migrations


def restrict(apps, schema_editor):
    # Lock the entire KPI section to super_admin. 0023 may already have seeded
    # kpis.* ON for admin/manager/dept-heads (the earlier baseline); this
    # re-baselines those rows OFF and ensures super_admin's are ON. Idempotent.
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.filter(
        codename__startswith='kpis.'
    ).exclude(role__name='super_admin').update(allowed=False)
    RolePermission.objects.filter(
        codename__startswith='kpis.', role__name='super_admin'
    ).update(allowed=True)


def noop(apps, schema_editor):
    # Reverse leaves rows as-is; re-running the seed would just re-apply the
    # current (super-admin-only) baseline anyway.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0023_seed_kpis_permissions'),
    ]
    operations = [migrations.RunPython(restrict, noop)]
