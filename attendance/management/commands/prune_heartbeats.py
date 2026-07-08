"""Delete old Heartbeat rows — the table grows by one row per machine per
minute, so keep only a recent window. AttendanceDay (the aggregate) is kept.

    python manage.py prune_heartbeats --days 30
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.models import Heartbeat


class Command(BaseCommand):
    help = 'Delete Heartbeat rows older than --days (default 30).'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='Keep heartbeats from the last N days (default 30).')

    def handle(self, *args, **options):
        days = max(1, options['days'])
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = Heartbeat.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(
            f'Pruned {deleted} heartbeat(s) older than {days} day(s).'))
