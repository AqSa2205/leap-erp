"""Seed a local demo costing sheet for manually verifying the category
(section) autonumbering-on-delete fix.

Creates one CostingSheet with 5 numbered categories (sections), each holding
2-3 line items numbered against their parent category (e.g. section "3" ->
items "3.1", "3.2"). Open the sheet, delete a middle category (e.g. #3), and
the remaining categories/items should immediately renumber to 1, 2, 3, 4
with no gaps.

Safe to re-run: the demo sheet is tagged via customer_reference
"DEMO-COSTING-AUTONUM" and upserted (deleted + recreated), never duplicated.
Run ``python manage.py seed_costing_demo --wipe`` to remove it.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from costing.models import CostingSheet, CostingSection, CostingLineItem

DEMO_TAG = 'DEMO-COSTING-AUTONUM'

CATEGORIES = [
    ('1', 'CCTV Cameras', [
        ('Dome camera, 4MP, IR 30m', 'EA', 10, 450),
        ('PTZ camera, 4MP, 20x zoom', 'EA', 2, 3800),
    ]),
    ('2', 'Access Control', [
        ('Card reader, Wiegand', 'EA', 8, 320),
        ('Electric strike lock', 'EA', 8, 210),
        ('Access control controller, 4-door', 'EA', 2, 1650),
    ]),
    ('3', 'Network Equipment', [
        ('PoE switch, 24-port', 'EA', 3, 1200),
        ('Network video recorder, 32-channel', 'EA', 1, 4200),
    ]),
    ('4', 'Cabling & Accessories', [
        ('Cat6 cable, per roll (305m)', 'Roll', 15, 380),
        ('Cable tray, 100mm, per meter', 'Mtr', 200, 25),
    ]),
    ('5', 'Installation & Commissioning', [
        ('On-site installation labor', 'LOT', 1, 15000),
        ('System commissioning & testing', 'LOT', 1, 5000),
    ]),
]


class Command(BaseCommand):
    help = 'Seed (or --wipe) a demo costing sheet for testing category autonumbering-on-delete.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Remove the demo sheet instead of creating it.')

    def handle(self, *args, **options):
        existing = CostingSheet.objects.filter(customer_reference=DEMO_TAG)
        if existing.exists():
            existing.delete()
            self.stdout.write('Removed existing demo costing sheet.')

        if options['wipe']:
            self.stdout.write(self.style.SUCCESS('Demo costing sheet wiped.'))
            return

        sheet = CostingSheet.objects.create(
            title='DEMO - Autonumbering Test Sheet',
            customer_reference=DEMO_TAG,
            margin=Decimal('40'),
        )

        for order, (section_number, title, items) in enumerate(CATEGORIES):
            section = CostingSection.objects.create(
                costing_sheet=sheet,
                section_number=section_number,
                title=title,
                order=order,
            )
            for item_order, (description, unit, qty, unit_cost) in enumerate(items):
                CostingLineItem.objects.create(
                    section=section,
                    item_number=f'{section_number}.{item_order + 1}',
                    description=description,
                    unit=unit,
                    quantity=Decimal(qty),
                    base_unit_cost=Decimal(unit_cost),
                    order=item_order,
                )

        self.stdout.write(self.style.SUCCESS(
            f'Created demo costing sheet (id={sheet.pk}) with {len(CATEGORIES)} categories. '
            f'Open it at /costing/{sheet.pk}/, delete category "3" (Network Equipment), '
            f'and categories 4 and 5 should renumber to 3 and 4 with their item numbers updated.'
        ))
