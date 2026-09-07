"""Grant PCC Engineer access to the Costing module.

PCC Engineer needs to reach Costing to use its unpriced BOM view/export
(see the accompanying costing/views.py changes: _user_can_see_pricing,
_user_can_view_sheet, and costing_scoped_queryset all now also recognize
is_pcc_engineer_user). Without costing.access/costing.nav granted here,
the module is unreachable regardless of what those functions allow once
inside it.

This role's RolePermission rows already exist (created by
0034_create_pcc_engineer_role, copied from document_controller, which
does not have costing access) - so this migration updates those two rows
to allowed=True rather than creating new ones.

Idempotent: safe to rerun. Reversing restores both to False, matching
what 0034 originally set them to.
"""
from django.db import migrations


def grant_costing_access(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    role = Role.objects.filter(name='pcc_engineer').first()
    if role is None:
        return
    RolePermission.objects.filter(
        role=role, codename__in=['costing.access', 'costing.nav'],
    ).update(allowed=True)


def revoke_costing_access(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    role = Role.objects.filter(name='pcc_engineer').first()
    if role is None:
        return
    RolePermission.objects.filter(
        role=role, codename__in=['costing.access', 'costing.nav'],
    ).update(allowed=False)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0034_create_pcc_engineer_role'),
    ]

    operations = [
        migrations.RunPython(grant_costing_access, revoke_costing_access),
    ]
