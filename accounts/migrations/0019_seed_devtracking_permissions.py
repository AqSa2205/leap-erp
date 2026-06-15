from django.db import migrations


def seed(apps, schema_editor):
    # Ensure the developer Role row exists (0018 added the choice; this creates
    # the actual row, mirroring how finance/proposal roles were seeded). Then
    # seed permissions. seed_default_permissions is idempotent (get_or_create),
    # so it only ADDS the new devtracking rows and the developer role's rows;
    # existing rows for prior modules/roles are left untouched.
    Role = apps.get_model('accounts', 'Role')
    Role.objects.get_or_create(
        name='developer',
        defaults={'description': 'Developer — works assigned dev-tracking tasks'},
    )
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    # Reverse: drop only the devtracking rows this migration introduced, plus
    # the developer role's rows and the developer role itself.
    RolePermission = apps.get_model('accounts', 'RolePermission')
    Role = apps.get_model('accounts', 'Role')
    RolePermission.objects.filter(codename__startswith='devtracking.').delete()
    dev = Role.objects.filter(name='developer').first()
    if dev:
        RolePermission.objects.filter(role=dev).delete()
        dev.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0018_alter_role_name'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
