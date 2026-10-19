#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py import_history
python manage.py init_roles
python manage.py collectstatic --noinput
exec gunicorn pfplus.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile - --access-logformat '%(h)s %(m)s %(U)s %(s)s %(L)s'
