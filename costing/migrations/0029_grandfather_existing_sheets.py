from django.db import migrations


def grandfather(apps, schema_editor):
    CostingSheet = apps.get_model('costing', 'CostingSheet')
    CostingSheet.objects.update(enforce_stage_barriers=False)


def ungrandfather(apps, schema_editor):
    pass  # one-way: re-enabling barriers on old sheets is a deliberate manual act


class Migration(migrations.Migration):
    dependencies = [('costing', '0028_costingsheet_enforce_stage_barriers')]
    operations = [migrations.RunPython(grandfather, ungrandfather)]
