"""Seed local dummy data for manually verifying recent fixes:

- 20 unread notifications for the super admin (my-work / navbar badge count).
- 5 sales call reports across different regions (Region column contrast).
- 3 employees with a mix of accumulative (Annual, Sick) and conditional
  (Marriage, Umrah) leave entitlements, at various balances (entitlement
  summary math).
- A leave-approval-workflow demo (designated approvers + pending/approved/
  disapproved leave requests).
- An attendance-exception-workflow demo: a 3-level management chain
  (DEMO-FIELDEMP -> DEMO-MIDMGR -> DEMO-HEADMGR), two secondary managers
  (DEMO-SECMGR1/DEMO-SECMGR2) with visibility/override rights over
  DEMO-FIELDEMP outside that formal chain, and pending/approved/expired/
  overdue-but-still-pending/overridden-by-secondary-manager
  AttendanceException scenarios for DEMO-FIELDEMP. Also links the
  pre-existing ali.sultan account (a super_admin User with no Employee
  profile, previously only usable as a leave-approver) to a real Employee
  profile and gives him a genuine direct report, DEMO-ALISDIRECT, with a
  pending AttendanceException — demonstrating that a main_manager with a
  proper Employee profile gets normal Approve/Reject controls, not just
  Override.

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
from hr.models import Employee, LeaveType, LeaveEntitlement, AttendanceException

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
        self._seed_attendance_exception_workflow()
        self._seed_leave_dashboard_access()

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
            code='annual', defaults={'name': 'Annual', 'default_annual_days': Decimal('30.0'),
                                     'is_accumulative': True})
        sick, _ = LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick', 'default_annual_days': Decimal('12.0'),
                                   'is_accumulative': False})
        marriage, _ = LeaveType.objects.get_or_create(
            code='marriage', defaults={'name': 'Marriage', 'default_annual_days': Decimal('3.0'),
                                       'is_accumulative': False})
        umrah, _ = LeaveType.objects.get_or_create(
            code='umrah', defaults={'name': 'Umrah Leave', 'default_annual_days': Decimal('2.0'),
                                    'is_accumulative': False})
        death_of_family_member, _ = LeaveType.objects.get_or_create(
            code='death_of_family_member', defaults={'name': 'Death Of Family Member',
                                                      'default_annual_days': Decimal('3.0'),
                                                      'is_accumulative': False})
        new_born, _ = LeaveType.objects.get_or_create(
            code='new_born', defaults={'name': 'New Born', 'default_annual_days': Decimal('3.0'),
                                       'is_accumulative': False})
        # Existing rows may predate is_accumulative, or predate the business-rule
        # change that made Sick conditional too — make sure all six are
        # actually flagged as such even if they already existed.
        LeaveType.objects.exclude(code='annual').update(is_accumulative=False)
        LeaveType.objects.filter(code='annual').update(is_accumulative=True)
        # Existing rows may also predate the exact name/day-count values below
        # (e.g. an old "Umrah"/15-day-Sick from before those were corrected) —
        # force them to the canonical values regardless of get_or_create above.
        CANONICAL_VALUES = {
            'annual': ('Annual', Decimal('30.0')),
            'sick': ('Sick', Decimal('12.0')),
            'marriage': ('Marriage', Decimal('3.0')),
            'umrah': ('Umrah Leave', Decimal('2.0')),
            'death_of_family_member': ('Death Of Family Member', Decimal('3.0')),
            'new_born': ('New Born', Decimal('3.0')),
        }
        leave_type_vars = {
            'annual': annual, 'sick': sick, 'marriage': marriage, 'umrah': umrah,
            'death_of_family_member': death_of_family_member, 'new_born': new_born,
        }
        for code, (name, days) in CANONICAL_VALUES.items():
            LeaveType.objects.filter(code=code).update(name=name, default_annual_days=days)
            # queryset.update() above bypasses LeaveType.save() (and its
            # in-memory instance), so keep these already-fetched objects in
            # sync too — they're reused just below as the single source of
            # truth for each demo employee's ENTITLED days, instead of a
            # second set of hardcoded literals that can silently drift from
            # the canonical values set here.
            lt = leave_type_vars[code]
            lt.name = name
            lt.default_annual_days = days

        year = timezone.localdate().year
        # Entitled days mirror each LeaveType's canonical default_annual_days
        # (set above) — sourced from the LeaveType objects themselves so this
        # can never drift from the canonical values the way two independent
        # hardcoded literals could. Only the "taken" balances below are
        # deliberately-chosen demo scenario data.
        default_entitled = {
            annual: annual.default_annual_days, sick: sick.default_annual_days,
            marriage: marriage.default_annual_days, umrah: umrah.default_annual_days,
            death_of_family_member: death_of_family_member.default_annual_days,
            new_born: new_born.default_annual_days,
        }
        employees_spec = [
            {'iqama': f'{DEMO_TAG}1001', 'name': f'{DEMO_TAG}Employee One',
             'entitled': dict(default_entitled),
             'taken': {annual: Decimal('5'), sick: Decimal('2'), marriage: Decimal('0'), umrah: Decimal('0'),
                       death_of_family_member: Decimal('0'), new_born: Decimal('0')}},
            {'iqama': f'{DEMO_TAG}1002', 'name': f'{DEMO_TAG}Employee Two',
             'entitled': dict(default_entitled),
             'taken': {annual: Decimal('20'), sick: Decimal('10'), marriage: Decimal('5'), umrah: Decimal('0'),
                       death_of_family_member: Decimal('0'), new_born: Decimal('0')}},
            {'iqama': f'{DEMO_TAG}1003', 'name': f'{DEMO_TAG}Employee Three',
             'entitled': dict(default_entitled),
             'taken': {annual: Decimal('30'), sick: Decimal('0'), marriage: Decimal('0'), umrah: Decimal('10'),
                       death_of_family_member: Decimal('0'), new_born: Decimal('0')}},
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
        # LeaveDashboardAccess is now the single merged roster: an active grant
        # here is both "can view the dashboard" AND "is a real approver on new
        # leave requests" (being a Super Admin, which aamna/ali also are,
        # grants viewing + override only — never approval authority by itself).
        granter = User.objects.filter(is_superuser=True).first()
        LeaveDashboardAccess.objects.update_or_create(user=aamna, defaults={'is_active': True, 'granted_by': granter})
        LeaveDashboardAccess.objects.update_or_create(user=ali, defaults={'is_active': True, 'granted_by': granter})
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

    # ─── Attendance exception workflow demo scenarios ──────────────
    def _seed_attendance_exception_workflow(self):
        """A 3-level management chain (DEMO-FIELDEMP -> DEMO-MIDMGR ->
        DEMO-HEADMGR), two secondary managers (DEMO-SECMGR1/DEMO-SECMGR2)
        assigned to DEMO-FIELDEMP outside that formal chain, and pending/
        approved/expired/overridden-by-secondary-manager AttendanceException
        scenarios for DEMO-FIELDEMP, created through the real service layer
        (hr.attendance_exception_services) rather than hand-set fields, same
        precedent as _seed_approval_workflow's use of record_approver_decision."""
        from django.contrib.auth.hashers import make_password
        from hr.attendance_exception_services import (
            submit_attendance_exception, decide_attendance_exception, expire_overdue_exceptions,
            override_attendance_exception,
        )

        head_user, _ = User.objects.get_or_create(
            username='demo.headmgr', defaults={
                'first_name': f'{DEMO_TAG}Head', 'last_name': 'Manager',
                'email': 'demo.headmgr@example.com',
                'role': Role.objects.filter(name='ai_head').first(), 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        mid_user, _ = User.objects.get_or_create(
            username='demo.midmgr', defaults={
                'first_name': f'{DEMO_TAG}Mid', 'last_name': 'Manager',
                'email': 'demo.midmgr@example.com', 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        field_user, _ = User.objects.get_or_create(
            username='demo.fieldemp', defaults={
                'first_name': f'{DEMO_TAG}Field', 'last_name': 'Employee',
                'email': 'demo.fieldemp@example.com', 'is_active': True,
                'password': make_password('DemoPass123!'),
            })

        head_emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}HEADMGR', defaults={
                'full_name': f'{DEMO_TAG}Head Manager', 'designation': 'AI Head',
                'is_active': True, 'user': head_user,
            })
        if head_emp.user_id != head_user.id:
            head_emp.user = head_user
            head_emp.save(update_fields=['user'])

        mid_emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}MIDMGR', defaults={
                'full_name': f'{DEMO_TAG}Mid Manager', 'designation': 'Team Lead',
                'is_active': True, 'user': mid_user, 'main_manager': head_emp,
            })
        if mid_emp.user_id != mid_user.id or mid_emp.main_manager_id != head_emp.id:
            mid_emp.user = mid_user
            mid_emp.main_manager = head_emp
            mid_emp.save(update_fields=['user', 'main_manager'])

        field_emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}FIELDEMP', defaults={
                'full_name': f'{DEMO_TAG}Field Employee', 'designation': 'Field Engineer',
                'is_active': True, 'user': field_user, 'main_manager': mid_emp,
            })
        if field_emp.user_id != field_user.id or field_emp.main_manager_id != mid_emp.id:
            field_emp.user = field_user
            field_emp.main_manager = mid_emp
            field_emp.save(update_fields=['user', 'main_manager'])

        self.stdout.write(self.style.SUCCESS(
            'Ensured management chain DEMO-FIELDEMP -> DEMO-MIDMGR -> DEMO-HEADMGR '
            '(logins: demo.fieldemp / demo.midmgr / demo.headmgr, password DemoPass123!).'))

        # Two secondary managers — outside the formal main_manager chain, but
        # with visibility/override rights over DEMO-FIELDEMP's requests
        # (Employee.secondary_managers, related_name='secondary_reports').
        sec1_user, _ = User.objects.get_or_create(
            username='demo.secmgr1', defaults={
                'first_name': f'{DEMO_TAG}Secondary', 'last_name': 'Manager One',
                'email': 'demo.secmgr1@example.com', 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        sec2_user, _ = User.objects.get_or_create(
            username='demo.secmgr2', defaults={
                'first_name': f'{DEMO_TAG}Secondary', 'last_name': 'Manager Two',
                'email': 'demo.secmgr2@example.com', 'is_active': True,
                'password': make_password('DemoPass123!'),
            })

        sec1_emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}SECMGR1', defaults={
                'full_name': f'{DEMO_TAG}Secondary Manager One', 'designation': 'Assistant Team Lead',
                'is_active': True, 'user': sec1_user,
            })
        if sec1_emp.user_id != sec1_user.id:
            sec1_emp.user = sec1_user
            sec1_emp.save(update_fields=['user'])

        sec2_emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}SECMGR2', defaults={
                'full_name': f'{DEMO_TAG}Secondary Manager Two', 'designation': 'Regional Coordinator',
                'is_active': True, 'user': sec2_user,
            })
        if sec2_emp.user_id != sec2_user.id:
            sec2_emp.user = sec2_user
            sec2_emp.save(update_fields=['user'])

        field_emp.secondary_managers.set([sec1_emp, sec2_emp])

        self.stdout.write(self.style.SUCCESS(
            'Ensured DEMO-SECMGR1/DEMO-SECMGR2 as DEMO-FIELDEMP\'s secondary managers '
            '(logins: demo.secmgr1 / demo.secmgr2, password DemoPass123!).'))

        now = timezone.now()

        # ─── Ali Sultan → Employee profile link + a genuine direct report ──
        # Bug-report fix: ali.sultan is a pre-existing User (super_admin,
        # created above in _seed_approval_workflow purely with a
        # LeaveDashboardAccess grant for the separate leave-approval
        # workflow) with NO linked hr.Employee record. main_manager is an
        # FK to Employee, not User —
        # an account with no Employee profile can structurally never be
        # anyone's main manager, so it always falls through to Override-only.
        # Give him a real Employee profile and a genuine direct report so
        # that exact "manager sees Override, not Approve/Reject" gap is
        # demonstrably closed. _seed_approval_workflow (which creates the
        # ali.sultan User) runs before this method, so get() is safe here.
        ali_user = User.objects.get(username='ali.sultan')
        ali_emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}ALISULTAN', defaults={
                'full_name': 'Ali Sultan', 'designation': 'Super Admin / Manager',
                'is_active': True, 'user': ali_user,
            })
        if ali_emp.user_id != ali_user.id:
            ali_emp.user = ali_user
            ali_emp.save(update_fields=['user'])

        ali_direct_user, _ = User.objects.get_or_create(
            username='demo.alisdirect', defaults={
                'first_name': f'{DEMO_TAG}Ali\'s', 'last_name': 'Direct Report',
                'email': 'demo.alisdirect@example.com', 'is_active': True,
                'password': make_password('DemoPass123!'),
            })
        ali_direct_emp, _ = Employee.objects.get_or_create(
            iqama_number=f'{DEMO_TAG}ALISDIRECT', defaults={
                'full_name': f'{DEMO_TAG}Ali\'s Direct Report', 'designation': 'Field Engineer',
                'is_active': True, 'user': ali_direct_user, 'main_manager': ali_emp,
            })
        if ali_direct_emp.user_id != ali_direct_user.id or ali_direct_emp.main_manager_id != ali_emp.id:
            ali_direct_emp.user = ali_direct_user
            ali_direct_emp.main_manager = ali_emp
            ali_direct_emp.save(update_fields=['user', 'main_manager'])

        self.stdout.write(self.style.SUCCESS(
            'Linked ali.sultan to an Employee profile ("Ali Sultan") and gave him a genuine '
            'direct report DEMO-ALISDIRECT (login: demo.alisdirect, password DemoPass123!) — '
            'this is the exact scenario for the "main manager sees Override, not Approve/Reject" '
            'bug report.'))

        # A genuinely pending request for ali_direct_emp, submitted through the
        # real service so main_manager snapshots to ali_emp — logging in as
        # ali.sultan and viewing the default "Direct Reports" tab should show
        # normal Approve/Reject controls on this row, not Override.
        if not AttendanceException.objects.filter(
                employee=ali_direct_emp, reason_category='site_visit', status='pending').exists():
            ali_pending_dt = now - timedelta(hours=4)
            submit_attendance_exception(
                employee=ali_direct_emp, event_date=ali_pending_dt.date(),
                event_start_time=ali_pending_dt.time(),
                reason_category='site_visit', created_by=ali_direct_user,
            )

        # 1. Pending — event a few hours ago, still inside the 24h decision
        #    window, so the manager UI's live countdown has something to show.
        pending_dt = now - timedelta(hours=3)
        if not AttendanceException.objects.filter(
                employee=field_emp, reason_category='site_visit', status='pending').exists():
            submit_attendance_exception(
                employee=field_emp, event_date=pending_dt.date(), event_start_time=pending_dt.time(),
                reason_category='site_visit', created_by=field_user,
            )

        # 2. Approved, reason_category='other' with a custom_reason —
        #    demonstrates the custom-reason path and the 'present' (excused)
        #    AttendanceRecord outcome. Decided well within the 24h window so
        #    decide_attendance_exception succeeds.
        approved_dt = now - timedelta(hours=15)
        approved_exc = AttendanceException.objects.filter(
            employee=field_emp, reason_category='other', status='approved').first()
        if not approved_exc:
            approved_exc = submit_attendance_exception(
                employee=field_emp, event_date=approved_dt.date(), event_start_time=approved_dt.time(),
                reason_category='other', custom_reason=f'{DEMO_TAG}Vendor site walkthrough ran long',
                created_by=field_user,
            )
            decide_attendance_exception(
                approved_exc, mid_user, 'approved',
                note=f'{DEMO_TAG}Approved — excused for the vendor site walkthrough.',
            )

        # 3. Overdue/expired — submitted with an event far enough in the past
        #    that it's already past its 24h decision deadline, then genuinely
        #    expired via the real expire_overdue_exceptions() sweep (rather
        #    than hand-setting status='expired').
        overdue_dt = now - timedelta(days=3)
        if not AttendanceException.objects.filter(
                employee=field_emp, reason_category='outside_meeting').exists():
            submit_attendance_exception(
                employee=field_emp, event_date=overdue_dt.date(), event_start_time=overdue_dt.time(),
                reason_category='outside_meeting', created_by=field_user,
            )

        expired_count = expire_overdue_exceptions()

        # 3b. Overdue but STILL 'pending' — event >24h ago, yet never run
        #     through the expire sweep, demonstrating the new behavior that an
        #     overdue-but-undecided request stays in the active queue (with
        #     the row-overdue red highlight) and remains fully decidable by
        #     the real assigned manager, not forced to Override.
        #
        #     Deleted and recreated fresh on every run (rather than
        #     get_or_create'd) specifically because expire_overdue_exceptions()
        #     above runs unconditionally on every invocation of this command
        #     and sweeps ALL overdue pending rows system-wide — so last run's
        #     copy of this exact scenario would itself get caught by this
        #     run's sweep the moment it went stale. Deleting + recreating it
        #     AFTER the sweep call above guarantees this run's copy is always
        #     genuinely pending and never touched by expire_overdue_exceptions()
        #     until a future invocation's sweep runs before this block again.
        overdue_pending_reason = f'{DEMO_TAG}Overdue check-in — still pending, not yet swept by cron'
        AttendanceException.objects.filter(
            employee=field_emp, reason_category='other', custom_reason=overdue_pending_reason,
        ).delete()
        overdue_pending_dt = now - timedelta(hours=30)
        submit_attendance_exception(
            employee=field_emp, event_date=overdue_pending_dt.date(),
            event_start_time=overdue_pending_dt.time(),
            reason_category='other', custom_reason=overdue_pending_reason, created_by=field_user,
        )

        # 4. Overridden by a secondary manager — submitted normally (so
        #    main_manager still snapshots to mid_emp, same as scenario 1), but
        #    never decided by mid_emp. Instead a secondary manager
        #    (sec1_user) force-decides it via override_attendance_exception,
        #    demonstrating that a secondary manager's decision is recorded as
        #    an override (is_overridden=True, overridden_by=sec1_user), not a
        #    normal decide_attendance_exception. Uses 'site_visit' again but
        #    is distinguished from scenario 1's guard (which only matches
        #    status='pending') via is_overridden=True, so the two never
        #    collide across re-runs. Event a few hours ago — same timing as
        #    scenario 1 — so a genuine 'pending' request exists at the moment
        #    override_attendance_exception is called (it only accepts
        #    'pending' or 'expired').
        override_dt = now - timedelta(hours=3)
        overridden_exc = AttendanceException.objects.filter(
            employee=field_emp, reason_category='site_visit', is_overridden=True).first()
        if not overridden_exc:
            overridden_exc = submit_attendance_exception(
                employee=field_emp, event_date=override_dt.date(), event_start_time=override_dt.time(),
                reason_category='site_visit', created_by=field_user,
            )
            override_attendance_exception(
                overridden_exc, sec1_user, 'approved',
                reason=f'{DEMO_TAG}Overridden — main manager was on leave, approving as secondary manager.',
            )

        self.stdout.write(self.style.SUCCESS(
            f'Seeded 5 attendance exception scenarios for DEMO-FIELDEMP '
            f'(pending / approved / expired / overdue-but-still-pending / '
            f'overridden-by-secondary-manager; expired {expired_count} overdue row(s) '
            f'via the real expiry sweep), plus 1 pending scenario for DEMO-ALISDIRECT '
            f'under ali.sultan.'))

    # ─── Leave Dashboard Access demo grant ──────────────────────────
    def _seed_leave_dashboard_access(self):
        """Grants demo.midmgr (a plain manager User — role=None, NOT a
        super admin) LeaveDashboardAccess — the merged roster that grants
        BOTH dashboard visibility AND real approval authority on new leave
        requests — so the access model can be tested end-to-end without
        needing to promote anyone to Super Admin. demo.midmgr already
        exists (created in _seed_attendance_exception_workflow, which runs
        before this method), as does ali.sultan (created in
        _seed_approval_workflow), so both .get() calls below are safe."""
        from hr.models import LeaveDashboardAccess
        mid_user = User.objects.get(username='demo.midmgr')
        ali_user = User.objects.get(username='ali.sultan')
        LeaveDashboardAccess.objects.update_or_create(
            user=mid_user, defaults={'is_active': True, 'granted_by': ali_user})
        self.stdout.write(self.style.SUCCESS(
            'Granted demo.midmgr (a non-super-admin manager) Leave Dashboard Access — '
            'also making them a real approver on new leave requests — granted by ali.sultan. '
            'Log in as demo.midmgr (password DemoPass123!) to test.'))

    # ─── Cleanup ───────────────────────────────────────────────────
    def _wipe(self):
        from hr.models import (
            LeaveRecord, LeaveRequest, LeaveRequestApproval, LeaveRequestNote,
            LeaveDashboardAccess,
        )
        n, _ = LeaveDashboardAccess.objects.filter(
            user__username__in=['demo.midmgr', 'aamna.khan', 'ali.sultan']).delete()
        self.stdout.write(f'Deleted {n} demo leave dashboard access row(s).')
        n, _ = Notification.objects.filter(verb__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo notification row(s).')
        n, _ = SalesCallReport.objects.filter(company_name__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo sales call row(s).')
        n, _ = LeaveRequestNote.objects.filter(note__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo leave request note(s).')
        n, _ = LeaveRequest.objects.filter(employee_reason__startswith=DEMO_TAG).delete()
        self.stdout.write(f'Deleted {n} demo leave request(s).')
        n, _ = LeaveRecord.objects.filter(note=f'{DEMO_TAG}seed').delete()
        self.stdout.write(f'Deleted {n} demo leave record row(s).')
        # DEMO-ALISULTAN (ali_emp — ali.sultan's linked Employee profile) is
        # deliberately excluded here alongside DEMO-SUPERUSER: it's not new
        # demo *scenario* data, it's the standing fix for the reported
        # "main_manager with no Employee profile" gap. Wiping it would force
        # the next non-wipe run to recreate it from scratch, which it already
        # does idempotently (get_or_create) — but leaving it in place across
        # --wipe means the Ali Sultan identity behaves the same as the
        # superuser link above: a persistent fixture, not disposable scenario
        # data. (ali.sultan's *User* row is still deleted below, same as
        # before this change — Employee.user is SET_NULL, so that only nulls
        # out ali_emp.user until the next non-wipe run relinks it; ali_emp
        # itself is never touched.) DEMO-ALISDIRECT (the new demo direct
        # report created for this scenario) IS genuinely new scenario data and
        # is wiped normally via emp_qs below.
        emp_qs = Employee.objects.filter(iqama_number__startswith=DEMO_TAG).exclude(
            iqama_number__in=[f'{DEMO_TAG}SUPERUSER', f'{DEMO_TAG}ALISULTAN'])
        n, _ = LeaveEntitlement.objects.filter(employee__in=emp_qs).delete()
        self.stdout.write(f'Deleted {n} demo entitlement row(s).')
        n, _ = AttendanceException.objects.filter(employee__in=emp_qs).delete()
        self.stdout.write(f'Deleted {n} demo attendance exception row(s).')
        n, _ = emp_qs.delete()
        self.stdout.write(f'Deleted {n} demo employee row(s).')
        n, _ = User.objects.filter(username__startswith=f'{DEMO_TAG.lower()}salesrep-').delete()
        self.stdout.write(f'Deleted {n} demo sales-rep user account(s).')
        n, _ = User.objects.filter(username__in=['aamna.khan', 'ali.sultan']).delete()
        self.stdout.write(f'Deleted {n} demo approver user account(s).')
        n, _ = User.objects.filter(username__in=[
            'demo.headmgr', 'demo.midmgr', 'demo.fieldemp', 'demo.secmgr1', 'demo.secmgr2',
            'demo.alisdirect',
        ]).delete()
        self.stdout.write(f'Deleted {n} demo attendance-exception user account(s).')
