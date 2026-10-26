#!/usr/bin/env bash
# Runs in GitHub Actions. Copies the compose file and deploy.sh to the server and runs deploy.sh there.
# Needs (environment secrets): SSH_HOST SSH_PORT SSH_USER SSH_PRIVATE_KEY SSH_KNOWN_HOSTS DEPLOY_PATH
# and from the workflow: IMAGE TAG REGISTRY_USER REGISTRY_TOKEN.
# The server's .env is never touched.
set -euo pipefail

for name in SSH_HOST SSH_USER SSH_PRIVATE_KEY SSH_KNOWN_HOSTS DEPLOY_PATH IMAGE TAG REGISTRY_USER REGISTRY_TOKEN; do
  if [ -z "${!name:-}" ]; then
    echo "Missing ${name}: add it as a secret of this GitHub environment." >&2
    exit 1
  fi
done
PORT="${SSH_PORT:-22}"

install -m 700 -d ~/.ssh
printf '%s\n' "$SSH_PRIVATE_KEY" > ~/.ssh/deploy_key
chmod 600 ~/.ssh/deploy_key
printf '%s\n' "$SSH_KNOWN_HOSTS" > ~/.ssh/known_hosts

SSH=(ssh -i ~/.ssh/deploy_key -p "$PORT" -o StrictHostKeyChecking=yes -o BatchMode=yes "${SSH_USER}@${SSH_HOST}")
SCP=(scp -i ~/.ssh/deploy_key -P "$PORT" -o StrictHostKeyChecking=yes -o BatchMode=yes)

"${SSH[@]}" "test -f '${DEPLOY_PATH}/.env' || { echo 'No ${DEPLOY_PATH}/.env on the server: create it first (deploy/.env.server.example).' >&2; exit 1; }"
"${SCP[@]}" deploy/docker-compose.server.yml deploy/deploy.sh "${SSH_USER}@${SSH_HOST}:${DEPLOY_PATH}/"

# Short-lived registry login (the token stops working when this job ends)
printf '%s' "$REGISTRY_TOKEN" | "${SSH[@]}" "docker login ghcr.io -u '${REGISTRY_USER}' --password-stdin"

"${SSH[@]}" "cd '${DEPLOY_PATH}' && GATEWAY_IMAGE='${IMAGE}' bash deploy.sh '${TAG}'"
