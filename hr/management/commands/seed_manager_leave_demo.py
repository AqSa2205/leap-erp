"""Seed local demo data for manually verifying manager-logged leave
requests (direct-manager Org Chart link, not a named Role):

- DEMO-MGRLEAVE-MANAGER: a plain employee (no special Role) with a login,
  set as the main_manager of the employee below.
- DEMO-MGRLEAVE-REPORT: their direct report, with a current-year Annual
  entitlement already set up.

Log in as demo.manager / DemoPass123!, open My Profile, and use the
"My Team" section's Log Leave link to log a request for the report —
it should land in the normal Leave Requests queue, waiting on whoever
holds LeaveDashboardAccess, exactly like any other request.

Safe to re-run: tagged with a "DEMO-MGRLEAVE-" iqama prefix and upserted.
Run ``python manage.py seed_manager_leave_demo --wipe`` to remove it.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from hr.models import Employee, LeaveType, LeaveEntitlement

User = get_user_model()
TAG_PREFIX = 'DEMO-MGRLEAVE-'


class Command(BaseCommand):
    help = 'Seed (or --wipe) demo data for manager-logged leave requests.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Remove the demo data instead of creating it.')

    def handle(self, *args, **options):
        existing = Employee.objects.filter(iqama_number__startswith=TAG_PREFIX)
        if existing.exists():
            existing.delete()
            self.stdout.write(f'Removed {existing.count()} existing demo employee(s).')
        User.objects.filter(username='demo.manager').delete()

        if options['wipe']:
            self.stdout.write(self.style.SUCCESS('Demo data wiped.'))
            return

        year = date.today().year
        lt, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'is_active': True, 'default_annual_days': Decimal('30')})

        manager_user = User.objects.create_user(username='demo.manager', password='DemoPass123!')
        manager = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}MANAGER', full_name='Layla Al-Zahrani (Manager)',
            is_active=True, user=manager_user)
        report = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}REPORT', full_name='Yousef Al-Qahtani (Report)',
            is_active=True, main_manager=manager)
        LeaveEntitlement.objects.create(employee=report, leave_type=lt, year=year, entitled_days=Decimal('30'))

        self.stdout.write(self.style.SUCCESS(
            f'Seeded manager-leave demo for year {year}: log in as demo.manager / DemoPass123!, '
            f'open My Profile, and use the My Team section to log leave for {report.full_name}.'
        ))
