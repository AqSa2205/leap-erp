from decimal import Decimal
from django.db import migrations

# code -> (default_annual_days, is_accumulative)
LEAVE_TYPE_VALUES = {
    'annual': (Decimal('30'), True),
    'death_of_family_member': (Decimal('3.0'), False),
    'death': (Decimal('3.0'), False),  # some environments may use this shorter code instead
    'marriage': (Decimal('3.0'), False),
    'new_born': (Decimal('3.0'), False),
    'newborn': (Decimal('3.0'), False),
    'sick': (Decimal('12.0'), False),
    'umrah': (Decimal('2.0'), False),
}


def apply_task1_values(apps, schema_editor):
    LeaveType = apps.get_model('hr', 'LeaveType')
    for code, (days, is_accumulative) in LEAVE_TYPE_VALUES.items():
        LeaveType.objects.filter(code=code).update(default_annual_days=days, is_accumulative=is_accumulative)
    # Crucial logic change: ANY leave type not explicitly listed above (i.e. not Annual)
    # must also be non-accumulative — Annual is the only standard accrued type.
    LeaveType.objects.exclude(code='annual').update(is_accumulative=False)
    LeaveType.objects.filter(code='annual').update(is_accumulative=True)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0023_add_leavetype_is_accumulative'),
    ]

    operations = [
        migrations.RunPython(apply_task1_values, reverse_noop),
    ]
