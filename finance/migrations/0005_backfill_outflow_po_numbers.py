"""Fill cash-outflow PO numbers for orders committed before the link existed.

Going forward a PO stamps its number onto the outflow rows it covers when it
reaches a committed status. Everything ordered before that has rows still
showing whatever finance typed, or nothing at all.

This runs as a migration because it is the only route to production: the web
service has no shell, so a management command could be run on a laptop and
nowhere else.

Additive and idempotent. It only ever appends a number to a row that does not
already name it, never replaces what finance wrote, and never writes a
generated placeholder. Running it twice changes nothing the second time, which
is also why the reverse is a no-op — there is no way to tell a number this
added from one somebody typed, and guessing would delete real work.
"""
from django.db import migrations


def fill(apps, schema_editor):
    # Deliberately the real implementation rather than a copy frozen at this
    # migration: the rules it enforces (never overwrite, never write a
    # placeholder, committed orders only) are the point, and a divergent copy
    # would be a second set of them to get wrong.
    from finance.outflow_links import backfill

    filled = backfill()
    if filled:
        print(f'\n  Filled {filled} cash-outflow row(s) with their PO number.')


def unfill(apps, schema_editor):
    """No-op. A number this added is indistinguishable from one typed by hand,
    so removing them would delete real work to undo a backfill."""


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0004_paymentmilestone_actual_payment_receive_date_and_more'),
        ('procurement', '0028_procurement_project_board'),
    ]

    operations = [
        migrations.RunPython(fill, unfill),
    ]
