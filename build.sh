#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input

python manage.py migrate

# Load initial data (roles, regions, statuses)
python manage.py loaddata initial_data.json || python manage.py load_initial_data

# Create superuser and assign admin role
python manage.py createsuperuser --noinput --username admin --email admin@leapnetworks.com || true

python manage.py shell -c "
from accounts.models import User, Role
try:
    admin_user = User.objects.get(username='admin')
    admin_role = Role.objects.get(name='admin')
    admin_user.role = admin_role
    admin_user.is_staff = True
    admin_user.save()
    print('Admin role assigned')
except Exception as e:
    print(f'Could not assign role: {e}')
"
