"""Auto-link Employee records to login accounts for the self-service portal.

Matches an unlinked Employee to a User by (in order):
  1. work_email / personal_email == user.email  (case-insensitive)
  2. iqama_number == user.employee_code

Only links when the target user isn't already linked to another employee.
Idempotent; safe to re-run. Use --dry-run to preview.

    python manage.py link_employee_users
    python manage.py link_employee_users --dry-run
"""
from django.core.management.base import BaseCommand

from accounts.models import User
from hr.models import Employee


class Command(BaseCommand):
    help = 'Auto-link employees to user accounts by email / employee_code.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be linked without saving.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        linked = 0
        skipped = 0

        for emp in Employee.objects.filter(user__isnull=True):
            user = self._find_user(emp)
            if not user:
                continue
            # Don't steal a user already linked to a different employee.
            if Employee.objects.filter(user=user).exclude(pk=emp.pk).exists():
                skipped += 1
                continue
            self.stdout.write(
                f'{"[dry] " if dry else ""}{emp.full_name} -> {user.username}')
            if not dry:
                emp.user = user
                emp.save(update_fields=['user'])
            linked += 1

        self.stdout.write(self.style.SUCCESS(
            f'{linked} linked{" (dry run)" if dry else ""}'
            f'{f", {skipped} skipped (user already linked)" if skipped else ""}.'))

    @staticmethod
    def _find_user(emp):
        for email in (emp.work_email, emp.personal_email):
            if email:
                u = User.objects.filter(email__iexact=email, is_active=True).first()
                if u:
                    return u
        if emp.iqama_number:
            u = User.objects.filter(
                employee_code=emp.iqama_number, is_active=True).first()
            if u:
                return u
        return None
