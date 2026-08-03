from django.db import migrations

NEW_ROLES = ['project_manager', 'site_manager', 'erp_admin', 'document_controller']


def seed(apps, schema_editor):
    # Create the four authorization Role rows (0027 added the choices; this
    # creates the actual rows, mirroring 0021). Then seed permissions.
    # seed_default_permissions is idempotent (get_or_create), so it only ADDS
    # the new roles' rows; existing rows for prior roles are left untouched.
    Role = apps.get_model('accounts', 'Role')
    for name in NEW_ROLES:
        Role.objects.get_or_create(name=name)
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    # Reverse: drop the four roles' permission rows and the roles themselves.
    RolePermission = apps.get_model('accounts', 'RolePermission')
    Role = apps.get_model('accounts', 'Role')
    for name in NEW_ROLES:
        role = Role.objects.filter(name=name).first()
        if role:
            RolePermission.objects.filter(role=role).delete()
            role.delete()


class Migration(migrations.Migration):
    dependencies = [('accounts', '0027_alter_role_name')]
    operations = [migrations.RunPython(seed, unseed)]
