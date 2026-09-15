#!/usr/bin/env bash
set -o errexit

echo "=== Installing dependencies ==="
# requirements.txt is self-correct across environments: psycopg2-binary carries
# a `sys_platform == 'linux'` marker, so it installs here (Render/Linux) and is
# skipped on hires' Windows machines.
pip install -r requirements.txt

echo "=== Checking DATABASE_URL ==="
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL is not set!"
    exit 1
else
    echo "DATABASE_URL is set: ${DATABASE_URL:0:30}..."
fi

echo "=== Testing database connection ==="
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp_leap.settings')
import django
django.setup()
from django.db import connection
cursor = connection.cursor()
cursor.execute('SELECT 1')
print('Database connection successful!')
print(f'Database engine: {connection.vendor}')
"

echo "=== Collecting static files ==="
python manage.py collectstatic --no-input

echo "=== Safeguard: dumping project references before migrations ==="
# Snapshot every proposal_reference before any migration rewrites them. Writes a
# file AND echoes the full snapshot to this deploy log (durable restore source).
# Never blocks the deploy if it can't run (e.g. table not created on first boot).
python manage.py dump_project_references || echo "Reference dump skipped (likely first deploy)."

echo "=== Running migrations ==="
python manage.py migrate --run-syncdb
python manage.py migrate accounts
python manage.py migrate projects
python manage.py migrate reports
python manage.py migrate costing
python manage.py migrate

echo "=== Showing migration status ==="
python manage.py showmigrations

echo "=== Seeding initial data (empty tables only) ==="
# Deliberately NOT `loaddata initial_data.json`. That fixture carries explicit
# primary keys, and loaddata overwrites a row outright - including fields the
# fixture does not mention, which reset to their defaults. On a database first
# initialised from it, every deploy put regions 1-4 and statuses 1-10 back:
# renames, dashboard_group and excluded_from_won_tile included. Proven on a
# disposable database before this change.
#
# load_initial_data seeds a table only while it is empty, so it is safe to run
# on every deploy. No `|| echo`: it cannot fail for "already exists", so a
# failure here is real and should stop the build.
python manage.py load_initial_data

echo "=== Creating superuser ==="
python manage.py createsuperuser --noinput --username admin --email admin@leapnetworks.com || echo "Superuser may already exist"

echo "=== Assigning admin role (first boot only) ==="
# Only when the account has no role yet. This used to run unconditionally, so
# every deploy put the bootstrap 'admin' account back to the 'admin' role and
# is_staff - undoing any change made to it in the ERP.
python manage.py shell -c "
from accounts.models import User, Role
try:
    admin_user = User.objects.get(username='admin')
    if admin_user.role_id is None:
        admin_user.role = Role.objects.get(name='admin')
        admin_user.is_staff = True
        admin_user.save()
        print('Admin role assigned')
    else:
        print('Admin account already has a role - left as it is')
except Exception as e:
    print(f'Could not assign role: {e}')
"

# The LNA project fixture (fixtures/lna_data.json) is no longer loaded here.
# It holds 325 objects with explicit primary keys, 311 of them projects, and
# was replayed on every deploy. It currently fails to load at all ("Project has
# no field named 'epc'"), and `|| echo` turned that failure into "LNA data may
# already exist" - so the risk was latent, but repairing the fixture would have
# started silently overwriting 311 live projects on the next deploy. Audit F07.

echo "=== Loading exchange rates ==="
python manage.py shell -c "
from costing.models import ExchangeRate
from decimal import Decimal

rates = [
    ('USD', 'US Dollar', Decimal('1.000000')),
    ('SAR', 'Saudi Riyal', Decimal('3.750000')),
    ('AED', 'UAE Dirham', Decimal('3.670000')),
    ('GBP', 'British Pound', Decimal('0.790000')),
    ('EUR', 'Euro', Decimal('0.920000')),
    ('SGD', 'Singapore Dollar', Decimal('1.340000')),
    ('CNY', 'Chinese Yuan', Decimal('7.240000')),
]

for code, name, rate in rates:
    obj, created = ExchangeRate.objects.get_or_create(
        currency_code=code,
        defaults={'currency_name': name, 'rate_to_usd': rate}
    )
    if created:
        print(f'Created: {code} = {rate}')
    else:
        print(f'Exists: {code} = {obj.rate_to_usd}')
"

echo "=== Resyncing Postgres PK sequences (after loaddata / PITR) ==="
# loaddata above inserts rows with explicit PKs without advancing Postgres'
# per-table sequences; a PITR restore does the same. Left unfixed, the next
# ORM create() reuses an existing id and every create page 500s on a
# duplicate-key error. This resync must run AFTER all loaddata steps.
python manage.py resync_sequences

echo "=== Build complete ==="
