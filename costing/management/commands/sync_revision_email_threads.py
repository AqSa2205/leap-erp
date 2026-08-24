"""Costing-revision email thread sync — run every 15 minutes via cron:

    python manage.py sync_revision_email_threads

Pulls new messages (client replies, mainly) into every RevisionEmailThread
from Microsoft Graph, using the same sync_thread() the manual "Refresh"
button in the UI calls, so both paths behave identically. Safe to call
repeatedly — messages are matched by graph_message_id, which is unique.
"""
from django.core.management.base import BaseCommand

from costing.graph_thread import GraphThreadError, sync_thread
from costing.models import RevisionEmailThread


class Command(BaseCommand):
    help = 'Pull new reply-thread messages for every sent costing revision. Run every 15 minutes via cron.'

    def handle(self, *args, **options):
        threads = list(RevisionEmailThread.objects.all())
        total_new = 0
        failed = 0
        for thread in threads:
            try:
                total_new += sync_thread(thread)
            except GraphThreadError as exc:
                failed += 1
                self.stderr.write(self.style.WARNING(
                    f'Could not sync thread {thread.pk} ({thread.subject}): {exc}'))
        self.stdout.write(self.style.SUCCESS(
            f'Synced {len(threads)} thread(s): {total_new} new message(s), {failed} failed.'))
