#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input

python manage.py migrate

# Load initial data (roles, regions, statuses)
python manage.py load_initial_data

# Create superuser if it doesn't exist
python manage.py createsuperuser --noinput --username admin --email admin@leapnetworks.com || true
