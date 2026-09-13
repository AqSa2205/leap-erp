"""Create the Planning and Cost Control Engineer role, with the same
permissions currently granted to Document Controller.

This copies the live RolePermission rows for document_controller rather
than reapplying the code defaults in accounts.permissions.DEFAULT_MODULE_ACCESS,
since a super admin may have adjusted Document Controller's grants by hand
since they were first seeded - the new role should start from what Document
Controller actually has today, not from the original baseline.

BOM access (export-only, unpriced data) is handled separately in the BOM
views themselves, not through the permission grid - see the accompanying
costing changes.

Idempotent: safe to rerun. Reversing removes the role only if nobody has
been assigned it yet, mirroring 0032_create_original_roles' reasoning that
deleting a role in use would orphan its users.
"""
from django.db import migrations


def create_pcc_engineer_role(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    RolePermission = apps.get_model('accounts', 'RolePermission')

    doc_controller = Role.objects.filter(name='document_controller').first()
    if doc_controller is None:
        # Document Controller doesn't exist on this database yet - nothing
        # to copy from, so there is nothing safe to do here.
        return

    pcc_role, created = Role.objects.get_or_create(
        name='pcc_engineer',
        defaults={'description': 'Planning and Cost Control Engineer'},
    )

    for perm in RolePermission.objects.filter(role=doc_controller):
        RolePermission.objects.get_or_create(
            role=pcc_role, codename=perm.codename,
            defaults={'allowed': perm.allowed},
        )

    if created:
        print('\n  Created role: pcc_engineer (permissions copied from document_controller).')


def remove_pcc_engineer_role(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    User = apps.get_model('accounts', 'User')
    role = Role.objects.filter(name='pcc_engineer').first()
    if role is None:
        return
    if User.objects.filter(role=role).exists():
        # Someone is assigned this role - removing it would orphan them.
        return
    role.delete()  # RolePermission rows cascade with it.


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0033_alter_role_name'),
    ]

    operations = [
        migrations.RunPython(create_pcc_engineer_role, remove_pcc_engineer_role),
    ]
