"""Seed local demo data for Project Manpower Costing.

The feature shipped with no data — there is nothing to look at until a PM
fills in Grade Structure and First Year Maintenance by hand. This creates one
demo project with both sheets fully populated, so the calculated totals
(section subtotals, annual roll-up, proposed contract value, profit, VAT)
can be checked against the source Excel workbook's own layout at a glance.

Every figure here is an INVENTED, deliberately round placeholder — not real
pricing — chosen so the arithmetic is easy to verify by hand:

  First Year Maintenance:
    Section A (manpower, monthly/annual)  ->  73,000/mo x 12 =   876,000
    Section B (manpower, one time)                           =   150,000
    Section C (vehicle/consumables, monthly) -> 17,000/mo x12 =   204,000
    Section D (tools/equipment, one time)                    =    75,000
                                             Total annual cost = 1,305,000
    At a 25% margin: contract value 1,740,000; profit 435,000;
    incl. 15% VAT: 2,001,000.

  Grade Structure: 25 heads across all 5 categories (EX/A/E/T/O),
  total annual cost 1,776,000 — deliberately a different number from First
  Year Maintenance's total, since only First Year Maintenance drives the bid
  value; Grade Structure is headcount planning.

Tagged DEMO-MPC in the project's notes field; --wipe removes exactly that
project (and, via CASCADE, everything under it).

Run:   python manage.py seed_manpower_costing_demo
Undo:  python manage.py seed_manpower_costing_demo --wipe
"""
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

TAG = 'DEMO-MPC'
PROPOSAL_REFERENCE = 'DEMO-MPC-2026'

# (category, grade_code, discipline, typical_designation, headcount, monthly_salary)
GRADE_STRUCTURE = [
    ('EX', 'EX-1', 'General/N/A', 'Project Director', 1, 25000),
    ('A', 'A-1', 'General/N/A', 'Admin Coordinator', 2, 6000),
    ('E', 'E-1', 'Telecom', 'Senior Engineer', 2, 12000),
    ('E', 'E-2', 'Security', 'Engineer', 3, 9000),
    ('T', 'T-1', 'Telecom', 'Senior Technician', 4, 5000),
    ('T', 'T-2', 'Hydraulics', 'Technician', 5, 4000),
    ('O', 'O-1', 'General/N/A', 'General Labour', 8, 2500),
]

# (section, description, nature, amount, qty, remarks)
FIRST_YEAR_MAINTENANCE = [
    ('A', 'Site Manager', 'monthly', 15000, 1, 'DEMO-MPC — 1 head'),
    ('A', 'Site Engineers', 'monthly', 8000, 3, 'DEMO-MPC — 3 heads'),
    ('A', 'Technicians', 'monthly', 4000, 6, 'DEMO-MPC — 6 heads'),
    ('A', 'Iqama / Medical / Air Ticket (per head, per year)', 'annual', 12000, 10, 'DEMO-MPC — spread over 12 months, 10 heads'),
    ('B', 'Mobilization', 'one_time', 100000, 1, 'DEMO-MPC'),
    ('B', 'Recruitment & Visa Costs', 'one_time', 50000, 1, 'DEMO-MPC'),
    ('C', 'Vehicle Lease', 'monthly', 3000, 4, 'DEMO-MPC — 4 vehicles'),
    ('C', 'Fuel & Consumables', 'monthly', 5000, 1, 'DEMO-MPC'),
    ('D', 'Tools & Equipment Set', 'one_time', 60000, 1, 'DEMO-MPC'),
    ('D', 'PPE — Initial Issue', 'one_time', 15000, 1, 'DEMO-MPC'),
]


class Command(BaseCommand):
    help = 'Seed (or --wipe) a demo project for Project Manpower Costing.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true',
                             help='Remove the demo project instead of creating it.')
        parser.add_argument('--force', action='store_true',
                             help='Allow running with DEBUG off. Refused otherwise.')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError(
                'DEBUG is off — this looks like a deployed environment. '
                'Re-run with --force only if you are certain.')

        from projects.models import Project, ProjectStatus, Region
        from pmo.models import FirstYearMaintenanceLine, GradeStructureLine, ManpowerCostingHeader

        if options['wipe']:
            return self._wipe(Project)

        with transaction.atomic():
            region = Region.objects.filter(code='LNA').first() or Region.objects.first()
            if region is None:
                raise CommandError('No Region exists — run migrations/seed data first.')
            status, _ = ProjectStatus.objects.get_or_create(
                name='Ongoing', defaults={'category': 'ongoing'})

            project, created = Project.objects.get_or_create(
                proposal_reference=PROPOSAL_REFERENCE,
                defaults=dict(
                    project_name='Demo — Facility O&M Contract (Manpower Costing)',
                    customer='Demo Client Co.',
                    po_number='PO-DEMO-2026-001',
                    region=region,
                    status=status,
                    notes=f'{TAG} — demo data, safe to wipe.',
                ),
            )
            if not created:
                project.grade_structure_lines.all().delete()
                project.first_year_maintenance_lines.all().delete()

            for order, (category, grade_code, discipline, designation, headcount, salary) in enumerate(GRADE_STRUCTURE):
                GradeStructureLine.objects.create(
                    project=project, order=order, category=category, grade_code=grade_code,
                    discipline=discipline, typical_designation=designation,
                    criteria=f'{TAG} demo line', headcount=headcount,
                    indicative_monthly_salary=Decimal(str(salary)))

            for order, (section, description, nature, amount, qty, remarks) in enumerate(FIRST_YEAR_MAINTENANCE):
                FirstYearMaintenanceLine.objects.create(
                    project=project, order=order, section=section, description=description,
                    nature_of_expense=nature, amount=Decimal(str(amount)), qty=Decimal(str(qty)),
                    remarks=remarks)

            header, _ = ManpowerCostingHeader.objects.update_or_create(
                project=project,
                defaults=dict(
                    po_type='1 Year (Renewable)',
                    po_value=Decimal('1740000.00'),
                    vat_rate=Decimal('15'),
                    profit_margin_pct=Decimal('25'),
                ),
            )

        self.stdout.write(self.style.SUCCESS(
            f'Seeded project #{project.pk} "{project.project_name}" '
            f'({region.code}, {"created" if created else "refreshed"}).'))
        self.stdout.write(
            f'  First Year Maintenance: total annual cost {header.total_annual_cost:,} '
            f'-> contract value {header.proposed_contract_value:,} '
            f'-> profit {header.profit:,} -> incl. VAT {header.proposed_contract_value_incl_vat:,}')
        total_headcount = sum(h for *_r, h, _s in GRADE_STRUCTURE)
        self.stdout.write(f'  Grade Structure: {total_headcount} heads across 5 categories.')
        self.stdout.write(
            f'  Open it at /delivery/manpower-costing/{project.pk}/ '
            f'(log in as pm_demo, or any project_manager/admin/super_admin account).')
        self.stdout.write(f'Undo with: python manage.py seed_manpower_costing_demo --wipe')

    def _wipe(self, Project):
        qs = Project.objects.filter(proposal_reference=PROPOSAL_REFERENCE, notes__contains=TAG)
        count = qs.count()
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f'Wiped {count} demo project(s) (cascades to their costing rows).'))
