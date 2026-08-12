"""Seed local demo data covering every state of the leave/attendance-exception
edit, delete, and revoke feature — for manual testing by both an employee and
an HR/manager reviewer.

Accounts created (all password DemoPass123!):
- demo.edr.employee  — the employee everything happens to/for.
- demo.edr.manager   — Fatima's direct manager (org-chart link); also the
                       assigned decider for her attendance exceptions.
- demo.edr.approver  — holds LeaveDashboardAccess (decides her leave requests).
- demo.edr.approver2 — a second LeaveDashboardAccess holder, needed to make
                       the partial-decision/Cancel scenarios below (6-7)
                       reproducible regardless of what other approvers
                       already exist in this dev database.
- demo.edr.hr        — Super Admin (override/revoke access).

What's seeded, all in September/October 2026 so nothing overlaps:

Leave Requests (self-submitted unless noted):
  1. Pending, no decision yet (Sep 1-2)        -> Edit + Cancel for the employee
     (Cancel with no reason required — nothing has happened yet)
  2. Approved, with an HR message (Sep 5-6)    -> Request Revoke + "1 message from HR"
  3. Approved, revoke already requested (Sep 10-11) -> "awaiting review" + shows in HR's queue
  4. Revoked directly by HR (Sep 15-16)        -> Revoked badge + reason, both sides
  5. Disapproved, salary deduction (Sep 20-21) -> salary-deduction warning
  6. Pending, one of two approvers already decided (Sep 22-23) -> Cancel Request
     still available (Edit has closed, Request Revoke hasn't opened yet —
     this is the gap Cancel exists for), but now a reason is required and
     the approver who decided gets notified
  7. Cancelled by the employee after one approver had decided (Sep 24) ->
     Cancelled badge + reason, visible on My Profile AND in the approver's
     queue History / detail page
  8-10. Three PENDING requests logged by the manager on the employee's behalf
       (Sep 25-26, Sep 28-29, Oct 1-2) -> Edit/Cancel on the manager's Reporting
       Structure card, and the "+1 more" collapse (2 visible, 1 tucked away)

Attendance Exceptions (self-submitted, manager = demo.edr.manager) — these
still use the original Edit/Delete pair: a single-decider domain has no
partial-approval limbo state, so there was never a gap for Cancel to fill:
  11. Pending (Sep 3)                          -> Edit/Delete for the employee
  12. Approved (Sep 7)                         -> Request Revoke
  13. Approved, revoke already requested (Sep 12) -> shows in the manager's queue
  14. Revoked directly by HR (Sep 17)           -> Revoked badge + reason

Safe to re-run: tagged with a "DEMO-EDR-" iqama prefix and the demo.edr.*
usernames, deleted and recreated each time. Run with --wipe to remove it.
"""
from datetime import date, time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from accounts.models import Role
from hr.models import (
    Employee, LeaveType, LeaveEntitlement, LeaveDashboardAccess,
    LeaveRequest, LeaveRevokeRequest, AttendanceExceptionRevokeRequest,
)

User = get_user_model()
TAG_PREFIX = 'DEMO-EDR-'
USERNAMES = ['demo.edr.employee', 'demo.edr.manager', 'demo.edr.approver', 'demo.edr.approver2', 'demo.edr.hr']


