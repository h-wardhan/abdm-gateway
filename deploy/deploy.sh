#!/usr/bin/env bash
# Runs ON THE SERVER (CD copies it and calls it over SSH): deploy.sh <image-tag>
#
#   1. pull the new image
#   2. run the database migration (one-off container)
#   3. restart app + worker on the new image
#   4. wait for /ready; if it never answers, put the previous image back and fail
#
# Migrations only add things (new tables / columns), so the previous image keeps working with the migrated
# database. Anything that is not backwards compatible needs a manual rollout.
set -euo pipefail

cd "$(dirname "$0")"

TAG="${1:?usage: deploy.sh <image-tag>}"
COMPOSE=(docker compose -f docker-compose.server.yml)
STATE_FILE=".deployed_tag"
PREVIOUS="$(cat "$STATE_FILE" 2>/dev/null || true)"
PORT="$(grep -E '^APP_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 || true)"
PORT="${PORT:-5000}"

wait_ready() {
  local tries=30
  while [ "$tries" -gt 0 ]; do
    if curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/ready" > /dev/null 2>&1; then
      return 0
    fi
    tries=$((tries - 1))
    sleep 3
  done
  return 1
}

echo "Deploying ${TAG} (previous: ${PREVIOUS:-none})"
export GATEWAY_TAG="$TAG"

"${COMPOSE[@]}" pull app worker
"${COMPOSE[@]}" run --rm --no-deps app migrate
"${COMPOSE[@]}" up -d app worker

if wait_ready; then
  echo "$TAG" > "$STATE_FILE"
  echo "Deployed ${TAG}: /ready is answering."
  docker image prune -f > /dev/null
  exit 0
fi

echo "ERROR: /ready did not answer after the deploy." >&2
"${COMPOSE[@]}" logs --tail=50 app >&2 || true
if [ -n "$PREVIOUS" ]; then
  echo "Rolling back to ${PREVIOUS}" >&2
  export GATEWAY_TAG="$PREVIOUS"
  "${COMPOSE[@]}" up -d app worker
  if wait_ready; then
    echo "Rolled back to ${PREVIOUS}." >&2
  else
    echo "ERROR: rollback to ${PREVIOUS} is not answering either. Check the server by hand." >&2
  fi
else
  echo "No previous version recorded, nothing to roll back to." >&2
fi
exit 1
