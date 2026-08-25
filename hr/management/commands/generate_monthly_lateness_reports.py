"""Monthly lateness-to-absence consequence report - run daily via cron:
    python manage.py generate_monthly_lateness_reports
Only actually does anything on the last calendar day of the month (see
hr/lateness_report_services.py:is_last_day_of_month) - safe to run daily
so it doesn't depend on cron being configured for a specific date, which
varies by month (28th/29th/30th/31st).

Cron scheduling: must run strictly before 21:00 UTC (00:00 Riyadh) to
still see "today" as the last day in local time - see
hr/lateness_report_services.py's module docstring for the full
UTC-vs-local-date explanation. Recommended: schedule around 20:30 UTC
(23:30 Riyadh) to catch as much of the day's late arrivals as safely
possible before the window closes.

Manual recovery (a scheduled run was missed):
    python manage.py generate_monthly_lateness_reports --skip-last-day-check --target-month 2026-08-01
Always pass --target-month explicitly for a recovery - without it, the
command derives the month from today's date, which produces a wrong,
permanently-locked partial report if run in a later month than the one
being recovered.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from hr.lateness_report_services import generate_monthly_lateness_reports, is_last_day_of_month


class Command(BaseCommand):
    help = ('Generate month-end lateness-to-absence reports and email them to affected '
            "employees. Only acts on the last day of the month - safe to run daily via cron. "
            "See this command's module docstring for the UTC cron scheduling constraint.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-last-day-check', action='store_true',
            help='Bypass the last-day-of-month gate for a deliberate manual recovery. '
                 'Always combine with --target-month.')
        parser.add_argument(
            '--target-month', type=str, default=None,
            help='YYYY-MM-DD (day is ignored) - the month to generate for during a manual '
                 'recovery. Required alongside --skip-last-day-check.')

    def handle(self, *args, **options):
        skip_check = options['skip_last_day_check']
        target_month_str = options['target_month']

        if skip_check and not target_month_str:
            raise CommandError(
                '--skip-last-day-check requires --target-month so the correct month is '
                "recovered - without it, the command would derive the month from today's "
                'date and generate a wrong, permanently-locked partial report.')

        target_month = None
        if target_month_str:
            try:
                target_month = datetime.date.fromisoformat(target_month_str)
            except ValueError:
                raise CommandError(f'--target-month must be YYYY-MM-DD, got {target_month_str!r}')

        today = timezone.localtime(timezone.now()).date()
        if not skip_check and not is_last_day_of_month(today):
            self.stdout.write('Not the last day of the month - nothing to do.')
            return

        count = generate_monthly_lateness_reports(
            today, _skip_last_day_check=skip_check, target_month=target_month)
        label_date = target_month or today
        self.stdout.write(self.style.SUCCESS(
            f'Generated {count} monthly lateness report(s) for {label_date.strftime("%B %Y")}.'))
