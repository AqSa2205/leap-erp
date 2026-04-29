import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("costing", "0022_add_vendor_quote"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientRemarkTemplate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                (
                    "remark",
                    models.TextField(help_text="The client's remark / question."),
                ),
                ("answer", models.TextField(help_text="Leap's response.")),
                (
                    "text_color",
                    models.CharField(
                        default="#000000",
                        help_text="Hex color (e.g. #C41E3A) applied to both cells in the PDF row.",
                        max_length=7,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="client_remark_templates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="costingsheet",
            name="selected_client_remarks",
            field=models.ManyToManyField(
                blank=True,
                related_name="costing_sheets",
                to="costing.clientremarktemplate",
            ),
        ),
    ]
