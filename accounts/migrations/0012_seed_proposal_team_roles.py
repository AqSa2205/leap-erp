from django.db import migrations


def create_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Role.objects.get_or_create(
        name='proposal_head',
        defaults={'description': 'Proposal Team Head — owns the BOM / proposals workflow'},
    )
    Role.objects.get_or_create(
        name='proposal_rep',
        defaults={'description': 'Proposal Representative — BOM editor on the proposals team'},
    )


def remove_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Role.objects.filter(name__in=('proposal_head', 'proposal_rep')).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0011_add_proposal_team_roles'),
    ]
    operations = [
        migrations.RunPython(create_roles, remove_roles),
    ]
