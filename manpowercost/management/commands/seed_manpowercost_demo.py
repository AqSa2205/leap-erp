"""Seed local demo data for the Manpower Costing module.

The module shipped with no charge rates, so A.4's Std rate column shows a
dash for every role and the rate card is empty - there is nothing to look at
until somebody enters rates by hand in the admin. This fills that in.

What it creates:
  - a second cost basis ("Site 10-hour day") so the rate card shows that the
    basis, not just the cost, moves the rate;
  - "Company Overheads 2026" - office staff, the shape of the source
    workbook's own sheet;
  - "Jubail Site Crew 2026" - site roles that map onto the A.4 resource
    catalogue, including the same role at two nationality bands, which is
    the spread the Jubail sheet prices;
  - one line with no gross salary, so the "not counted in the totals"
    warning is visible rather than described;
  - charge rates for the site roles, derived from that crew sheet's real
    monthly cost, with one manual override so the "fixed" badge shows.

Everything is tagged DEMO-MANPOWERCOST in a note field, and --wipe removes
exactly what was tagged.

Run:   python manage.py seed_manpowercost_demo
Undo:  python manage.py seed_manpowercost_demo --wipe
"""
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

TAG = 'DEMO-MANPOWERCOST'

# (title, designation, department, classification, gross, iqama, gosi, eosb,
#  air ticket, insurance, saudization, allowance) - monthly SAR.
#
# The salaries here are INVENTED - round placeholder figures, deliberately
# not anyone's real pay. An earlier version of this file carried the actual
# figures from the source workbook against these same names, which is real
# payroll data and has no business sitting in a demo seeder. The names stay
# because the point of the demo is a recognisable roster; what people are
# paid is not needed to show a cost sheet working.
#
# The benefit columns are policy-level constants (Iqama, ticket, Saudization
# levy, insurance), not personal pay, so they stay as the real per-head
# rates - those are what make the totals look plausible.
OFFICE = [
    ('Mr. Asif Imam', 'CEO', 'BOD', 'other', 20130, 854, 0, 833, 208, 119, 0, 0),
    ('Mr. Tarek Bary', 'Chairman', 'BOD', 'other', 18530, 0, 0, 771, 208, 119, 0, 0),
    ('Mr. Amir Fida', 'BDO', 'Sales & Marketing', 'other_arabs', 14030, 854, 175, 583, 208, 119, 1000, 500),
    ('Khizar Ahmed', 'Sales Manager', 'Sales & Marketing', 'other', 12530, 854, 156, 521, 208, 119, 1000, 500),
    ('Babar Zulfiqar', 'COO', 'Accounts & Finance', 'other', 15530, 854, 194, 646, 208, 119, 1000, 0),
    ('Syed Hasan Abbas', 'Procurement Officer', 'Procurement', 'other', 8530, 854, 106, 354, 208, 119, 1000, 0),
    ('Mr. Bashar Al Momin', 'Admin Manager', 'Admin', 'saudi', 11030, 0, 1310, 458, 0, 119, 0, 0),
    ('Lauy Al Biladi', 'GRO', 'Admin', 'saudi', 7530, 0, 893, 313, 0, 119, 0, 0),
]

# (name, catalogue role, classification, gross, iqama, gosi, eosb, ticket,
#  insurance, saudization, project allowance, ppe)
SITE = [
    ('Ali Haider', 'Project Manager', 'eastern', 22130, 875, 275, 917, 833, 119, 1000, 2000, 60),
    ('Mohammed Al Otaibi', 'Project Manager', 'saudi', 26130, 0, 3100, 1083, 0, 119, 0, 2000, 60),
    ('Rakesh Menon', 'Site Engineer (Civil)', 'eastern', 12130, 875, 150, 500, 833, 119, 1000, 1500, 60),
    ('Samir Haddad', 'Site Engineer (Civil)', 'other_arabs', 15630, 875, 194, 646, 833, 119, 1000, 1500, 60),
    ('Imran Qureshi', 'Safety Officer', 'eastern', 9530, 875, 119, 396, 833, 119, 1000, 1200, 120),
    ('Bilal Anwar', 'Welder', 'eastern', 4530, 875, 56, 188, 833, 119, 1000, 600, 150),
    ('Suresh Kumar', 'Technician/ Carpenter/ Steel Fixer', 'eastern', 3830, 875, 48, 158, 833, 119, 1000, 600, 150),
    ('Arun Pillai', 'Helper', 'eastern', 2030, 875, 25, 83, 833, 119, 1000, 400, 150),
    # Deliberately no salary: shows the incomplete-line warning.
    ('New Starter (salary TBC)', 'Telecom Technician', 'eastern', 0, 875, 0, 0, 833, 119, 1000, 600, 150),
]

# Roles that get a charge rate, and whether the rate is negotiated rather
# than derived. 'Helper' is fixed to show the override path.
MANUAL_RATES = {'Helper': Decimal('38.00')}


