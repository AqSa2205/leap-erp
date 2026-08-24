"""Move the Administration section from `admin` to `erp_admin`.

Administration is now owned by super_admin and erp_admin. The `admin` role
keeps everything else it has — costing, procurement, pipeline, dev tracking —
but loses the two capability-gated entries that live inside Administration.

Capability grants are rows in RolePermission, so changing the registry defaults
alone would only affect environments seeded from scratch. This flips the
existing rows.
"""
from django.db import migrations

# Capabilities that belong to the Administration section.
ADMIN_SECTION_CODENAMES = [
    'timesheets.review',          # Request Timesheets
    'engineer_calendar.access',   # Engineer Calendar
    'engineer_calendar.nav',
]


def _set(apps, role_name, codenames, allowed):
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    role = Role.objects.filter(name=role_name).first()
    if role is None:
        return 0
    changed = 0
    for codename in codenames:
        row, _ = RolePermission.objects.get_or_create(
            role=role, codename=codename, defaults={'allowed': allowed})
        if row.allowed != allowed:
            row.allowed = allowed
            row.save(update_fields=['allowed'])
            changed += 1
    return changed


def forwards(apps, schema_editor):
    revoked = _set(apps, 'admin', ADMIN_SECTION_CODENAMES, False)
    granted = _set(apps, 'erp_admin', ADMIN_SECTION_CODENAMES, True)
    print(f'  admin: {revoked} revoked · erp_admin: {granted} granted')


def backwards(apps, schema_editor):
    _set(apps, 'admin', ADMIN_SECTION_CODENAMES, True)
    _set(apps, 'erp_admin', ADMIN_SECTION_CODENAMES, False)


class Migration(migrations.Migration):

    dependencies = [('accounts', '0030_seed_engineer_calendar_permissions')]

    operations = [migrations.RunPython(forwards, backwards)]
