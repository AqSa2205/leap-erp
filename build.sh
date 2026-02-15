#!/usr/bin/env bash
set -o errexit

echo "=== Installing dependencies ==="
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

echo "=== Running migrations ==="
python manage.py migrate --run-syncdb
python manage.py migrate accounts
python manage.py migrate projects
python manage.py migrate reports
python manage.py migrate costing
python manage.py migrate

echo "=== Showing migration status ==="
python manage.py showmigrations

echo "=== Loading initial data ==="
python manage.py loaddata initial_data.json || python manage.py load_initial_data || echo "Data may already exist"

echo "=== Creating superuser ==="
python manage.py createsuperuser --noinput --username admin --email admin@leapnetworks.com || echo "Superuser may already exist"

echo "=== Assigning admin role ==="
python manage.py shell -c "
from accounts.models import User, Role
try:
    admin_user = User.objects.get(username='admin')
    admin_role = Role.objects.get(name='admin')
    admin_user.role = admin_role
    admin_user.is_staff = True
    admin_user.save()
    print('Admin role assigned successfully')
except Exception as e:
    print(f'Could not assign role: {e}')
"

echo "=== Loading LNA project data ==="
python manage.py loaddata fixtures/lna_data.json || echo "LNA data may already exist"

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

echo "=== Build complete ==="