class Command(BaseCommand):
    help = 'Seed (or --wipe) demo data covering every state of the edit/delete/revoke feature.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Remove the demo data instead of creating it.')

    def handle(self, *args, **options):
        Employee.objects.filter(iqama_number__startswith=TAG_PREFIX).delete()
        User.objects.filter(username__in=USERNAMES).delete()

        if options['wipe']:
            self.stdout.write(self.style.SUCCESS('Demo data wiped.'))
            return

        from hr.leave_approval_services import (
            submit_leave_request, record_approver_decision, revoke_leave_request, request_leave_revoke,
            cancel_leave_request,
        )
        from hr.attendance_exception_services import (
            submit_attendance_exception, decide_attendance_exception,
            revoke_attendance_exception, request_attendance_exception_revoke,
        )

        year = 2026
        lt, _ = LeaveType.objects.update_or_create(code='annual', defaults={
            'name': 'Annual', 'is_active': True, 'default_annual_days': Decimal('30')})

        manager_user = User.objects.create_user(
            username='demo.edr.manager', password='DemoPass123!', first_name='Ahmad', last_name='Al-Otaibi')
        manager = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}MANAGER', full_name='Ahmad Al-Otaibi (Manager)',
            is_active=True, user=manager_user)

        emp_user = User.objects.create_user(
            username='demo.edr.employee', password='DemoPass123!', first_name='Fatima', last_name='Al-Harbi')
        emp = Employee.objects.create(
            iqama_number=f'{TAG_PREFIX}EMPLOYEE', full_name='Fatima Al-Harbi (Employee)',
            is_active=True, user=emp_user, main_manager=manager)
        LeaveEntitlement.objects.create(employee=emp, leave_type=lt, year=year, entitled_days=Decimal('60'))

        approver_user = User.objects.create_user(
            username='demo.edr.approver', password='DemoPass123!', first_name='Sara', last_name='Al-Qahtani')
        LeaveDashboardAccess.objects.create(user=approver_user, is_active=True)

        approver2_user = User.objects.create_user(
            username='demo.edr.approver2', password='DemoPass123!', first_name='Yousef', last_name='Al-Dosari')
        LeaveDashboardAccess.objects.create(user=approver2_user, is_active=True)

        hr_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        hr_user = User.objects.create_user(
            username='demo.edr.hr', password='DemoPass123!', first_name='Demo', last_name='HR')
        hr_user.role = hr_role
        hr_user.save(update_fields=['role'])

        def decide_all(req, decision, comment=''):
            # Reconciliation now requires EVERY designated approver to agree
            # (see hr.leave_approval_services._reconcile) — this dev database
            # may already have other real/demo LeaveDashboardAccess holders
            # beyond demo.edr.approver, so decide on behalf of all of them,
            # not just the one this script created. The comment (if any) is
            # attached to only the first decision, matching how a real
            # decision-with-message would only come from one approver.
            req.refresh_from_db()
            for i, approver in enumerate(list(req.pending_approvers())):
                record_approver_decision(req, approver, decision, comment=(comment if i == 0 else ''))
            req.refresh_from_db()

        def decide_one(req, approver, decision, comment=''):
            # Decides with exactly ONE approver, deliberately leaving any
            # others still pending — used for the partial-decision/Cancel
            # scenarios below (6-7), which must NOT fully reconcile.
            req.refresh_from_db()
            record_approver_decision(req, approver, decision, comment=comment)
            req.refresh_from_db()

        # 1. Pending — self-submitted, still editable/cancellable.
        submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 9, 1), end_date=date(year, 9, 2),
            employee_reason='Family visit.', created_by=emp_user)

        # 2. Approved, with an HR message left at decision time.
        req2 = submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 9, 5), end_date=date(year, 9, 6),
            employee_reason='Short trip.', created_by=emp_user)
        decide_all(req2, 'approved', comment='Approved — enjoy your trip.')

        # 3. Approved, with a revoke request already pending review.
        req3 = submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 9, 10), end_date=date(year, 9, 11),
            employee_reason='Personal errand.', created_by=emp_user)
        decide_all(req3, 'approved')
        request_leave_revoke(req3, emp_user, 'Project deadline moved — can no longer take this leave.')

        # 4. Revoked directly by HR.
        req4 = submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 9, 15), end_date=date(year, 9, 16),
            employee_reason='Weekend trip.', created_by=emp_user)
        decide_all(req4, 'approved')
        revoke_leave_request(req4, hr_user, 'Coverage arranged — leave voided per the employee\'s own request.')

        # 5. Disapproved — salary deduction applies.
        req5 = submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 9, 20), end_date=date(year, 9, 21),
            employee_reason='Last-minute request.', created_by=emp_user)
        decide_all(req5, 'disapproved', comment='Too close to a critical delivery date.')

        # 6. Pending, one of two approvers already decided — Cancel is the
        # only withdrawal path left (Edit closed the moment approver_user
        # decided), and it now requires a reason since a decision exists.
        req6 = submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 9, 22), end_date=date(year, 9, 23),
            employee_reason='Awaiting the second approver.', created_by=emp_user)
        decide_one(req6, approver_user, 'approved')

        # 7. Cancelled by the employee after one approver had already
        # decided — stays visible as 'Cancelled' in History on both sides.
        req7 = submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 9, 24), end_date=date(year, 9, 24),
            employee_reason='Plans changed after the first approval.', created_by=emp_user)
        decide_one(req7, approver_user, 'approved')
        cancel_leave_request(req7, emp_user, 'Project got cancelled — no longer need this leave.')

        # 8-10. Three requests the manager logged on the employee's behalf, all still pending.
        for start_day, end_day in ((25, 26), (28, 29)):
            submit_leave_request(
                employee=emp, leave_type=lt, start_date=date(year, 9, start_day), end_date=date(year, 9, end_day),
                employee_reason='Logged by manager.', created_by=manager_user)
        submit_leave_request(
            employee=emp, leave_type=lt, start_date=date(year, 10, 1), end_date=date(year, 10, 2),
            employee_reason='Logged by manager.', created_by=manager_user)

        # 11. Pending attendance exception — self-submitted, still editable/deletable.
        submit_attendance_exception(
            employee=emp, event_date=date(year, 9, 3), event_start_time=time(9, 0),
            reason_category='site_visit', employee_comment='Client site visit.', created_by=emp_user)

        # 12. Approved attendance exception.
        exc10 = submit_attendance_exception(
            employee=emp, event_date=date(year, 9, 7), event_start_time=time(9, 30),
            reason_category='outside_meeting', employee_comment='Vendor meeting.', created_by=emp_user)
        decide_attendance_exception(exc10, manager_user, 'approved')

        # 13. Approved, with a revoke request already pending review.
        exc11 = submit_attendance_exception(
            employee=emp, event_date=date(year, 9, 12), event_start_time=time(10, 0),
            reason_category='site_visit', employee_comment='Follow-up visit.', created_by=emp_user)
        decide_attendance_exception(exc11, manager_user, 'approved')
        request_attendance_exception_revoke(exc11, emp_user, 'Meeting was cancelled last minute.')

        # 14. Revoked directly by HR.
        exc12 = submit_attendance_exception(
            employee=emp, event_date=date(year, 9, 17), event_start_time=time(9, 0),
            reason_category='site_visit', employee_comment='Site inspection.', created_by=emp_user)
        decide_attendance_exception(exc12, manager_user, 'approved')
        revoke_attendance_exception(exc12, hr_user, 'Verified the visit did not happen.')

        self.stdout.write(self.style.SUCCESS(
            'Seeded the edit/delete/revoke demo. All accounts use password DemoPass123!\n'
            '  - demo.edr.employee (Fatima Al-Harbi): My Profile shows all 7 leave-request states '
            '(including the Cancel-window and Cancelled states) and all 4 attendance-exception states.\n'
            '  - demo.edr.manager (Ahmad Al-Otaibi): My Profile -> My Reporting Structure shows 3 '
            'pending in-behalf requests (2 + "1 more"); Team Exceptions shows a pending revoke request.\n'
            '  - demo.edr.approver (Sara Al-Qahtani) / demo.edr.approver2 (Yousef Al-Dosari): the two '
            'designated approvers for Fatima\'s leave requests — both must agree to finalize.\n'
            '  - demo.edr.hr (Super Admin, override access): Leave Requests queue shows a pending '
            'revoke request, a revocable approved request, and a cancelled request in History; can '
            'decide revoke requests on both the Leave Requests queue and Team Exceptions.'
        ))
