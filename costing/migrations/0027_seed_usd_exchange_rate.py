from decimal import Decimal

from django.db import migrations


SEED_RATES = [
    ("USD", "US Dollar",      Decimal("1.000000")),
    ("SAR", "Saudi Riyal",    Decimal("3.750000")),
    ("AED", "UAE Dirham",     Decimal("3.670000")),
    ("GBP", "British Pound",  Decimal("0.790000")),
    ("EUR", "Euro",           Decimal("0.920000")),
]


def seed(apps, schema_editor):
    ExchangeRate = apps.get_model("costing", "ExchangeRate")
    for code, name, rate in SEED_RATES:
        ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={"currency_name": name, "rate_to_usd": rate},
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("costing", "0026_costingsheet_finance_approved_at_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
