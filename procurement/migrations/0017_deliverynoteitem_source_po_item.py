# Generated for DN-from-PO partial-delivery tracking.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procurement", "0016_purchaseorder_ceo_signature_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="deliverynoteitem",
            name="source_po_item",
            field=models.ForeignKey(
                blank=True,
                help_text='If imported from a Purchase Order, points back to the source PO line item — used to mark PO items "already delivered" so remaining items can be picked for later DNs.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="delivered_dn_items",
                to="procurement.purchaseorderitem",
            ),
        ),
    ]
