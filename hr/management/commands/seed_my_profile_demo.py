"""Seed local demo data covering every leave/attendance-exception state
that can appear on My Profile, all on one employee, for fast manual
screenshotting/testing:

- DEMO-MYPROFILE-BOSS: the viewer's own manager (decides their exceptions).
- DEMO-MYPROFILE-VIEWER: log in as demo.viewer / DemoPass123! and open
  My Profile — this is the one to look at.
- DEMO-MYPROFILE-REPORT: a direct report of the viewer, with three leave
  requests logged BY the viewer (triggers the "+N more" collapse on the
  Direct Reports card).
- DEMO-MYPROFILE-GRANDCHILD: a report of DEMO-MYPROFILE-REPORT, i.e. two
  levels below the viewer (populates the downstream-chain expand control).

Leave requests on the viewer's own My Profile leave table, one of each:
  1. Pending, undecided                (Edit + Cancel, no warning)
  2. Pending, 1-of-2 approved           (Edit + Cancel, WITH reset warning)
  3. Fully approved                     (Request Revoke available)
  4. Approved + a pending revoke request (awaiting-review note)
  5. Revoked                            (Revoked badge)
  6. Cancelled                          (Cancelled badge)
  7. Disapproved                        (Disapproved badge)
  8. Backdated + pending                (Backdated badge)
  9. Exceeds balance (held)             (Exceeds balance badge)

Balance display: Annual has a +5 exception grant, Sick has a -3 one, so
the info-icon/tooltip breakdown has something to show for both directions.

Attendance exceptions: one pending, one approved, one backdated+pending.

Safe to re-run: tagged with a "DEMO-MYPROFILE-" iqama prefix and
upserted. Run ``python manage.py seed_my_profile_demo --wipe`` to remove.
"""
from datetime import date, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from hr.models import Employee, LeaveType, LeaveEntitlement, LeaveDashboardAccess

User = get_user_model()
TAG_PREFIX = 'DEMO-MYPROFILE-'
DEMO_USERNAMES = [
    'demo.viewer', 'demo.myprofile.boss', 'demo.myprofile.approver1', 'demo.myprofile.approver2',
]


