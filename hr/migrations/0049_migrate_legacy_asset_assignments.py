from django.db import migrations
from django.utils import timezone as dj_timezone
import datetime


def migrate_legacy_assignments(apps, schema_editor):
    # One-time conversion of pre-existing open AssetAssignment rows (assets
    # issued via the legacy Issue/Return flow, before AssetHandover
    # existed) into proper AssetHandover rows, so the new flow - now the
    # sole source of truth for current custody - can see and manage them
    # too. Purely additive: never touches AssetAssignment itself, and
    # skips any asset that somehow already has an active AssetHandover.
    AssetAssignment = apps.get_model('hr', 'AssetAssignment')
    AssetHandover = apps.get_model('hr', 'AssetHandover')

    open_assignments = AssetAssignment.objects.filter(
        returned_at__isnull=True).select_related('asset', 'employee')
    for assignment in open_assignments:
        if AssetHandover.objects.filter(asset=assignment.asset, status='active').exists():
            continue
        issued_at = dj_timezone.make_aware(
            datetime.datetime.combine(assignment.assigned_at, datetime.time.min))
        AssetHandover.objects.create(
            asset=assignment.asset,
            employee=assignment.employee,
            status='active',
            issued_by=assignment.assigned_by,
            issued_at=issued_at,
            received_at=issued_at,
        )


def reverse_noop(apps, schema_editor):
    # Deliberately a no-op: reversing would need to know which AssetHandover
    # rows this migration itself created vs. ones created normally through
    # the app afterward, which can't be distinguished after the fact. Safe
    # either way, since this migration never touches AssetAssignment - it
    # only ever adds rows.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0048_alter_assethandover_status'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_assignments, reverse_noop),
    ]
