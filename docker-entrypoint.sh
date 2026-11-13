#!/bin/sh
# One image, three jobs: `app` (web), `worker` (callback delivery / retries), `migrate` (database upgrade only).
set -e

role="${1:-app}"

case "$role" in
  app)
    flask --app wsgi db upgrade
    # %(U)s is the path without the query string: ABHA card/QR/profile GETs carry the user's token in `?xToken=`,
    # which must not reach the access log (the default format logs the whole request line).
    exec gunicorn -w "${WEB_CONCURRENCY:-4}" -b 0.0.0.0:5000 --timeout 120 --access-logfile - \
      --access-logformat '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(L)s' wsgi:app
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
