"""Open PO approval to Project Manager, without taking it from anyone.

Two different problems, both caused by the same thing: `seed_default_permissions()`
only creates rows that are MISSING, so on a database that has been running,
changing a baseline in `permissions.py` changes nothing.

1. `po.approve` was declared and never read, so every role's row for it sits at
   OFF. The moment can_user_approve_stage() starts reading it, OFF means
   refused - deploying the wiring alone would take approval away from Admin and
   the procurement manager and stop every purchase order in flight at its
   current stage. Turning it on for them is what keeps the policy the same.

2. `project_manager` already has rows for `po.access` and `po.nav`, set OFF.
   Without turning those on, the grid would say they may approve while the
   Purchase Orders section stayed invisible to them - a permission that cannot
   be exercised. There is no shell on production, so what a migration does not
   do cannot be done afterwards.

Deliberately unconditional: the usual rule is to leave rows as an
administrator set them, and that rule has nothing to respect here - no code
has ever read `po.approve`, and Project Manager's PO rows are at the default
nobody chose.
"""
from django.db import migrations

# codename -> the roles it is turned ON for.
#
# po.approve for the three that could already sign a stage before it became a
# capability - admin (PM, COO), procurement manager (SCM), super admin (all) -
# plus project_manager, which is the change being made.
GRANTS = {
    'po.approve': ('super_admin', 'admin', 'procurement_mgr', 'project_manager'),
    'po.access': ('project_manager',),
    'po.nav': ('project_manager',),
}


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    for codename, role_names in GRANTS.items():
        for role in Role.objects.filter(name__in=role_names):
            RolePermission.objects.update_or_create(
                role=role, codename=codename, defaults={'allowed': True})


def ungrant(apps, schema_editor):
    """Back to where these rows were: OFF, for exactly these roles.

    Narrower than "set the codename off everywhere" on purpose - po.access is
    held by most roles, and reversing this migration must not take Purchase
    Orders away from them.
    """
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')
    for codename, role_names in GRANTS.items():
        role_ids = Role.objects.filter(name__in=role_names).values_list('id', flat=True)
        (RolePermission.objects
         .filter(codename=codename, role_id__in=list(role_ids))
         .update(allowed=False))


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0036_unique_user_email'),
    ]

    operations = [
        migrations.RunPython(grant, ungrant),
    ]
