import django.utils.timezone
from django.db import migrations, models
from django.db.models import F


def backfill_updated_at(apps, schema_editor):
    # Existing templates have no edit history, so start them at their
    # creation time rather than the moment this migration ran.
    TermsTemplate = apps.get_model('costing', 'TermsTemplate')
    TermsTemplate.objects.update(updated_at=F('created_at'))


class Migration(migrations.Migration):

    dependencies = [
        ('costing', '0045_costinglineitem_added_at_costinglineitem_added_by_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='termstemplate',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_updated_at, migrations.RunPython.noop),
    ]
