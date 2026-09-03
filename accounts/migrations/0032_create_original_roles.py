"""Create the three roles no migration ever created.

`admin`, `manager` and `sales_rep` are declared in Role.ROLE_CHOICES and used
throughout the codebase, but nothing in the migration history creates them.
They predate the seeding migrations — they were made by hand on the original
database and every environment since has been a copy of it.

So a genuinely fresh install comes up with 17 of the 20 roles, missing
Administrator, Manager and Sales Representative. Nobody can be assigned any of
the three, and because seed_default_permissions() iterates over the roles that
exist, they get no RolePermission rows either.

It also leaves 0005_promote_admin_to_super_admin irreversible on a fresh
database: its reverse does Role.objects.get(name='admin') and would raise
DoesNotExist.

Idempotent, so this is a no-op on the live database and on any copy of it.
Reversing does not delete the roles: users point at them, and removing a role
somebody is assigned to is a far worse outcome than leaving three rows behind.
"""
from django.db import migrations

ORIGINAL_ROLES = [
    ('admin', 'Administrator'),
    ('manager', 'Manager'),
    ('sales_rep', 'Sales Representative'),
]


def create_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    created = []
    for name, description in ORIGINAL_ROLES:
        _role, was_created = Role.objects.get_or_create(
            name=name, defaults={'description': description})
        if was_created:
            created.append(name)

    if not created:
        return

    # Only the roles that were just created lack permission rows, but seeding
    # is get_or_create per (role, capability) so it cannot disturb the ones
    # already there — including any an administrator has since changed by hand.
    #
    # The real function rather than a copy frozen here: the baseline it applies
    # is the thing being restored, and a divergent copy would be a second
    # definition of it to keep in step. 0017_reseed_permissions_match_today
    # calls it the same way.
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()
    print(f'\n  Created missing roles: {", ".join(created)} (with default permissions).')


def noop(apps, schema_editor):
    """Deliberately does not delete them — users are assigned to these."""


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0031_administration_erp_admin_only'),
    ]

    operations = [
        migrations.RunPython(create_roles, noop),
    ]
