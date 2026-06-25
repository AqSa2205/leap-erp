from django.db import migrations

DEVTRACKING_CAPS = [
    'devtracking.access', 'devtracking.nav',
    'devtracking.admin', 'devtracking.mywork',
]


def enable_devtracking_for_ai_head(apps, schema_editor):
    """Ensure the AI Head role has the entire Dev Tracking feature enabled,
    regardless of when the role/grants were seeded."""
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    role = Role.objects.filter(name='ai_head').first()
    if not role:
        return
    for codename in DEVTRACKING_CAPS:
        RolePermission.objects.update_or_create(
            role=role, codename=codename, defaults={'allowed': True})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('accounts', '0025_seed_kpis_activity_permission')]
    operations = [migrations.RunPython(enable_devtracking_for_ai_head, noop)]
