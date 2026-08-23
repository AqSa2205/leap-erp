"""Monthly lateness-to-absence consequence report - run daily via cron:
    python manage.py generate_monthly_lateness_reports
Only actually does anything on the last calendar day of the month (see
hr/lateness_report_services.py:is_last_day_of_month) - safe to run daily
so it doesn't depend on cron being configured for a specific date, which
varies by month (28th/29th/30th/31st).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from hr.lateness_report_services import generate_monthly_lateness_reports, is_last_day_of_month


class Command(BaseCommand):
    help = ('Generate month-end lateness-to-absence reports and email them to affected '
            'employees. Only acts on the last day of the month - safe to run daily via cron.')

    def handle(self, *args, **options):
        today = timezone.localtime(timezone.now()).date()
        if not is_last_day_of_month(today):
            self.stdout.write('Not the last day of the month - nothing to do.')
            return
        count = generate_monthly_lateness_reports(today)
        self.stdout.write(self.style.SUCCESS(
            f'Generated {count} monthly lateness report(s) for {today.strftime("%B %Y")}.'))
