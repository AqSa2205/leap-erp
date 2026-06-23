"""Report file-storage usage by class, plus R2/S3 orphan detection.

Run this in the environment that holds the real files — i.e. on Render
(USE_R2=True), via the Render shell:

    python manage.py storage_report

Run locally it only sees the local media/ dir (near-empty in dev), so the
numbers are meaningful only against the production storage backend. The same
report is available in the app at Administration -> Storage (super admin).
"""
from django.core.management.base import BaseCommand

from dashboard.storage_report import build_storage_report, DEFAULT_QUOTA_GB


def human(n):
    f = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if f < 1024 or unit == 'TB':
            return f'{int(f):,} B' if unit == 'B' else f'{f:,.1f} {unit}'
        f /= 1024


class Command(BaseCommand):
    help = 'Summarise file-storage usage per class, with R2 orphan detection.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--quota-gb', type=float, default=DEFAULT_QUOTA_GB,
            help=f'Storage quota in GB for the %% readout (default {DEFAULT_QUOTA_GB}).')

    def handle(self, *args, **opts):
        report = build_storage_report(quota_gb=opts['quota_gb'])

        kind = 'S3/R2' if report['is_s3'] else 'local filesystem'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nStorage backend: {report["backend"]} ({kind})\n'))

        header = f'{"File class":<24}{"files":>8}{"size":>14}{"missing":>9}'
        self.stdout.write(header)
        self.stdout.write('-' * len(header))
        for row in report['classes']:
            self.stdout.write(
                f'{row["label"]:<24}{row["count"]:>8,}'
                f'{human(row["bytes"]):>14}{row["missing"]:>9,}')
        self.stdout.write('-' * len(header))
        self.stdout.write(self.style.SUCCESS(
            f'{"TOTAL (referenced)":<24}{report["total_files"]:>8,}'
            f'{human(report["total_bytes"]):>14}{report["total_missing"]:>9,}'))

        self.stdout.write(
            f'\n{human(report["total_bytes"])} of {report["quota_gb"]:g} GB quota '
            f'({report["pct_used"]:.1f}% used)')
        if report['total_missing']:
            self.stdout.write(self.style.WARNING(
                f'{report["total_missing"]} referenced file(s) missing from the backend.'))

        # Orphans
        self.stdout.write(self.style.MIGRATE_HEADING('\nOrphan scan'))
        if not report['is_s3']:
            self.stdout.write(
                'Note: on local filesystem; orphan numbers reflect the dev media/ dir.')
        if not report['orphan_count']:
            self.stdout.write(self.style.SUCCESS('No orphaned objects found.'))
        else:
            self.stdout.write(self.style.WARNING(
                f'{report["orphan_count"]:,} orphaned object(s), '
                f'{human(report["orphan_bytes"])} reclaimable '
                f'(no DB row references them):'))
            for ex in report['orphan_examples']:
                self.stdout.write(f'  {human(ex["bytes"]):>12}  {ex["key"]}')
            if report['orphan_count'] > len(report['orphan_examples']):
                shown = len(report['orphan_examples'])
                self.stdout.write(f'  ... and {report["orphan_count"] - shown:,} more')
            self.stdout.write('\nThis is a read-only report - nothing was deleted.')
