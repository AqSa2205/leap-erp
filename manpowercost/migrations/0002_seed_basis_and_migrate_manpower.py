"""Seed a default cost basis and carry over the `manpower` app's sheets.

The old figures are copied across **as monthly amounts**, because that is what
the old importer actually wrote into them: it read the source workbook's
monthly columns straight into fields whose `total_yearly_cost` then summed
them with no x12. Reading them as yearly here would multiply every migrated
sheet by twelve.

That assumption cannot be checked per row, so it is recorded on each migrated
sheet's notes rather than left implicit.

The old tables are deliberately NOT dropped here. They are the only copy of
this data, and a migration that both transforms and destroys leaves nothing to
compare against if the transform is wrong.
"""

from decimal import Decimal

from django.db import migrations

COST_FIELDS = (
    'gross_salary', 'iqama_cost', 'service_transfer_visa_fee', 'gosi_cost',
    'vacation_pay', 'exe_cost', 'eosb', 'air_ticket', 'insurance_cost',
    'project_allowance', 'ppe', 'engineering_council_cost',
    'other_expenditures',
)

# Matches the REAL COST sheet's actual arithmetic, not its labels: it says
# "Overhead 10%", "Profit 25%" and "Monthly Inv 10.5 months" while computing
# 5%, 20% and a division by 11.
DEFAULT_BASIS = {
    'name': 'Standard (REAL COST)',
    'overhead_pct': Decimal('5'),
    'profit_pct': Decimal('20'),
    'billable_months': Decimal('11'),
    'hours_per_month': Decimal('176'),
    'working_days_per_month': Decimal('22'),
    'is_default': True,
    'notes': ('Reproduces the REAL COST workbook: overhead 5%, profit 20%, '
              '11 billable months, 176 hours/month. The workbook labelled '
              'these 10%, 25% and 10.5 while computing these values.'),
}

CLASSIFICATION_MAP = {
    'saudi': 'saudi',
    'eastern': 'eastern',
    'other arabs': 'other_arabs',
    'other arab': 'other_arabs',
}


def forwards(apps, schema_editor):
    CostBasis = apps.get_model('manpowercost', 'CostBasis')
    NewSheet = apps.get_model('manpowercost', 'ManpowerCostSheet')
    NewLine = apps.get_model('manpowercost', 'ManpowerCostLine')

    basis, _ = CostBasis.objects.get_or_create(
        name=DEFAULT_BASIS['name'], defaults=DEFAULT_BASIS)

    try:
        OldSheet = apps.get_model('manpower', 'ManpowerSheet')
    except LookupError:
        # The old app has already been removed; nothing to carry over.
        return

    for old in OldSheet.objects.all().iterator():
        if NewSheet.objects.filter(
                title=old.title, date=old.date,
                project_reference=old.project_reference or '').exists():
            continue  # idempotent: re-running must not duplicate sheets

        note = (old.notes + '\n\n') if old.notes else ''
        note += ('Carried over from the previous Manpower module. Cost '
                 'figures were copied as MONTHLY amounts, which is what the '
                 'old importer wrote into them. Check one line against '
                 'payroll before pricing from this sheet.')

        new = NewSheet.objects.create(
            title=old.title,
            project_reference=old.project_reference or '',
            date=old.date,
            basis=basis,
            notes=note,
            created_by_id=old.created_by_id,
        )

        for old_line in old.line_items.all().order_by('order', 'pk'):
            raw_class = (old_line.classification or '').strip().lower()
            values = {f: getattr(old_line, f, Decimal('0')) or Decimal('0')
                      for f in COST_FIELDS}
            NewLine.objects.create(
                sheet=new,
                order=old_line.order,
                employee_name=old_line.employee_name or '',
                department=old_line.department or '',
                classification=CLASSIFICATION_MAP.get(raw_class, ''),
                location_project=old_line.location_project or '',
                designation=old_line.designation or '',
                doj=old_line.doj,
                demobilization_date=old_line.demobilization_date,
                **values,
            )


def backwards(apps, schema_editor):
    """Remove only what this migration created.

    The old `manpower` rows were never deleted, so reversing loses nothing.
    """
    CostBasis = apps.get_model('manpowercost', 'CostBasis')
    NewSheet = apps.get_model('manpowercost', 'ManpowerCostSheet')
    NewSheet.objects.filter(
        notes__contains='Carried over from the previous Manpower module'
    ).delete()
    CostBasis.objects.filter(name=DEFAULT_BASIS['name']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('manpowercost', '0001_initial'),
        ('manpower', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
