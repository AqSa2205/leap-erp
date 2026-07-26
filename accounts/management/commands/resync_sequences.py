from django.apps import apps
from django.core.management.base import BaseCommand
from django.core.management.color import no_style
from django.db import connection


class Command(BaseCommand):
    """Reset every table's primary-key sequence to MAX(id)+1.

    PostgreSQL keeps an independent sequence for each auto-increment primary
    key. `loaddata` with explicit PKs (which build.sh runs on every deploy)
    and PITR restores insert rows *without* advancing that sequence, so the
    next ORM `create()` reuses an id that already exists and the INSERT fails
    with `duplicate key value violates unique constraint "..._pkey"` — a 500
    on any create page. This resyncs the sequences so the next id is safe.

    It is a no-op on SQLite (no sequences) and safe to run repeatedly: it only
    moves each sequence forward to where the data already is, never backward,
    and never touches a row. Run it in build.sh after the loaddata steps.
    """
    help = 'Resync Postgres PK sequences to MAX(id)+1 (fixes duplicate-key 500s after loaddata/PITR).'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write(f'DB vendor is {connection.vendor!r}, not postgresql — nothing to resync.')
            return

        auto_types = {'AutoField', 'BigAutoField', 'SmallAutoField'}
        desynced = []
        with connection.cursor() as cursor:
            for model in apps.get_models():
                meta = model._meta
                if meta.proxy or not meta.managed:
                    continue
                pk = meta.pk
                if pk.get_internal_type() not in auto_types:
                    continue
                table, col = meta.db_table, pk.column
                cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [table, col])
                seq = cursor.fetchone()[0]
                if not seq:
                    continue
                qtable, qcol = connection.ops.quote_name(table), connection.ops.quote_name(col)
                cursor.execute(f'SELECT COALESCE(MAX({qcol}), 0) FROM {qtable}')
                max_id = cursor.fetchone()[0]
                cursor.execute(f'SELECT last_value, is_called FROM {seq}')
                last_value, is_called = cursor.fetchone()
                next_id = last_value + 1 if is_called else last_value
                if max_id > 0 and next_id <= max_id:
                    desynced.append((table, max_id, next_id))

            if desynced:
                self.stdout.write(self.style.WARNING(
                    f'Found {len(desynced)} desynced sequence(s) — next INSERT would collide:'))
                for table, max_id, next_id in desynced:
                    self.stdout.write(f'  {table}: max_id={max_id} but next_id={next_id}')
            else:
                self.stdout.write('No desynced sequences found.')

            # Resync every model unconditionally (a no-op where already correct).
            statements = connection.ops.sequence_reset_sql(no_style(), list(apps.get_models()))
            for sql in statements:
                cursor.execute(sql)

        self.stdout.write(self.style.SUCCESS(
            f'Resynced {len(statements)} sequence(s); {len(desynced)} had been behind.'))