class Command(BaseCommand):
    help = 'Seed (or --wipe) demo data for the Manpower Costing module.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true',
                            help='Remove the demo data instead of creating it.')
        parser.add_argument('--force', action='store_true',
                            help='Allow running with DEBUG off. Refused otherwise.')

    def handle(self, *args, **options):
        # Demo data on a live database would be indistinguishable from real
        # staff costs, and this module is what people price from.
        if not settings.DEBUG and not options['force']:
            raise CommandError(
                'DEBUG is off - this looks like a deployed environment. '
                'Demo salaries here would be indistinguishable from real ones. '
                'Re-run with --force only if you are certain.')

        from manpowercost.models import (
            ChargeRate, CostBasis, ManpowerCostLine, ManpowerCostSheet)

        if options['wipe']:
            return self._wipe(ChargeRate, CostBasis, ManpowerCostSheet)

        with transaction.atomic():
            basis = CostBasis.get_default()
            if basis is None:
                raise CommandError(
                    'No default cost basis - run migrations first '
                    '(manpowercost/0003 seeds one).')

            site_basis, _ = CostBasis.objects.get_or_create(
                name='Site 10-hour day',
                defaults=dict(
                    overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
                    billable_months=Decimal('11'),
                    hours_per_month=Decimal('260'),
                    working_days_per_month=Decimal('26'),
                    notes=f'{TAG} - 26 days x 10 hours, the site pattern. Shows '
                          f'how the basis alone moves the hourly rate.'))

            office = self._sheet(ManpowerCostSheet, 'Company Overheads 2026',
                                 'OVERHEAD-2026', basis)
            for (name, desig, dept, cls, gross, iqama, gosi, eosb, ticket,
                 ins, saud, allow) in OFFICE:
                self._line(ManpowerCostLine, office, name, desig, dept, cls,
                           gross_salary=gross, iqama_cost=iqama, gosi_cost=gosi,
                           eosb=eosb, air_ticket=ticket, insurance_cost=ins,
                           saudization_cost=saud, project_allowance=allow)

            crew = self._sheet(ManpowerCostSheet, 'Jubail Site Crew 2026',
                               'JUBAIL-2026', site_basis)
            role_costs = {}
            for (name, role, cls, gross, iqama, gosi, eosb, ticket, ins,
                 saud, allow, ppe) in SITE:
                line = self._line(
                    ManpowerCostLine, crew, name, role, 'PMT', cls,
                    gross_salary=gross, iqama_cost=iqama, gosi_cost=gosi,
                    eosb=eosb, air_ticket=ticket, insurance_cost=ins,
                    saudization_cost=saud, project_allowance=allow, ppe=ppe,
                    position_name=role)
                if gross:
                    role_costs.setdefault((role, cls), line.monthly_cost)

            rates_made = self._charge_rates(ChargeRate, site_basis, role_costs)

        self.stdout.write(self.style.SUCCESS(
            f'Seeded: 2 cost sheets ({len(OFFICE)} office, {len(SITE)} site '
            f'lines), 1 extra cost basis, {rates_made} charge rates.'))
        self.stdout.write(
            '  /manpower-costing/         cost sheets\n'
            '  /manpower-costing/rates/   charge rates (one is a fixed override)\n'
            '  A costing sheet\'s A.4 Resources now offers a Std rate for the '
            'site roles.\n'
            f'Undo with: python manage.py seed_manpowercost_demo --wipe')

    # ── helpers ──────────────────────────────────────────────────────────
    def _sheet(self, Sheet, title, reference, basis):
        from django.utils import timezone
        sheet, created = Sheet.objects.get_or_create(
            title=title,
            defaults=dict(project_reference=reference, date=timezone.localdate(),
                          basis=basis, notes=f'{TAG} - demo data, safe to wipe.'))
        if not created:
            sheet.lines.all().delete()   # re-runnable without duplicating rows
        return sheet

    def _line(self, Line, sheet, name, designation, department, classification,
              position_name=None, **costs):
        position = None
        if position_name:
            from costing.models import ResourceCatalogueItem
            position = ResourceCatalogueItem.objects.filter(
                name__iexact=position_name).first()
        order = sheet.lines.count() + 1
        return Line.objects.create(
            sheet=sheet, order=order, employee_name=name,
            designation=designation, department=department,
            classification=classification, position=position,
            **{k: Decimal(str(v)) for k, v in costs.items()})

    def _charge_rates(self, ChargeRate, basis, role_costs):
        from costing.models import ResourceCatalogueItem
        made = 0
        for (role_name, classification), monthly in role_costs.items():
            position = ResourceCatalogueItem.objects.filter(
                name__iexact=role_name).first()
            if position is None:
                continue
            ChargeRate.objects.update_or_create(
                position=position, classification=classification, basis=basis,
                defaults=dict(
                    monthly_cost=monthly,
                    manual_rate=MANUAL_RATES.get(role_name),
                    notes=f'{TAG} - built from the Jubail crew sheet.'))
            made += 1
        return made

    def _wipe(self, ChargeRate, CostBasis, Sheet):
        rates = ChargeRate.objects.filter(notes__contains=TAG).delete()[0]
        sheets = Sheet.objects.filter(notes__contains=TAG)
        lines = sum(s.lines.count() for s in sheets)
        removed = sheets.delete()[0]
        # PROTECT on sheet.basis means this only succeeds once the sheets
        # above are gone, which is the order we want anyway.
        bases = CostBasis.objects.filter(notes__contains=TAG).delete()[0]
        self.stdout.write(self.style.SUCCESS(
            f'Wiped {rates} charge rate(s), {lines} line(s) across '
            f'{removed} sheet row(s), {bases} cost basis row(s).'))
