"""Safeguard: dump every project's proposal_reference to a timestamped file
before any migration rewrites them.

Writes a JSON snapshot (id, region, proposal_reference, project_name) to a
backups/ directory AND echoes the full snapshot to stdout so it is captured in
the deploy log (durable even on ephemeral filesystems like Render).

Run manually:  python manage.py dump_project_references
"""
import json
import os
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand

from projects.models import Project


class Command(BaseCommand):
    help = "Dump all project proposal_references to a timestamped JSON file (pre-migration safeguard)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dir', default=None,
            help='Directory to write the snapshot to (default: <BASE_DIR>/backups).')

    def handle(self, *args, **options):
        rows = list(
            Project.objects.select_related('region')
            .values('id', 'proposal_reference', 'project_name', 'region__code')
            .order_by('id'))

        snapshot = {
            'taken_at': datetime.now().isoformat(timespec='seconds'),
            'count': len(rows),
            'projects': rows,
        }

        out_dir = options['dir'] or os.path.join(str(settings.BASE_DIR), 'backups')
        try:
            os.makedirs(out_dir, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(out_dir, f'project_references_{stamp}.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            self.stdout.write(self.style.SUCCESS(
                f'Wrote reference snapshot ({len(rows)} projects) to {path}'))
        except OSError as e:
            # Don't let a write failure block the deploy — the stdout copy below
            # is still a durable record in the logs.
            self.stdout.write(self.style.WARNING(
                f'Could not write snapshot file ({e}); dumping to stdout only.'))

        # Durable copy in the deploy log.
        self.stdout.write('=== PROJECT REFERENCE SNAPSHOT (restore source) ===')
        self.stdout.write(json.dumps(snapshot, ensure_ascii=False))
        self.stdout.write('=== END PROJECT REFERENCE SNAPSHOT ===')
