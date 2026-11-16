# Architecture, API contracts and security model

abdm-gateway is the ABDM middleware between i-medicus and ABDM (M1 ABHA, M2 HIP, M3 HIU). i-medicus keeps its screens,
workflows and bodies; every ABDM call goes through the gateway, which holds the NHA credentials, talks to ABDM,
builds FHIR, encrypts and receives ABDM's callbacks.

## 1. Architecture

```
                     i-medicus (Laravel)                                   ABDM (sandbox / production)
   screens, workflows, FHIR payloads, /api/abdm-webhook                    gateway, ABHA, facility, PHR/HIU apps
        |   ^                                                                    ^   |
        |   | forwarded callbacks (worker)                       NHA session       |   | callbacks (JWT, RS256)
        v   |                                                    + API calls       |   v
  +-----------------------------------------------------------------------------------------------+
  |  abdm-gateway  (Flask, gunicorn)                                                              |
  |   API  /api/v1/*   auth -> rate limit -> idempotency -> audit -> service -> ABDM client       |
  |   Callback receiver  /api/v3/*   verify ABDM's JWT -> store encrypted -> queue forward        |
  |   Worker (outbox)   forwards callbacks to i-medicus, retries ABDM calls and HIU pushes        |
  |   FHIR builders, Fidelius (ECDH Curve25519 + AES-GCM), vault (encryption at rest)             |
  +-----------------------------------------------------------------------------------------------+
        |                                  |
   PostgreSQL (SQLite in dev)        Redis (optional: shared locks, rate limits)

  HIP --(encrypted records, straight to the HIU's dataPushUrl; never through ABDM or the gateway)--> HIU
```

| Component | Where | What it does |
|---|---|---|
| API | `src/app/api/v1/*` | One namespace per area: M1 (`auth`, `enrol`, `login`, `search`, `profile`, `account`, `benefit`, `share`, `app_compat`), M2 (`hip`, `hips`), M3 (`hiu`), FHIR and crypto (`fhir`) |
| Services | `src/app/services/*` | The ABDM logic of each area; routes only read the request and answer |
| ABDM client | `src/app/abdm/*` | Paths, headers and bodies of ABDM's calls; session token handling; Fidelius (`fidelius.py`) |
| FHIR builders | `src/app/fhir/*` | The 8 record types as ABDM document Bundles (NRCeS ABDM FHIR R4 IG 6.5.0) |
| Callback receiver | `src/app/callbacks/receiver.py` | `/api/v3/*`: verifies ABDM's JWT, stores the body encrypted, finds the facility, queues the forward |
| Outbox + worker | `src/app/outbox/*` | Queue with retry/backoff: callback forwarding, ABDM calls that are safe to repeat, the HIU push |
| Middleware | `src/app/middleware/*` | Tenant auth, idempotency, audit log, body limits, request context |

**Technology.** Python 3.13 (the Docker image), Flask + flask-restx (Swagger at `/docs`), SQLAlchemy + Alembic, gunicorn, PostgreSQL
(SQLite for local runs and tests), Redis optional, Docker image built by GitHub Actions.

### Data model (tables)

| Table | Holds |
|---|---|
| `api_clients` | One row per integrating client (i-medicus): client id, hashed secret, HIP id, rate limit, allowed IPs, default webhook |
| `hip_registrations` | Facilities (HIP/HIU ids) and the webhook URL each one's callbacks go to |
| `callback_events` | Every ABDM callback received: path, request id, facility, encrypted body, auth result, forward status |
| `outbox_jobs` | Queued work: kind, encrypted payload, attempts, next attempt, status (`pending`, `processing`, `done`, `dead`) |
| `abdm_transactions` | Which client started which ABDM `txnId` (enrolment / login flows): flow, last step, status `active, completed, failed`, expiry. A client can only continue its own transactions |
| `api_audit_logs` | One row per API call (the request/response trail, `flask audit purge --days N`): `requestId`, client, route, HTTP status, error code, the ABDM endpoint and status it called, latency, masked request body, timestamp |
| `abdm_error_logs` | ABDM errors with the code and message |
| `hip_replies` | The answers sent for ABDM's callbacks (duplicate guard) |
| `hip_consents` | Consent notifications received as HIP (status, types, care contexts, date range, expiry) |
| `hip_transfers` | One row per health-information transaction: push URL, status `ACKNOWLEDGED, PUSH_QUEUED, PUSHED, NOTIFIED, FAILED`, care contexts, attempts, last error, sender key used |

### Environments

| Environment | ABDM | Deploy | Notes |
|---|---|---|---|
| dev (laptop) | `ABDM_ENV=sbx`, or the local mock ABDM (`scripts/mock_abdm.py`) | `python run.py`, SQLite | `CALLBACK_AUTH_MODE=off` only against the mock; cloudflared tunnels for ABDM callbacks |
| sandbox | `ABDM_ENV=sbx` (`dev.abdm.gov.in`, X-CM-ID `sbx`) | Automatic from `develop` after CI (`deploy.yml`) | The VPS used for i-medicus tests |
| UAT | `ABDM_ENV=sbx` | Same image as sandbox, own server and `.env` | To be set up with the client (Day 23); the host and the callback URL are TBD |
| production | `ABDM_ENV=prod` (`live.abdm.gov.in`, X-CM-ID `abdm`) | Manual run of `deploy.yml`, only from `main`, after a reviewer approves the `production` GitHub environment | `CALLBACK_AUTH_MODE` must be `jwt`; `FHIR_STRUCTURAL_CHECK` is always on |

