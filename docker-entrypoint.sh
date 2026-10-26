#!/bin/sh
# One image, three jobs: `app` (web), `worker` (callback delivery / retries), `migrate` (database upgrade only).
set -e

role="${1:-app}"

case "$role" in
  app)
    flask --app wsgi db upgrade
    exec gunicorn -w "${WEB_CONCURRENCY:-4}" -b 0.0.0.0:5000 --access-logfile - wsgi:app
    ;;
  worker)
    exec flask --app wsgi worker run
    ;;
  migrate)
    exec flask --app wsgi db upgrade
    ;;
  *)
    exec "$@"
    ;;
esac
