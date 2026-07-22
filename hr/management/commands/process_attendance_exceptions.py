"""Attendance exception workflow sweep — run every 15 minutes via cron:

    python manage.py process_attendance_exceptions

Two jobs in one pass:
1. Send each pending request's manager a one-time "event started, 24h to
   decide" reminder (send_pending_start_reminders).
2. Expire any pending request whose 24h decision window has passed,
   marking that day absent (expire_overdue_exceptions).

Both underlying functions are idempotent/safe to call every tick — see
hr/attendance_exception_services.py.
"""
from django.core.management.base import BaseCommand

from hr.attendance_exception_services import expire_overdue_exceptions, send_pending_start_reminders


class Command(BaseCommand):
    help = 'Send attendance-exception start reminders and expire overdue requests. Run every 15 minutes via cron.'

    def handle(self, *args, **options):
        reminded = send_pending_start_reminders()
        expired = expire_overdue_exceptions()
        self.stdout.write(self.style.SUCCESS(
            f'Sent {reminded} start reminder(s); expired {expired} overdue request(s).'))
