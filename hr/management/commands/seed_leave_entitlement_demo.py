"""Seed local demo data for manually verifying the Annual Leave location
baselines + HR exception overrides feature:

- Confirms the 'annual' LeaveType has a Site default (45) distinct from its
  Office default (30) — editable from Leave Types, not hardcoded.
- DEMO-LEAVEEXC-OFFICE: an Office employee (30-day baseline).
- DEMO-LEAVEEXC-SITE-GRANT: a Site employee (45-day baseline) with a +5
  HR-granted exception day bank (50 available total) — open their Leave
  Summary to see the baseline stay the anchor number, with the exception
  shown as its own line. Has a login (see below) so you can also see it from
  the employee's own My Profile page.
- DEMO-LEAVEEXC-SITE-HELD: a Site employee with a 47-day leave request that
  exceeds their 45-day baseline — held (not blocked), routed through the
  normal approver roster with a red "Exceeds balance" warning. Also has a
  login.

Both Site employees are linked to a login account (password ``DemoPass123!``)
so you can see their own Leave Balance section on My Profile, not just the
HR-facing pages.

Safe to re-run: everything here is tagged with a "DEMO-LEAVEEXC-" iqama
prefix and upserted, never duplicated. Run
``python manage.py seed_leave_entitlement_demo --wipe`` to remove it.

Run locally: ``python manage.py seed_leave_entitlement_demo``
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from hr.models import Employee, LeaveType, LeaveEntitlement

User = get_user_model()
TAG_PREFIX = 'DEMO-LEAVEEXC-'


class Command(BaseCommand):
    help = 'Seed (or --wipe) demo data for the Annual Leave location baselines + exception overrides feature.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Remove the demo data instead of creating it.')

    def handle(self, *args, **options):
        existing = Employee.objects.filter(iqama_number__startswith=TAG_PREFIX)
        if existing.exists():
            existing.delete()
            self.stdout.write(f'Removed {existing.count()} existing demo employee(s).')
        User.objects.filter(username__in=['demo.sami', 'demo.hind']).delete()

        if options['wipe']:
            self.stdout.write(self.style.SUCCESS('Demo data wiped.'))
            return

        year = date.today().year

        lt, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'is_active': True,
            'default_annual_days': Decimal('30'),
            'site_default_annual_days': Decimal('45'),
        })

        actor = User.objects.filter(is_superuser=True).first()

        office_emp = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}OFFICE', full_name='Omar Al-Faisal (Office)',
            work_location='office', is_active=True)
        LeaveEntitlement.objects.create(
            employee=office_emp, leave_type=lt, year=year,
            entitled_days=lt.default_days_for('office'))

        sami_user = User.objects.create_user(username='demo.sami', password='DemoPass123!')
        grant_emp = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}SITE-GRANT', full_name='Sami Al-Harbi (Site, granted)',
            work_location='site', is_active=True, user=sami_user)
        LeaveEntitlement.objects.create(
            employee=grant_emp, leave_type=lt, year=year,
            entitled_days=lt.default_days_for('site'))
        from hr.leave_approval_services import grant_exception_days
        grant_exception_days(
            employee=grant_emp, leave_type=lt, year=year, days=Decimal('5'),
            granted_by=actor, reason='DEMO: worked through Eid holidays.')

        hind_user = User.objects.create_user(username='demo.hind', password='DemoPass123!')
        held_emp = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}SITE-HELD', full_name='Hind Al-Otaibi (Site, over balance)',
            work_location='site', is_active=True, user=hind_user)
        LeaveEntitlement.objects.create(
            employee=held_emp, leave_type=lt, year=year,
            entitled_days=lt.default_days_for('site'))
        from hr.leave_approval_services import submit_leave_request
        held_request = submit_leave_request(
            employee=held_emp, leave_type=lt,
            start_date=date(year, 1, 1), end_date=date(year, 2, 16),  # 47 days, over the 45-day Site baseline
            employee_reason='DEMO: extended family leave.', created_by=actor)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded demo data for year {year}:\n"
            f"  - {office_emp.full_name}: Office, {lt.default_days_for('office')}-day baseline. No login.\n"
            f"  - {grant_emp.full_name}: Site, {lt.default_days_for('site')}-day baseline + 5 exception days "
            f"granted (open /hr/{grant_emp.pk}/leave/, or log in as demo.sami / DemoPass123! and check My Profile).\n"
            f"  - {held_emp.full_name}: Site, {lt.default_days_for('site')}-day baseline, a 47-day request is "
            f"held, waiting on the normal approvers with a red 'Exceeds balance' warning "
            f"(open /hr/leave-requests/{held_request.pk}/, or log in as demo.hind / DemoPass123!)."
        ))
