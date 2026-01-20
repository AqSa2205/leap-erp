#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input

python manage.py migrate

python manage.py shell -c "
from accounts.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser(username='admin', email='admin@leapnetworks.com', password='LeapAdmin@2026', first_name='Admin', last_name='User')
    print('Superuser created')
else:
    print('Superuser exists')
"
