import costing.models
from django.db import migrations, models


def pairs_to_grid(apps, schema_editor):
    """Fold each bundle's ClientRemarkPair rows into the new columns/rows
    JSON grid. Every legacy bundle keeps exactly its two columns
    (Client Remark / Leap's Answer) with per-cell colours preserved."""
    Template = apps.get_model('costing', 'ClientRemarkTemplate')
    for tmpl in Template.objects.all():
        tmpl.columns = [
            {'key': 'c1', 'name': 'Client Remark'},
            {'key': 'c2', 'name': "Leap's Answer"},
        ]
        rows = []
        for pair in tmpl.pairs.order_by('order', 'pk'):
            rows.append({'cells': {
                'c1': {'text': pair.remark or '', 'color': pair.remark_color or '#000000'},
                'c2': {'text': pair.answer or '', 'color': pair.answer_color or '#000000'},
            }})
        tmpl.rows = rows
        tmpl.save(update_fields=['columns', 'rows'])


def grid_to_pairs(apps, schema_editor):
    """Best-effort reverse: rebuild ClientRemarkPair rows from the first two
    columns of the grid (later columns can't be represented and are dropped)."""
    Template = apps.get_model('costing', 'ClientRemarkTemplate')
    Pair = apps.get_model('costing', 'ClientRemarkPair')
    for tmpl in Template.objects.all():
        cols = tmpl.columns or []
        k1 = cols[0]['key'] if len(cols) > 0 else 'c1'
        k2 = cols[1]['key'] if len(cols) > 1 else 'c2'
        for order, row in enumerate(tmpl.rows or []):
            cells = (row or {}).get('cells') or {}
            c1 = cells.get(k1) or {}
            c2 = cells.get(k2) or {}
            Pair.objects.create(
                template=tmpl,
                remark=c1.get('text', '') or '',
                answer=c2.get('text', '') or '',
                remark_color=c1.get('color') or '#000000',
                answer_color=c2.get('color') or '#000000',
                order=order,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('costing', '0036_costingsheet_bom_started_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='clientremarktemplate',
            name='columns',
            field=models.JSONField(default=costing.models.default_remark_columns),
        ),
        migrations.AddField(
            model_name='clientremarktemplate',
            name='rows',
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(pairs_to_grid, grid_to_pairs),
        migrations.DeleteModel(name='ClientRemarkPair'),
    ]
