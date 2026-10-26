# Deploying the gateway

CI/CD lives in `.github/workflows/`:

| Workflow | When | What |
|---|---|---|
| `ci.yml` | every push to `develop` / `main` and every PR | lint, tests on SQLite **and** Postgres (100 % coverage on the callback, outbox and Fidelius code), migration check, Docker build |
| `deploy.yml` | green CI on `develop` -> **sandbox**, automatically | build image, push to GHCR, SSH to the server, `deploy.sh` |
| `deploy.yml` | "Run workflow" with `production` | only from `main`, and only after a reviewer approves the `production` environment |

> `workflow_run` triggers are read from the repository's **default branch**: the automatic sandbox deploy starts
> working once `deploy.yml` is on `main`. Until then use "Run workflow" (environment `sandbox`).

## One-time setup

### 1. GitHub (repository settings -> Environments)

Create two environments, **`sandbox`** and **`production`**. Each has its own secrets, so a sandbox run can never
reach the production host:

| Secret | Value |
|---|---|
| `SSH_HOST` | server address |
| `SSH_PORT` | optional, default 22 |
| `SSH_USER` | deploy user (member of the `docker` group) |
| `SSH_PRIVATE_KEY` | private key of a key pair made only for deploys (public half in the user's `authorized_keys`) |
| `SSH_KNOWN_HOSTS` | output of `ssh-keyscan -p 22 <host>`, taken from a machine you trust |
| `DEPLOY_PATH` | folder on the server, e.g. `/opt/abdm-gateway` |

On `production` add **Required reviewers**. Also protect `main` (pull requests + passing CI).

### 2. The server

The gateway is already running there (i-medicus signs in to it). **Look at how, before changing anything:**

```bash
docker ps          # is it a container? which image, which published port?
systemctl status   # or a systemd service / gunicorn process?
```

Then:

1. Install Docker + the compose plugin, create `DEPLOY_PATH`.
2. Create `DEPLOY_PATH/.env` from `.env.server.example`. **`DATABASE_URL` must be the database the running gateway
   uses today** (it holds i-medicus' client id and secret), and `ENCRYPTION_KEY` must be generated once and kept:
   `python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.
   Copy the other values (`SECRET_KEY`, `JWT_SECRET_KEY`, `NHA_CLIENT_ID`, `NHA_CLIENT_SECRET`) from the current `.env`:
   a new `JWT_SECRET_KEY` logs every client out once, a new `SECRET_KEY` is harmless.
3. The reverse proxy that serves `https://abdm-gateway.example.com` must forward to
   `127.0.0.1:${APP_PORT}` (default 5000). Set `APP_PORT` if the proxy uses another port. If the current gateway
   already listens there, stop it at the moment of the first deploy (the new `app` container takes the port).
4. Run the first deploy with "Run workflow" -> `sandbox`, and watch `/ready`.

### 3. Sandbox callbacks

After the first deploy: `docker compose -f docker-compose.server.yml exec app flask hips add ...`
(see `docs/callbacks.md`). The worker container delivers the callbacks.

## What a deploy does (`deploy.sh <tag>`)

1. pull the image, 2. run `migrate` in a one-off container, 3. restart `app` and `worker`,
4. wait for `/ready`, then remember the tag in `.deployed_tag`.

If `/ready` never answers, the previous tag is started again and the deploy fails. Migrations only add tables and
columns, so the previous image keeps working with the migrated database.

Manual rollback: `cd DEPLOY_PATH && bash deploy.sh <previous-tag>`; tags are the first 12 characters of the commit.

## Safety

- The server's `.env` and the database volume are never touched by a deploy.
- Nothing in the pipeline sets `ABDM_ENV=prod`. Going to production is a separate, deliberate change of the server
  `.env` (`ABDM_ENV=prod`, production NHA credentials), and the gateway then refuses
  `CALLBACK_AUTH_MODE` other than `jwt`.
- Callback targets listed in `CALLBACK_BLOCKED_HOSTS` (production webhooks) can never be registered or delivered to.
