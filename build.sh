#!/usr/bin/env bash
set -o errexit

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Collecting static files ==="
python manage.py collectstatic --no-input

echo "=== Running migrations ==="
python manage.py migrate --run-syncdb
python manage.py migrate accounts
python manage.py migrate projects
python manage.py migrate reports
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

echo "=== Build complete ==="
