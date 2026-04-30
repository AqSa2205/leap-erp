from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("costing", "0024_clientremarkpair"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="costingsheet",
            name="additional_contacts",
            field=models.ManyToManyField(
                blank=True,
                related_name="additional_contact_costing_sheets",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
