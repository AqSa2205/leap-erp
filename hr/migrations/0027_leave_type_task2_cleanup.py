"""Data migration: reconcile LeaveType rows to the exact 6-type business rule.

Background (see hr/migrations/0011, 0023, 0024):
- 0011 accidentally seeded a fourth type, `Unpaid` (code='unpaid'), which was
  never part of the spec and must be removed.
- 0023/0024 both only ran `.filter(code__in=[...]).update(...)` for codes like
  `marriage`, `death_of_family_member`, `new_born`, `umrah` — since those rows
  were never created by any migration, those updates were no-ops on a fresh
  database. `Death Of Family Member` and `New Born` therefore don't actually
  exist today, and `Umrah`'s name/days never got corrected either.

This migration:
1. Removes the `Unpaid` type (or, defensively, deactivates it instead if real
   history rows reference it, since LeaveRecord/LeaveEntitlement both use
   on_delete=PROTECT against LeaveType).
2. get_or_create's (and corrects the values of) the exact six leave types the
   system is supposed to have: Annual, Death Of Family Member, Marriage,
   New Born, Sick, Umrah Leave.
"""
from decimal import Decimal

from django.db import migrations


# name, code, default_annual_days, is_accumulative, is_paid
LEAVE_TYPES = [
    ('Annual', 'annual', Decimal('30.0'), True, True),
    ('Death Of Family Member', 'death_of_family_member', Decimal('3.0'), False, True),
    ('Marriage', 'marriage', Decimal('3.0'), False, True),
    ('New Born', 'new_born', Decimal('3.0'), False, True),
    ('Sick', 'sick', Decimal('12.0'), False, True),
    ('Umrah Leave', 'umrah', Decimal('2.0'), False, True),
]


def remove_unpaid_leave_type(apps, schema_editor):
    LeaveType = apps.get_model('hr', 'LeaveType')
    LeaveRecord = apps.get_model('hr', 'LeaveRecord')
    LeaveEntitlement = apps.get_model('hr', 'LeaveEntitlement')

    unpaid = LeaveType.objects.filter(code='unpaid').first()
    if unpaid is None:
        return

    has_records = LeaveRecord.objects.filter(leave_type=unpaid).count() > 0
    has_entitlements = LeaveEntitlement.objects.filter(leave_type=unpaid).count() > 0
    if not has_records and not has_entitlements:
        unpaid.delete()
    else:
        # Defensive fallback: some environment genuinely recorded leave
        # history against "Unpaid" — deleting would violate the PROTECT
        # constraint (and destroy history) so just deactivate it instead. It
        # will stop appearing in active pickers without breaking anything.
        unpaid.is_active = False
        unpaid.save(update_fields=['is_active'])


def apply_exact_leave_types(apps, schema_editor):
    LeaveType = apps.get_model('hr', 'LeaveType')
    for name, code, days, is_accumulative, is_paid in LEAVE_TYPES:
        obj, created = LeaveType.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'default_annual_days': days,
                'is_accumulative': is_accumulative,
                'is_paid': is_paid,
                'is_active': True,
            },
        )
        if not created:
            fields_to_update = []
            if obj.name != name:
                obj.name = name
                fields_to_update.append('name')
            if obj.default_annual_days != days:
                obj.default_annual_days = days
                fields_to_update.append('default_annual_days')
            if obj.is_accumulative != is_accumulative:
                obj.is_accumulative = is_accumulative
                fields_to_update.append('is_accumulative')
            if fields_to_update:
                obj.save(update_fields=fields_to_update)


def seed(apps, schema_editor):
    remove_unpaid_leave_type(apps, schema_editor)
    apply_exact_leave_types(apps, schema_editor)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0026_alter_leavetype_is_accumulative'),
    ]

    operations = [
        migrations.RunPython(seed, reverse_noop),
    ]
