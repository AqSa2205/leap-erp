from django.db import migrations


def create_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Role.objects.get_or_create(
        name='finance_head',
        defaults={'description': 'Finance Head — owns the finance module'},
    )
    Role.objects.get_or_create(
        name='finance_manager',
        defaults={'description': 'Finance Manager — manages finance team operations'},
    )
    Role.objects.get_or_create(
        name='finance_rep',
        defaults={'description': 'Finance Representative — day-to-day finance work'},
    )


def remove_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Role.objects.filter(
        name__in=('finance_head', 'finance_manager', 'finance_rep')
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0013_add_finance_team_roles'),
    ]
    operations = [
        migrations.RunPython(create_roles, remove_roles),
    ]
