#!/bin/sh
set -eu

# DB + static init for container start (safe to run multiple times)
python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"

