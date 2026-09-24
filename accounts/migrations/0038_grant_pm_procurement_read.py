"""Open the Procurement section (read-only) to Project Manager.

Same shape as 0037_grant_po_approve: seed_default_permissions() only creates
rows that are MISSING, so on a database that has already run, changing the
DEFAULT_MODULE_ACCESS baseline in permissions.py changes nothing - the four
rows below already exist for project_manager, seeded OFF back when the
capability was declared for other roles.

Deliberately unconditional, same reasoning as 0037: the "leave rows as an
administrator set them" rule protects a deliberate admin choice, and there
has never been one here - these rows have sat at the default nobody chose.

Read-only is enforced in code (procurement/views.py test_func checks +
template button guards for is_project_manager_user), not by withholding a
capability - po.access/po.nav proved that pattern already works for PM.
"""
from django.db import migrations

GRANTS = {
    'procurement.access': ('project_manager',),
    'procurement.nav': ('project_manager',),
    'dn.access': ('project_manager',),
    'dn.nav': ('project_manager',),
}


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    for codename, role_names in GRANTS.items():
        for role in Role.objects.filter(name__in=role_names):
            RolePermission.objects.update_or_create(
                role=role, codename=codename, defaults={'allowed': True})


def ungrant(apps, schema_editor):
    """Back to where these rows were: OFF, for exactly these roles."""
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    for codename, role_names in GRANTS.items():
        role_ids = Role.objects.filter(name__in=role_names).values_list('id', flat=True)
        (RolePermission.objects
         .filter(codename=codename, role_id__in=list(role_ids))
         .update(allowed=False))


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0037_grant_po_approve'),
    ]

    operations = [
        migrations.RunPython(grant, ungrant),
    ]
