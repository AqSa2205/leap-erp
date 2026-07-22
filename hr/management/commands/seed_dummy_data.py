"""Seed local dummy data for manually verifying recent fixes:

- 20 unread notifications for the super admin (my-work / navbar badge count).
- 5 sales call reports across different regions (Region column contrast).
- 3 employees with a mix of accumulative (Annual, Sick) and conditional
  (Marriage, Umrah) leave entitlements, at various balances (entitlement
  summary math).

Safe to re-run: everything this command creates is tagged with a "DEMO-"
prefix (iqama numbers, company names, notification verb), and re-running
only tops up notifications — sales calls and employees are upserted by their
tagged unique key, not duplicated. Run ``python manage.py seed_dummy_data
--wipe`` to remove everything this command created.

Run locally: ``python manage.py seed_dummy_data``
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User, Role
from notifications.models import Notification
from projects.models import Region
from reports.models import SalesCallReport
from hr.models import Employee, LeaveType, LeaveEntitlement

DEMO_TAG = 'DEMO-'


class Command(BaseCommand):
    help = 'Seed local dummy notifications, sales call reports, and employee leave data for manual QA.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true',
                            help='Delete dummy rows created by this command and exit.')

    def handle(self, *args, **opts):
        if opts['wipe']:
            self._wipe()
            return

        self._seed_notifications()
        self._seed_sales_calls()
        self._seed_leave_data()
        self._link_superuser_to_employee()
        self._seed_approval_workflow()

    # ─── Notifications ────────────────────────────────────────────
    def _seed_notifications(self):
        superadmin = (User.objects.filter(is_superuser=True).first()
                      or User.objects.filter(role__name='super_admin').first())
        if not superadmin:
            self.stderr.write(self.style.ERROR('No super admin account found — skipping notifications.'))
            return

        created = 0
        for i in range(1, 21):
            Notification.objects.create(
                recipient=superadmin,
                verb=f'{DEMO_TAG}sent you a test notification #{i}',
                description='Dummy notification seeded for QA — safe to mark read or delete.',
                level='info',
                target_url='',
                is_read=False,
            )
            created += 1
        self.stdout.write(self.style.SUCCESS(
            f'Created {created} unread notifications for {superadmin.username}.'))

    # ─── Sales call reports ───────────────────────────────────────
    def _seed_sales_calls(self):
        region_codes = ['UK', 'LNA', 'PA', 'GLB', 'UK']  # LNA = Leap Networks Arabia (Saudi ops)
        regions = {r.code: r for r in Region.objects.filter(code__in=set(region_codes))}
        missing = set(region_codes) - set(regions)
        if missing:
            self.stderr.write(self.style.WARNING(f'Regions not found, skipping: {sorted(missing)}'))

        today = timezone.localdate()
        created = 0
        for i, code in enumerate(region_codes, start=1):
            region = regions.get(code)
            if not region:
                continue
            rep, _ = User.objects.get_or_create(
                username=f'{DEMO_TAG.lower()}salesrep-{code.lower()}',
                defaults={
                    'first_name': f'{DEMO_TAG}Sales', 'last_name': code,
                    'email': f'demo.salesrep.{code.lower()}@example.com',
                    'region': region,
                    'role': Role.objects.filter(name='sales_rep').first(),
                    'is_active': True,
                },
            )
            if rep.region_id != region.id:
                rep.region = region
                rep.save(update_fields=['region'])

            _, was_created = SalesCallReport.objects.get_or_create(
                company_name=f'{DEMO_TAG}Company {code}-{i}',
                sales_rep=rep,
                defaults={
                    'call_date': today - timedelta(days=i),
                    'contact_name': f'{DEMO_TAG}Contact {i}',
                    'action_type': 'meeting',
                    'contact_type': 'direct',
                    'system_categories': 'cctv,servers',
                    'goal': 'requirement_gathering',
                    'comments': 'Dummy sales call seeded for QA.',
                },
            )
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f'Created {created} sales call report(s).'))

    # ─── Employees + leave entitlements/records ───────────────────
    def _seed_leave_data(self):
        annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual', 'default_annual_days': Decimal('30'),
                                     'is_accumulative': True})
        sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': Decimal('15'),
                                   'is_accumulative': False})
        marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': Decimal('5'),
                                       'is_accumulative': False})
        umrah, _ = LeaveType.objects.get_or_create(
            code='umrah', defaults={'name': 'Umrah', 'default_annual_days': Decimal('10'),
                                    'is_accumulative': False})
        # Existing rows may predate is_accumulative, or predate the business-rule
        # change that made Sick conditional too — make sure all three are
        # actually flagged as such even if they already existed.
        LeaveType.objects.filter(code__in=['marriage', 'umrah', 'sick'], is_accumulative=True).update(is_accumulative=False)

        year = timezone.localdate().year
        employees_spec = [
            {'iqama': f'{DEMO_TAG}1001', 'name': f'{DEMO_TAG}Employee One',
             'entitled': {annual: Decimal('30'), sick: Decimal('15'), marriage: Decimal('5'), umrah: Decimal('10')},
             'taken': {annual: Decimal('5'), sick: Decimal('2'), marriage: Decimal('0'), umrah: Decimal('0')}},
            {'iqama': f'{DEMO_TAG}1002', 'name': f'{DEMO_TAG}Employee Two',
             'entitled': {annual: Decimal('30'), sick: Decimal('15'), marriage: Decimal('5'), umrah: Decimal('10')},
             'taken': {annual: Decimal('20'), sick: Decimal('10'), marriage: Decimal('5'), umrah: Decimal('0')}},
            {'iqama': f'{DEMO_TAG}1003', 'name': f'{DEMO_TAG}Employee Three',
             'entitled': {annual: Decimal('30'), sick: Decimal('15'), marriage: Decimal('5'), umrah: Decimal('10')},
             'taken': {annual: Decimal('30'), sick: Decimal('0'), marriage: Decimal('0'), umrah: Decimal('10')}},
        ]

        created_employees = 0
        for spec in employees_spec:
            emp, was_created = Employee.objects.get_or_create(
                iqama_number=spec['iqama'],
                defaults={
                    'full_name': spec['name'],
                    'designation': 'QA Test Employee',
                    'joining_date': date(year - 1, 1, 1),
                    'is_active': True,
                },
            )
            if was_created:
                created_employees += 1

            for leave_type, entitled_days in spec['entitled'].items():
                LeaveEntitlement.objects.update_or_create(
                    employee=emp, leave_type=leave_type, year=year,
                    defaults={'entitled_days': entitled_days},
                )

            # Record "taken" leave as an actual LeaveRecord so entitled_days -
            # taken_days (derived from records) lines up with the target balance.
            from hr.models import LeaveRecord
            LeaveRecord.objects.filter(employee=emp, note=f'{DEMO_TAG}seed').delete()
            for leave_type, taken_days in spec['taken'].items():
                if taken_days <= 0:
                    continue
                start = date(year, 3, 1)
                end = start + timedelta(days=int(taken_days) - 1)
                LeaveRecord.objects.create(
                    employee=emp, leave_type=leave_type,
                    start_date=start, end_date=end, days=taken_days,
                    note=f'{DEMO_TAG}seed',
                )

        self.stdout.write(self.style.SUCCESS(
            f'Created {created_employees} employee(s); entitlements/records seeded for {year}.'))

    # ─── Superuser profile link ────────────────────────────────────
    def _link_superuser_to_employee(self):
        superadmin = User.objects.filter(is_superuser=True).first()
        if not superadmin:
            self.stderr.write(self.style.ERROR('No superuser found — skipping profile link.'))
            return
        if getattr(superadmin, 'employee_profile', None):
            self.stdout.write(f'{superadmin.username} already linked to an employee profile.')
            return
        emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}SUPERUSER',
            defaults={'full_name': superadmin.get_full_name() or superadmin.username,
                     'designation': 'Super Admin', 'is_active': True, 'user': superadmin},
        )
        if emp.user_id != superadmin.id:
            emp.user = superadmin
            emp.save(update_fields=['user'])
        self.stdout.write(self.style.SUCCESS(f'Linked {superadmin.username} to employee profile "{emp.full_name}".'))

    # ─── Approval workflow demo scenarios ──────────────────────────
    def _seed_approval_workflow(self):
        from django.core.files.base import ContentFile
        from django.contrib.auth.hashers import make_password
        from hr.models import LeaveDashboardAccess, LeaveRequest, LeaveRequestApproval, LeaveRequestNote

        aamna, _ = User.objects.get_or_create(
            username='aamna.khan', defaults={
                'first_name': 'Aamna', 'last_name': 'Khan', 'email': 'aamna.khan@example.com',
                'role': Role.objects.filter(name='super_admin').first(), 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        ali, _ = User.objects.get_or_create(
            username='ali.sultan', defaults={
                'first_name': 'Ali', 'last_name': 'Sultan', 'email': 'ali.sultan@example.com',
                'role': Role.objects.filter(name='super_admin').first(), 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        # LeaveApprover was merged into LeaveDashboardAccess — one roster grants
        # both dashboard visibility and approval authority.
        LeaveDashboardAccess.objects.get_or_create(user=aamna)
        LeaveDashboardAccess.objects.get_or_create(user=ali)
        self.stdout.write(self.style.SUCCESS('Ensured Aamna Khan and Ali Sultan exist as designated leave approvers (login: aamna.khan / ali.sultan, password DemoPass123!).'))

        conditional_types = list(LeaveType.objects.filter(is_accumulative=False)[:3])
        if not conditional_types:
            self.stderr.write(self.style.WARNING('No conditional leave types found — run _seed_leave_data first.'))
            return
        demo_employees = list(Employee.objects.filter(iqama_number__startswith=DEMO_TAG).exclude(
            iqama_number=f'{DEMO_TAG}SUPERUSER'))
        if not demo_employees:
            self.stderr.write(self.style.WARNING('No demo employees found — run _seed_leave_data first.'))
            return

        year = timezone.localdate().year
        scenarios = [
            ('pending', demo_employees[0], conditional_types[0], date(year, 9, 1), date(year, 9, 2), []),
            ('approved', demo_employees[1], conditional_types[1 % len(conditional_types)], date(year, 9, 5), date(year, 9, 6),
             [('aamna', 'approved'), ('ali', 'approved')]),
            ('disapproved', demo_employees[2], conditional_types[2 % len(conditional_types)], date(year, 9, 10), date(year, 9, 11),
             [('aamna', 'disapproved')]),
        ]
        approver_map = {'aamna': aamna, 'ali': ali}
        created = 0
        for label, emp, lt, start, end, decisions in scenarios:
            req, was_created = LeaveRequest.objects.get_or_create(
                employee=emp, leave_type=lt, start_date=start, end_date=end,
                defaults={'employee_reason': f'{DEMO_TAG}{label} scenario for QA', 'created_by': aamna},
            )
            if not was_created:
                continue
            req.document.save(f'{DEMO_TAG}{label}-doc.txt', ContentFile(b'Dummy supporting document for QA.'), save=True)
            for approver_key in ('aamna', 'ali'):
                LeaveRequestApproval.objects.get_or_create(leave_request=req, approver=approver_map[approver_key])
            LeaveRequestNote.objects.create(leave_request=req, author=aamna, note=f'{DEMO_TAG}Seeded {label} scenario.')
            from hr.leave_approval_services import record_approver_decision
            for approver_key, decision in decisions:
                record_approver_decision(req, approver_map[approver_key], decision, comment=f'{DEMO_TAG}auto-decision')
            created += 1
        self.stdout.write(self.style.SUCCESS(f'Created {created} demo leave request(s) (pending/approved/disapproved).'))

    # ─── Cleanup ───────────────────────────────────────────────────
    def _wipe(self):
        from hr.models import LeaveRecord, LeaveRequest, LeaveRequestApproval, LeaveRequestNote, LeaveDashboardAccess
        n, _ = Notification.objects.filter(verb__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo notification row(s).')
        n, _ = SalesCallReport.objects.filter(company_name__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo sales call row(s).')
        n, _ = LeaveRequestNote.objects.filter(note__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo leave request note(s).')
        n, _ = LeaveRequest.objects.filter(employee_reason__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo leave request(s).')
        n, _ = LeaveDashboardAccess.objects.filter(user__username__in=['aamna.khan', 'ali.sultan']).delete()
        self.stdout.write(f'Deleted {n} demo leave approver row(s).')
        n, _ = LeaveRecord.objects.filter(note=f'{DEMO_TAG}seed').delete()
        self.stdout.write(f'Deleted {n} demo leave record row(s).')
        emp_qs = Employee.objects.filter(iqama_number__startswith=DEMO_TAG).exclude(iqama_number=f'{DEMO_TAG}SUPERUSER')
        n, _ = LeaveEntitlement.objects.filter(employee__in=emp_qs).delete()
        self.stdout.write(f'Deleted {n} demo entitlement row(s).')
        n, _ = emp_qs.delete()
        self.stdout.write(f'Deleted {n} demo employee row(s).')
        n, _ = User.objects.filter(username__startswith=f'{DEMO_TAG.lower()}salesrep-').delete()
        self.stdout.write(f'Deleted {n} demo sales-rep user account(s).')
        n, _ = User.objects.filter(username__in=['aamna.khan', 'ali.sultan']).delete()
        self.stdout.write(f'Deleted {n} demo approver user account(s).')
