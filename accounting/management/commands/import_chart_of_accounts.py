"""Import / refresh the Chart of Accounts from the finance team's workbook.

The parsing lives in `accounting.chart_import`, shared with the upload screen at
/accounting/chart/import/ so the two cannot drift apart about what the workbook
means. Finance can publish a revision themselves from that screen; this command
is for scripted or bulk use.

Re-runnable: accounts are matched on `code`, so a second run updates names and
types rather than duplicating. Nothing is ever deleted — accounts missing from
the file can be deactivated with --deactivate-missing, which keeps historic
references intact.

Reads both .xlsx and legacy .xls, identified by content rather than extension.

    python manage.py import_chart_of_accounts "ERP final_Babar.xls"
    python manage.py import_chart_of_accounts <file> --dry-run
    python manage.py import_chart_of_accounts <file> --sheet changing
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting.chart_import import (
    ChartImportError, apply, parse_rows, plan, read_grid,
)
from accounting.management.commands._console import use_utf8_console
from accounting.models import Account


class Command(BaseCommand):
    help = 'Import or refresh the Chart of Accounts from a finance workbook.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='Path to the .xlsx or .xls file.')
        parser.add_argument('--sheet', default=None,
                            help='Sheet name. Defaults to the first sheet.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')
        parser.add_argument('--deactivate-missing', action='store_true',
                            help='Deactivate accounts absent from the file. '
                                 'Never deletes — history stays intact.')

    def handle(self, *args, **options):
        use_utf8_console()   # account names carry Arabic; cp1252 cannot print them

        path = options['path']
        try:
            sheet_name, rows = read_grid(path, sheet_name=options['sheet'])
        except FileNotFoundError as exc:
            raise CommandError(f'File not found: {path}') from exc
        except ChartImportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f'Reading {path} — sheet {sheet_name!r}')
        parsed, duplicates, bad_types = parse_rows(rows)
        if not parsed:
            raise CommandError('No account rows found — check the sheet layout.')

        for row, code, first_row in duplicates:
            self.stdout.write(self.style.WARNING(
                f'  ! row {row}: duplicate code {code} (first seen row {first_row}) — skipped'))
        for row, code, value in bad_types:
            self.stdout.write(self.style.WARNING(
                f'  ! row {row}: account {code} has unrecognised Internal Type '
                f'{value!r} — imported as {Account.TYPE_REGULAR}'))

        proposed = plan(parsed)
        self._report_change(proposed)

        if options['dry_run']:
            self._report_shape(parsed)
            self.stdout.write(self.style.WARNING('\nDry run — nothing written.'))
            return

        with transaction.atomic():
            result = apply(parsed, deactivate_missing=options['deactivate_missing'])

        self.stdout.write(self.style.SUCCESS(
            f"\nImported {len(parsed)} accounts: {result['created']} created, "
            f"{result['updated']} updated."))
        self.stdout.write(f"Parent links set: {result['linked']}.")
        if result['orphans']:
            self.stdout.write(self.style.WARNING(
                f"Top-level (no parent found): {', '.join(result['orphans'])}"))
        if result['deactivated']:
            self.stdout.write(self.style.WARNING(
                f"Deactivated {result['deactivated']} account(s) absent from the file."))
        self._report_shape(parsed)

    # ── reporting ────────────────────────────────────────────────────────
    def _report_change(self, proposed):
        """What this file does to the chart that already exists."""
        self.stdout.write(
            f"\n{proposed['total']} rows: {len(proposed['created'])} new, "
            f"{len(proposed['updated'])} changed, {len(proposed['unchanged'])} unchanged, "
            f"{len(proposed['missing'])} in the ERP but not in this file.")
        for item in proposed['created'][:15]:
            self.stdout.write(f"  + {item['code']:9s} {item['name'][:44]}")
        if len(proposed['created']) > 15:
            self.stdout.write(f"  … and {len(proposed['created']) - 15} more new")
        for change in proposed['updated'][:15]:
            bits = []
            if change['from_name'] != change['to_name']:
                bits.append(f"{change['from_name']!r} -> {change['to_name']!r}")
            if change['from_type'] != change['to_type']:
                bits.append(f"{change['from_type']} -> {change['to_type']}")
            self.stdout.write(f"  ~ {change['code']:9s} {'; '.join(bits)}")
        if len(proposed['updated']) > 15:
            self.stdout.write(f"  … and {len(proposed['updated']) - 15} more changed")
        for account in proposed['missing'][:15]:
            self.stdout.write(f"  ? {account.code:9s} {account.name[:44]} (not in file)")

    def _report_shape(self, parsed):
        by_type, by_class = {}, {}
        for item in parsed:
            by_type[item['internal_type']] = by_type.get(item['internal_type'], 0) + 1
            label = Account.CLASS_LABELS.get(item['code'][:1], 'Unclassified')
            by_class[label] = by_class.get(label, 0) + 1
        self.stdout.write('\nBy internal type:')
        for key in sorted(by_type):
            self.stdout.write(f'  {key:12s} {by_type[key]:4d}')
        self.stdout.write('By class:')
        for key in sorted(by_class):
            self.stdout.write(f'  {key:26s} {by_class[key]:4d}')
