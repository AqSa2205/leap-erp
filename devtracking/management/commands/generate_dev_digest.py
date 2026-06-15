from django.core.management.base import BaseCommand

from devtracking.ai import generate_admin_digest


class Command(BaseCommand):
    help = 'Generate the daily AI developer-progress digest.'

    def handle(self, *args, **options):
        dg = generate_admin_digest()
        self.stdout.write(self.style.SUCCESS(
            f'Generated digest #{dg.pk} ({dg.model_used or "fallback"})'))
