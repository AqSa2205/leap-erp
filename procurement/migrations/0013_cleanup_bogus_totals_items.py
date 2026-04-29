"""Delete the bogus PurchaseOrderItem rows that the old Excel-import bug
persisted as line items: "Base Amount", "Discount", "Gross Value", "VAT",
and "Total Value in SAR".

Safe filter: only deletes rows whose total_value (qty x rate) is exactly 0
AND make_model is empty — so any legitimately-named-but-real line items
are left alone.
"""
from decimal import Decimal
from django.db import migrations
from django.db.models import F


BOGUS_DESCRIPTIONS = (
    'Base Amount',
    'Discount',
    'Gross Value',
    'VAT',
    'Total Value in SAR',
)


def delete_bogus_totals_items(apps, schema_editor):
    PurchaseOrderItem = apps.get_model('procurement', 'PurchaseOrderItem')
    qs = PurchaseOrderItem.objects.filter(
        description__in=BOGUS_DESCRIPTIONS,
        make_model='',
    ).annotate(
        # We want rows where qty * rate == 0 (i.e. no real money on them).
        # Since we can't multiply in a filter portably across SQL backends
        # without expressions, gate on qty=0 OR rate=0 — both leave the
        # row's total_value at 0, matching the bogus-row signature.
    ).filter(rate_per_unit=Decimal('0'))
    count = qs.count()
    if count:
        qs.delete()
    # No-op if nothing matches — migration is idempotent.


def noop_reverse(apps, schema_editor):
    # No reverse: re-creating bogus data isn't useful.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0012_external_summary_fields'),
    ]

    operations = [
        migrations.RunPython(delete_bogus_totals_items, noop_reverse),
    ]