class Command(BaseCommand):
    help = "Seed (or --wipe) demo data covering every My Profile leave/exception state."

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Remove the demo data instead of creating it.')

    def handle(self, *args, **options):
        existing = Employee.objects.filter(iqama_number__startswith=TAG_PREFIX)
        removed = existing.count()  # count BEFORE delete — afterwards it's always 0
        if removed:
            existing.delete()
            self.stdout.write(f'Removed {removed} existing demo employee(s).')
        User.objects.filter(username__in=DEMO_USERNAMES).delete()

        if options['wipe']:
            self.stdout.write(self.style.SUCCESS('Demo data wiped.'))
            return

        from hr.leave_approval_services import (
            submit_leave_request, record_approver_decision, cancel_leave_request,
            revoke_leave_request, request_leave_revoke, grant_exception_days,
        )
        from hr.attendance_exception_services import submit_attendance_exception, decide_attendance_exception

        year = date.today().year
        today = date.today()

        annual, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'is_active': True, 'default_annual_days': Decimal('60')})
        sick, _ = LeaveType.objects.update_or_create(code='sick', defaults={
            'name': 'Sick', 'is_active': True, 'default_annual_days': Decimal('15'),
            'requires_medical_certificate': True})
        marriage, _ = LeaveType.objects.update_or_create(code='marriage', defaults={
            'name': 'Marriage', 'is_active': True, 'default_annual_days': Decimal('3'),
            'is_accumulative': False})

        boss_user = User.objects.create_user(username='demo.myprofile.boss', password='DemoPass123!')
        boss = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}BOSS', full_name='Demo Boss', is_active=True, user=boss_user)

        viewer_user = User.objects.create_user(username='demo.viewer', password='DemoPass123!')
        viewer = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}VIEWER', full_name='Demo Viewer', is_active=True,
            work_location='site',  # Site gets hold-instead-of-block by default — needed for scenario 9.
            user=viewer_user, main_manager=boss)
        LeaveEntitlement.objects.create(employee=viewer, leave_type=annual, year=year, entitled_days=Decimal('60'))
        LeaveEntitlement.objects.create(employee=viewer, leave_type=sick, year=year, entitled_days=Decimal('15'))
        LeaveEntitlement.objects.create(employee=viewer, leave_type=marriage, year=year, entitled_days=Decimal('3'))

        report = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}REPORT', full_name='Demo Direct Report', is_active=True,
            main_manager=viewer)
        LeaveEntitlement.objects.create(employee=report, leave_type=annual, year=year, entitled_days=Decimal('30'))

        Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}GRANDCHILD', full_name='Demo Downstream Report', is_active=True,
            main_manager=report)

        approver1 = User.objects.create_user(username='demo.myprofile.approver1', password='DemoPass123!')
        approver2 = User.objects.create_user(username='demo.myprofile.approver2', password='DemoPass123!')
        LeaveDashboardAccess.objects.create(user=approver1, is_active=True)
        LeaveDashboardAccess.objects.create(user=approver2, is_active=True)

        def _submit(leave_type, start_offset, days, reason):
            start = today + timedelta(days=start_offset)
            return submit_leave_request(
                employee=viewer, leave_type=leave_type, start_date=start,
                end_date=start + timedelta(days=days - 1), employee_reason=reason, created_by=viewer_user)

        def _decide_one(req, decision, comment=''):
            # The real roster may include more than just approver1/2 (any
            # other active LeaveDashboardAccess holder in this environment
            # is snapshotted too) — decide with exactly one arbitrary
            # approver, leaving the rest pending, for the "still pending,
            # partially decided" scenario.
            approval = req.approvals.filter(decision='pending').first()
            record_approver_decision(req, approval.approver, decision, comment=comment)

        def _decide_all(req, decision, comment=''):
            # Finalizing requires EVERY approver on the roster to decide,
            # not just the two created here — loop the actual roster.
            for approval in list(req.approvals.filter(decision='pending').select_related('approver')):
                record_approver_decision(req, approval.approver, decision, comment=comment)

        # 1. Pending, undecided.
        _submit(annual, 20, 2, 'Pending, no decision yet — demo scenario 1.')

        # 2. Pending, 1-of-N approved (unanimous consensus keeps it pending).
        req2 = _submit(annual, 25, 2, 'Pending, one approver has already decided — demo scenario 2.')
        _decide_one(req2, 'approved', comment='Looks good from my side.')

        # 3. Fully approved.
        req3 = _submit(annual, 30, 2, 'Fully approved — demo scenario 3.')
        _decide_all(req3, 'approved')

        # 4. Approved + a pending revoke request.
        req4 = _submit(annual, 35, 2, 'Approved, revoke requested — demo scenario 4.')
        _decide_all(req4, 'approved')
        request_leave_revoke(req4, viewer_user, 'Plans changed after approval — demo.')

        # 5. Revoked (direct, by someone with override access).
        req5 = _submit(annual, 40, 2, 'Approved then revoked — demo scenario 5.')
        _decide_all(req5, 'approved')
        revoke_leave_request(req5, boss_user, 'Applied for demo purposes.')

        # 6. Cancelled.
        req6 = _submit(annual, 45, 2, 'Cancelled by the employee — demo scenario 6.')
        cancel_leave_request(req6, viewer_user, 'No longer needed — demo.')

        # 7. Disapproved (unanimous decline).
        req7 = _submit(annual, 50, 2, 'Disapproved — demo scenario 7.')
        _decide_all(req7, 'disapproved', comment='Team is short-staffed that week.')

        # 8. Backdated + pending (start date already in the past).
        _submit(annual, -10, 2, 'Backdated — reported after it already started, demo scenario 8.')

        # 9. Exceeds balance (Marriage entitlement is only 3 days).
        req9 = _submit(marriage, 55, 5, 'Exceeds the Marriage balance — demo scenario 9 (held).')

        # Balance display: an addition on Annual, a reduction on Sick.
        grant_exception_days(
            employee=viewer, leave_type=annual, year=year, days=Decimal('5'),
            granted_by=boss_user, reason='Worked through a public holiday — demo grant.')
        grant_exception_days(
            employee=viewer, leave_type=sick, year=year, days=Decimal('-3'),
            granted_by=boss_user, reason='New-hire policy cap — demo deduction.')

        # Manager-logged: three requests the viewer logged for their direct report.
        for i in range(3):
            r = submit_leave_request(
                employee=report, leave_type=annual, start_date=today + timedelta(days=10 + i * 5),
                end_date=today + timedelta(days=11 + i * 5),
                employee_reason=f'Logged by manager — demo #{i + 1}.', created_by=viewer_user)
            if i == 0:
                _decide_all(r, 'approved')

        # Attendance exceptions: pending, approved, backdated+pending.
        submit_attendance_exception(
            employee=viewer, event_date=today, event_start_time=time(9, 15),
            reason_category='traffic', created_by=viewer_user)
        exc_approved = submit_attendance_exception(
            employee=viewer, event_date=today - timedelta(days=1), event_start_time=time(9, 5),
            reason_category='site_visit', created_by=viewer_user)
        decide_attendance_exception(exc_approved, boss_user, 'approved')
        submit_attendance_exception(
            employee=viewer, event_date=today - timedelta(days=7), event_start_time=time(9, 20),
            reason_category='traffic', created_by=viewer_user)

        self.stdout.write(self.style.SUCCESS(
            f'Seeded My Profile demo for year {year}.\n'
            f'Log in as demo.viewer / DemoPass123! and open My Profile.\n'
            f'Boss (decides exceptions, holds override access): demo.myprofile.boss / DemoPass123!\n'
            f'Leave requests cover: pending, pending+1-decided, approved, approved+revoke-requested,\n'
            f'revoked, cancelled, disapproved, backdated, and exceeds-balance (held).\n'
            f'Annual has a +5 exception grant; Sick has a -3 one (info-icon breakdown on both).\n'
            f'Direct Reports card shows 3 manager-logged requests for Demo Direct Report ("+1 more"),\n'
            f'plus one downstream report (Demo Downstream Report) for the expand control.\n'
            f'Attendance exceptions: one pending, one approved, one backdated.'
        ))