The deploy is image-based (`deploy/deploy.sh`: pull, `migrate`, `up -d app worker`); each environment has its own
secrets in GitHub, so a sandbox run cannot reach production. Settings are environment variables (`src/config/index.py`,
`.env.example`).

## 2. API contracts

- **Auth.** `Client-Id` + `Client-Secret` headers, or `Authorization: <token>` from `POST /api/v1/auth/session`.
- **Response.** `{"success": true, "status": "success", "data": {...}, "requestId": "..."}`; an error is
  `{"success": false, "code": "error", "message": {"error": {"code", "message", "details"}}, "requestId"}`. Exceptions:
  the FHIR routes answer the Bundle at the top level, the `app_compat` routes answer ABDM's own top-level shape, as
  i-medicus reads them. Error codes: [error-codes.md](error-codes.md).
- **Transaction id.** Every call has a `requestId`, returned in the body and kept in the audit log and the ABDM
  transaction trail.
- **Retries.** A repeated POST is `409 DUPLICATE_REQUEST` while the first runs; with an `Idempotency-Key` header the first
  answer is replayed. ABDM calls that are safe to repeat are queued when ABDM is down; calls that create things at ABDM
  (consent init, health-information request, OTPs) are sent once and never queued.

| Area | Where the contract is |
|---|---|
| Getting started, all M1 flows, examples | [integration-guide.md](integration-guide.md) |
| i-medicus path mapping (M1, M2, M3, FHIR, transfer, legacy video-call flow) | [imedicus-migration.md](imedicus-migration.md) |
| Callbacks (what ABDM sends, how it is verified, forwarded and retried) | [callbacks.md](callbacks.md) |
| Error codes | [error-codes.md](error-codes.md) |
| Every route with sample bodies | Swagger `/docs`, and [postman_collection.json](postman_collection.json) |
| Demo | [m2-demo-script.md](m2-demo-script.md) |

| Milestone | Routes (prefix `/api/v1`) |
|---|---|
| M1 ABHA | `auth/session`, `abha/enrol/*`, `abha/login/*`, `abha/search/*`, `abha/profile*`, `abha/account/*`, `abha/benefit/*`, `share/*`, `abha/app/*` |
| M2 HIP | `hip/care-context/on-discover`, `hip/link/*`, `hip/link-token`, `hip/sms/notify`, `hip/consent/on-notify`, `hip/health-information/{on-request,push,notify}`, `hip/transfers/<txn>` |
| FHIR / crypto | `fhir/{prescription,diagnostic-report,op-consultation,discharge-summary,immunization,health-document,wellness,invoice,legacy-prescription,validate}`, `fhir/{keys,encrypt,decrypt}` |
| M3 HIU | `hiu/consent/{init,on-notify,fetch,status}`, `hiu/health-information/request` |

## 3. Security model

| Concern | How it is done | Where |
|---|---|---|
| i-medicus to gateway authentication | Client id + secret (secret stored hashed) or a signed access token; optional per-client allowed IPs and rate limit | `middleware/tenant_auth.py`, `models/api_client.py` |
| Authorisation | Every call acts for the client's own facilities only: another client's transfer or callback is 404; a HIP id outside the client's facilities is refused | services, `callbacks/routing.py` |
| ABDM to gateway (callbacks) | ABDM's RS256 JWT is verified against its published JWKS (cached, refreshed once on an unknown key); `jwt` mode is the only one allowed in production | `callbacks/auth.py`, `config/index.py` |
| Callback integrity | A callback body is stored as received (encrypted) and forwarded to i-medicus unchanged; a duplicate callback or answer is detected (`hip_replies`, request id) | `callbacks/receiver.py`, `services/hip_service.py` |
| Gateway to i-medicus (forward) | The webhook URL must be https and not a private address; each forward is signed with the client's webhook secret | `callbacks/registry.py`, `callbacks/forwarder.py` |
| Secrets | NHA credentials live only in the gateway's environment; client secrets are shown once and stored hashed; webhook secrets, callback bodies and queued payloads are encrypted at rest (`ENCRYPTION_KEY`) | `callbacks/vault.py`, `utils/encryption.py` |
| Health records | Fidelius ECDH Curve25519 + AES-GCM, a fresh key pair and nonce per transfer; the gateway never stores keys or plain records | `abdm/fidelius.py`, `services/crypto_service.py` |
| Consent | Checked before every push and every acknowledgement: granted, not expired, date range, care contexts covered. The push goes only to the URL ABDM gave for the transaction (https, no private addresses, no redirects) | `services/hip_consent_service.py`, `services/hip_transfer_service.py` |
| Input validation | Request bodies are validated per route; size limits (larger for FHIR) | `common/validators.py`, `middleware/body_limits.py` |
| Logging | Aadhaar, OTP, mobile, tokens, secrets, Fidelius private keys and `plainTextData` / `decryptedData` are masked in logs and the audit table; the `fhir/*` routes (health records and keys) are audited without any request body. The app log records the path only; gunicorn (`--access-logformat` with `%(U)s`) and nginx (`log_format noquery` with `$uri`) leave the query string out, because ABHA profile/card/QR GETs carry the user's token in `?xToken=` | `utils/masking.py`, `middleware/audit.py`, `docker-entrypoint.sh`, the server's gunicorn service and nginx site |
| Duplicate and replay | Idempotency guard on every authenticated POST | `middleware/idempotency.py` |

Open points of the security model are tracked in Day 21 (security validation), not here.
