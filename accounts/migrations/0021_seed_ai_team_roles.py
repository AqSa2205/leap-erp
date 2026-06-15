from django.db import migrations


def seed(apps, schema_editor):
    # Create the four AI-team Role rows (0020 added the choices; this creates the
    # actual rows, mirroring 0019). Then seed permissions. seed_default_permissions
    # is idempotent (get_or_create), so it only ADDS the new roles' rows; existing
    # rows for prior roles/modules are left untouched.
    Role = apps.get_model('accounts', 'Role')
    for name in ['ai_head', 'ai_intern', 'ai_engineer', 'ai_junior_engineer']:
        Role.objects.get_or_create(name=name)
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    # Reverse: drop the four AI-team roles' permission rows and the roles.
    RolePermission = apps.get_model('accounts', 'RolePermission')
    Role = apps.get_model('accounts', 'Role')
    for name in ['ai_head', 'ai_intern', 'ai_engineer', 'ai_junior_engineer']:
        role = Role.objects.filter(name=name).first()
        if role:
            RolePermission.objects.filter(role=role).delete()
            role.delete()


class Migration(migrations.Migration):
    dependencies = [('accounts', '0020_alter_role_name')]
    operations = [migrations.RunPython(seed, unseed)]
