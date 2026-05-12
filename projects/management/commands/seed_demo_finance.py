"""Seed a handful of demo Projects + CostingSheets so the new
"Actual Sales / Costing" panel on the project detail page shows
every branch of the resolution rule.

Idempotent — running twice doesn't duplicate; existing demo rows are
upserted by their proposal_reference. Cleanup: ``python manage.py
seed_demo_finance --wipe`` removes everything this command created.

Run locally: ``python manage.py seed_demo_finance``
On Render:   open the service shell and run the same command.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from costing.models import CostingSection, CostingSheet, CostingLineItem
from projects.models import Project, ProjectStatus, Region


DEMO_TAG = 'DEMO-FIN-'


class Command(BaseCommand):
    help = 'Seed demo projects + costing sheets that exercise every state of the Actual Sales / Costing panel.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true',
                            help='Delete demo rows created by this command and exit.')

    def handle(self, *args, **opts):
        if opts['wipe']:
            self._wipe()
            return

        region = Region.objects.filter(code='LNA').first() or Region.objects.first()
        if not region:
            self.stderr.write(self.style.ERROR('No Region rows exist — nothing to seed against.'))
            return

        # Pick a sensible owner (super admin if available, otherwise any active).
        owner = (User.objects.filter(is_active=True, role__name='super_admin').first()
                 or User.objects.filter(is_active=True).first())
        if not owner:
            self.stderr.write(self.style.ERROR('No active users — nothing to own these demos.'))
            return

        status_active = (ProjectStatus.objects.filter(category='active').first()
                         or ProjectStatus.objects.first())
        status_won = ProjectStatus.objects.filter(category='won').first() or status_active

        self.stdout.write(self.style.NOTICE(f'Seeding demo data in region {region.code} owned by {owner.username}…'))

        # ── 1. No costing sheet at all → "Costing not started" ────────
        self._upsert_project(
            ref=f'{DEMO_TAG}NO-COSTING',
            name='Demo — Pipeline entry with no costing sheet yet',
            customer='ACME Holdings (demo)',
            end_user='ACME Industrial Park',
            estimated_value=Decimal('850000'),
            estimated_value_usd=Decimal('226678'),
            actual_sales=Decimal('0'),
            region=region,
            status=status_active,
            owner=owner,
        )

        # ── 2. Costing sheet still in BOM phase → "Costing in progress"
        proj_bom = self._upsert_project(
            ref=f'{DEMO_TAG}BOM',
            name='Demo — BOM in progress',
            customer='Beta Construction (demo)',
            end_user='Beta HQ',
            estimated_value=Decimal('1250000'),
            estimated_value_usd=Decimal('333334'),
            actual_sales=Decimal('0'),
            region=region,
            status=status_active,
            owner=owner,
        )
        self._upsert_sheet(
            title='Demo BOM — Riyadh Tower ELV',
            project=proj_bom,
            stage='bom_in_progress',
            owner=owner,
            seed_items=False,
        )

        # ── 3. Sheet in costing_in_progress with real items → "Costing Total (live)"
        proj_costing = self._upsert_project(
            ref=f'{DEMO_TAG}COSTING',
            name='Demo — Costing in progress (live total shown)',
            customer='Gamma Telecom (demo)',
            end_user='Gamma Data Center',
            estimated_value=Decimal('1875000'),
            estimated_value_usd=Decimal('500000'),
            actual_sales=Decimal('0'),
            region=region,
            status=status_won,
            owner=owner,
        )
        self._upsert_sheet(
            title='Demo Costing — Gamma DC Phase 1',
            project=proj_costing,
            stage='costing_in_progress',
            owner=owner,
            seed_items=True,
        )

        # ── 4. Actual sales recorded → falls back to actual_sales path
        self._upsert_project(
            ref=f'{DEMO_TAG}CLOSED',
            name='Demo — Deal closed, actual sales recorded',
            customer='Delta Energy (demo)',
            end_user='Delta Field 3',
            estimated_value=Decimal('2100000'),
            estimated_value_usd=Decimal('560000'),
            actual_sales=Decimal('1985500'),
            region=region,
            status=status_won,
            owner=owner,
        )

        self.stdout.write(self.style.SUCCESS('Demo finance projects seeded. Open Commercial Pipeline to see all four.'))

    def _upsert_project(self, *, ref, name, customer, end_user,
                        estimated_value, estimated_value_usd,
                        actual_sales, region, status, owner):
        defaults = {
            'project_name':         name,
            'customer':             customer,
            'end_user':             end_user,
            'estimated_value':      estimated_value,
            'estimated_value_usd':  estimated_value_usd,
            'actual_sales':         actual_sales,
            'region':               region,
            'status':               status,
            'owner':                owner,
            'created_by':           owner,
        }
        proj, created = Project.objects.update_or_create(
            proposal_reference=ref, defaults=defaults,
        )
        verb = 'created' if created else 'updated'
        self.stdout.write(f'  · {verb} project {ref} ({name})')
        return proj

    def _upsert_sheet(self, *, title, project, stage, owner, seed_items):
        sheet, created = CostingSheet.objects.update_or_create(
            title=title,
            project=project,
            defaults={
                'workflow_stage': stage,
                'created_by': owner,
                'output_currency': 'SAR',
                'margin': Decimal('40'),
            },
        )
        verb = 'created' if created else 'updated'
        self.stdout.write(f'    └─ {verb} costing sheet "{title}" (stage={stage})')

        if seed_items and not sheet.sections.exists():
            section = CostingSection.objects.create(
                costing_sheet=sheet,
                section_number='1.0',
                title='Demo Networking Equipment',
                order=1,
            )
            items = [
                ('1.1', 'Managed Switch 48-port',  '5', 'Nos', '4200'),
                ('1.2', '24-port PoE+ Switch',     '8', 'Nos', '3100'),
                ('1.3', 'Fiber Module SFP+ 10G',  '24', 'Nos',  '380'),
                ('1.4', 'Cat6 Patch Cord 2m',    '120', 'Nos',   '18'),
                ('1.5', 'Rack 42U with cooling',   '2', 'Nos', '9800'),
            ]
            for item_no, desc, qty, unit, cost in items:
                CostingLineItem.objects.create(
                    section=section,
                    item_number=item_no,
                    description=desc,
                    make='Demo Make',
                    model_number='DM-' + item_no.replace('.', ''),
                    quantity=Decimal(qty),
                    unit=unit,
                    base_unit_cost=Decimal(cost),
                    supplier_currency='SAR',
                )
            self.stdout.write(f'    └─ seeded {len(items)} demo line items')

    def _wipe(self):
        from costing.models import CostingSheet
        projs = Project.objects.filter(proposal_reference__startswith=DEMO_TAG)
        sheet_count = CostingSheet.objects.filter(project__in=projs).count()
        proj_count = projs.count()
        # CostingSheet sections + line items cascade-delete with the sheet,
        # the sheet cascade-deletes with the project's deletion.
        projs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Removed {proj_count} demo project(s) and {sheet_count} linked costing sheet(s).'
        ))
