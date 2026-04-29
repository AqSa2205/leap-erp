"""Move `system` field from PurchaseOrder to PurchaseOrderItem and
re-point POSummaryEntry from PurchaseOrder to PurchaseOrderItem.

The summary table is intentionally empty before this migration runs
(see procurement.views.internal_summary which auto-creates entries),
so dropping/adding the OneToOne is safe.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0010_drop_mta_production_fat'),
    ]

    operations = [
        # 1) Drop summary entries (FK we're about to rebuild)
        migrations.RunSQL(
            sql='DELETE FROM procurement_posummaryentry;',
            reverse_sql=migrations.RunSQL.noop,
        ),

        # 2) Drop the old PurchaseOrder->summary OneToOne
        migrations.RemoveField(
            model_name='posummaryentry',
            name='purchase_order',
        ),

        # 3) Drop the now-orphaned `system` and `description` fields on PurchaseOrder
        migrations.RemoveField(
            model_name='purchaseorder',
            name='system',
        ),
        migrations.RemoveField(
            model_name='purchaseorder',
            name='description',
        ),

        # 4) Add `system` to PurchaseOrderItem
        migrations.AddField(
            model_name='purchaseorderitem',
            name='system',
            field=models.CharField(
                blank=True, max_length=100,
                verbose_name='System',
                help_text=(
                    'Pick from the suggestion list (Siren / PAGA / PICS / GSM Repeater / CCTV / UPS) '
                    'or type a custom value. Drives grouping in the procurement summary.'
                ),
            ),
        ),

        # 5) Add the new OneToOne purchase_order_item on POSummaryEntry
        migrations.AddField(
            model_name='posummaryentry',
            name='purchase_order_item',
            field=models.OneToOneField(
                on_delete=models.deletion.CASCADE,
                related_name='summary_entry',
                to='procurement.purchaseorderitem',
            ),
            preserve_default=False,
        ),

        # 6) Update the POSummaryEntry Meta ordering
        migrations.AlterModelOptions(
            name='posummaryentry',
            options={
                'ordering': [
                    'purchase_order_item__system',
                    'purchase_order_item__purchase_order__po_number',
                    'purchase_order_item__serial_number',
                ],
                'verbose_name': 'PO Summary Entry',
                'verbose_name_plural': 'PO Summary Entries',
            },
        ),
    ]
