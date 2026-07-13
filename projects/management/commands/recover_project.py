"""Recover a hard-deleted Project from a Render Point-in-Time-Recovery copy.

Prereq: restore the DB to just before the deletion into a NEW Render instance,
then set RECOVERY_DATABASE_URL to that instance's connection string. Dry-run by
default; add --commit to write. (A browser equivalent lives at the super-admin
'Project recovery' page for setups without shell access.)

    python manage.py recover_project --reference "LNA 2817 - Acme" --with-cascaded
    python manage.py recover_project --reference "LNA 2817 - Acme" --with-cascaded --commit
"""
from django.core.management.base import BaseCommand, CommandError

from projects.recovery import run_recovery, recovery_available


class Command(BaseCommand):
    help = "Recover a hard-deleted Project from a RECOVERY_DATABASE_URL PITR copy."

    def add_arguments(self, parser):
        g = parser.add_mutually_exclusive_group(required=True)
        g.add_argument('--pk', type=int)
        g.add_argument('--reference', type=str)
        parser.add_argument('--with-cascaded', action='store_true',
                            help='also recreate cascade-deleted children (history/revisions/finance)')
        parser.add_argument('--commit', action='store_true',
                            help='actually write (default: dry-run preview only)')

    def handle(self, *args, **o):
        if not recovery_available():
            raise CommandError("RECOVERY_DATABASE_URL is not set — point it at the "
                               "restored PITR database and retry.")
        try:
            r = run_recovery(reference=o.get('reference'), pk=o.get('pk'),
                             with_cascaded=o['with_cascaded'], commit=o['commit'])
        except ValueError as e:
            raise CommandError(str(e))

        w = self.stdout.write
        w(f"Project pk={r['pk']}  ref={r['reference']!r}  name={r['name']!r}")
        w(f"[1] Project row: {'already exists' if r['already_exists'] else 'recreate'}")
        w("[2] Relink surviving related rows:")
        for x in r['relinks']:
            w(f"      {x['label']:32} {x['count']:>4}")
        w("[3] Cascade-deleted children:")
        for x in r['recreations']:
            act = 'RECREATE' if o['with_cascaded'] else 'SKIP (use --with-cascaded)'
            w(f"      {x['label']:32} {x['count']:>4}  [{x['on_delete']}] -> {act}")
        w("")
        if r['committed']:
            w("DONE. Verify in the app, then remove RECOVERY_DATABASE_URL and "
              "delete the recovery database instance.")
        else:
            w("DRY RUN — nothing written. Re-run with --commit to apply.")
