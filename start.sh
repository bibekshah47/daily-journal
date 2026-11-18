#!/usr/bin/env sh
# Render start command. Applies any pending database migrations, then
# starts the web server. One worker, so the in-memory rate limits count every
# request; threads let it serve several requests at once.
set -eu

flask --app app init-db
exec gunicorn "app:app" --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 60 --access-logfile -
