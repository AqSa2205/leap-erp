"""Pull Zoho Books' chart of accounts into the mapping table.

This deliberately does NOT touch the ERP's own chart. That chart is the
structure the finance team designed and Zoho's data is coded *into* it —
overwriting it from Zoho would destroy the very thing being built.

What this does instead is record every account that exists in Zoho, so each
one can be pointed at an ERP account. New Zoho accounts arrive unmapped, which
is a worklist item rather than a failure: a transaction that quietly vanishes
is far worse than one that shows up asking where it belongs.

    python manage.py sync_zoho_accounts
    python manage.py sync_zoho_accounts --dry-run
    python manage.py sync_zoho_accounts --automap   # propose code matches

Re-runnable: rows are matched on Zoho's account id, so existing mappings are
never disturbed.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting.management.commands._console import use_utf8_console
from accounting.models import Account, ZohoAccountMap
from accounting.zoho import ZohoClient, ZohoError


class Command(BaseCommand):
    help = "Sync Zoho Books' chart of accounts into the ERP mapping table."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')
        parser.add_argument('--automap', action='store_true',
                            help='Where a Zoho account code exactly matches an ERP '
                                 'account code, propose that mapping. Only fills '
                                 'blanks; never rewrites a mapping finance has set.')

    def handle(self, *args, **options):
        use_utf8_console()   # Zoho names are Arabic; cp1252 cannot print them
        try:
            client = ZohoClient.from_settings()
        except ZohoError as exc:
            raise CommandError(str(exc))

        self.stdout.write('Reading the chart of accounts from Zoho …')
        try:
            records = list(client.accounts())
        except ZohoError as exc:
            raise CommandError(f'Could not read Zoho: {exc}')

        if not records:
            raise CommandError('Zoho returned no accounts — check the organization ID.')

        if options['dry_run']:
            return self._report_plan(records)

        with transaction.atomic():
            created, updated = self._upsert(records)
            proposed = self._automap() if options['automap'] else 0

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(records)} Zoho account(s): {created} new, {updated} updated.'))
        if proposed:
            self.stdout.write(self.style.SUCCESS(
                f'{proposed} mapping(s) proposed by exact code match.'))
        self._report_state()

    def _upsert(self, records):
        created = updated = 0
        for record in records:
            _, was_created = ZohoAccountMap.objects.update_or_create(
                zoho_account_id=str(record.get('account_id')),
                defaults={
                    'zoho_account_code': str(record.get('account_code') or '').strip(),
                    'zoho_account_name': str(record.get('account_name') or '').strip(),
                    'zoho_account_type': str(record.get('account_type') or '').strip(),
                    'zoho_parent_name': str(record.get('parent_account_name') or '').strip(),
                    'zoho_is_active': bool(record.get('is_active', True)),
                },
            )
            created += was_created
            updated += not was_created
        return created, updated

    def _automap(self):
        """Fill blank mappings where the codes match exactly.

        Only a suggestion engine, and a conservative one: it never overwrites a
        mapping finance has already made, and only acts on an exact code match.
        Anything ambiguous is left for a person.
        """
        by_code = {a.code: a for a in Account.objects.postable()}
        proposed = 0
        for row in ZohoAccountMap.objects.unmapped():
            target = by_code.get(row.zoho_account_code)
            if target is not None:
                row.account = target
                row.note = (row.note + '\nAuto-mapped on exact code match.').strip()
                row.save(update_fields=['account', 'note', 'last_seen_at'])
                proposed += 1
        return proposed

    def _report_plan(self, records):
        known = set(ZohoAccountMap.objects.values_list('zoho_account_id', flat=True))
        incoming = {str(r.get('account_id')) for r in records}
        self.stdout.write(f'\nZoho accounts: {len(records)}')
        self.stdout.write(f'  already known : {len(incoming & known)}')
        self.stdout.write(f'  new           : {len(incoming - known)}')
        by_type = {}
        for record in records:
            key = record.get('account_type') or '?'
            by_type[key] = by_type.get(key, 0) + 1
        self.stdout.write('\nBy Zoho account type:')
        for key in sorted(by_type):
            self.stdout.write(f'  {key:28s} {by_type[key]:4d}')
        self.stdout.write(self.style.WARNING('\nDry run — nothing written.'))

    def _report_state(self):
        total = ZohoAccountMap.objects.count()
        mapped = ZohoAccountMap.objects.mapped().count()
        unmapped = ZohoAccountMap.objects.unmapped().count()
        ignored = ZohoAccountMap.objects.filter(is_ignored=True).count()
        self.stdout.write(f'\nMapping state: {mapped} mapped · {unmapped} need mapping · '
                          f'{ignored} ignored  (of {total})')
        if unmapped:
            self.stdout.write(self.style.WARNING(
                '\nUnmapped accounts — transactions against these cannot be coded yet:'))
            for row in ZohoAccountMap.objects.unmapped()[:15]:
                self.stdout.write(
                    f"   {row.zoho_account_code or '—':>10}  {row.zoho_account_name[:44]:46s} "
                    f"[{row.zoho_account_type}]")
            if unmapped > 15:
                self.stdout.write(f'   … and {unmapped - 15} more')
