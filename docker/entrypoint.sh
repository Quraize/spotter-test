#!/bin/sh
# Prepare the database, then hand over to the CMD (gunicorn by default).
set -eu

echo "[entrypoint] applying migrations"
python manage.py migrate --noinput

echo "[entrypoint] importing fuel stations (idempotent)"
python manage.py import_stations

echo "[entrypoint] starting: $*"
exec "$@"
