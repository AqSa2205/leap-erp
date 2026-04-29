import django.db.models.deletion
from django.db import migrations, models


def copy_single_pairs_forward(apps, schema_editor):
    """If 0023 left any one-pair rows on ClientRemarkTemplate, copy them
    into ClientRemarkPair before the columns are dropped."""
    Template = apps.get_model('costing', 'ClientRemarkTemplate')
    Pair = apps.get_model('costing', 'ClientRemarkPair')
    for tmpl in Template.objects.all():
        remark = getattr(tmpl, 'remark', '') or ''
        answer = getattr(tmpl, 'answer', '') or ''
        color = getattr(tmpl, 'text_color', '') or '#000000'
        if remark or answer:
            Pair.objects.create(
                template=tmpl,
                remark=remark,
                answer=answer,
                remark_color=color,
                answer_color=color,
                order=0,
            )


def copy_single_pairs_backward(apps, schema_editor):
    """No-op: the dropped fields can't be restored from pairs without lossy
    flattening, so reverse migrations leave the parent rows intact."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("costing", "0023_clientremarktemplate"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientRemarkPair",
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
                ("remark", models.TextField()),
                ("answer", models.TextField(blank=True)),
                ("remark_color", models.CharField(default="#000000", max_length=7)),
                ("answer_color", models.CharField(default="#000000", max_length=7)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pairs",
                        to="costing.clientremarktemplate",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "pk"],
            },
        ),
        migrations.RunPython(copy_single_pairs_forward, copy_single_pairs_backward),
        migrations.RemoveField(
            model_name="clientremarktemplate",
            name="remark",
        ),
        migrations.RemoveField(
            model_name="clientremarktemplate",
            name="answer",
        ),
        migrations.RemoveField(
            model_name="clientremarktemplate",
            name="text_color",
        ),
    ]
