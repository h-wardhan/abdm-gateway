# abdm-gateway

Middleware between client applications and **ABDM** (Ayushman Bharat Digital Mission).
Client apps in any stack (Laravel, Node, Java, .NET, mobile backends) call this service over plain HTTPS + JSON.
The gateway holds the NHA credentials, encrypts sensitive fields (RSA-OAEP with the ABDM public key),
calls ABDM v3 APIs and returns one consistent response format.

It replaces the third-party VCDoctor gateway that i-medicus used. Request bodies and response
shapes are kept compatible with what i-medicus already sends and parses, so i-medicus only changes
its base URL and endpoint paths.

**Scope (phase 1): ABDM Milestone 1:** ABHA creation, ABHA login/verification, profile, ABHA card and QR.
M2/M3 (care contexts, consent, health data exchange) come in later phases.

> Status: **M1 complete in code and tested against a mock ABDM** (60 pytest tests, plus a 33-check PHP smoke test from i-medicus).
> Pending: an end-to-end run on the real ABDM sandbox once NHA sandbox credentials are available.

Documentation:
- [docs/integration-guide.md](docs/integration-guide.md): how any client app integrates (flows + curl / PHP / Node / Java / C# examples)
- [docs/error-codes.md](docs/error-codes.md): every error code
- [docs/imedicus-migration.md](docs/imedicus-migration.md): i-medicus: VCDoctor path → gateway path, `.env`
- [docs/postman_collection.json](docs/postman_collection.json): Postman collection (regenerate with `python scripts/build_postman.py`)
- Live gateway: https://abdm-gateway.chickenkiller.com (Swagger UI: https://abdm-gateway.chickenkiller.com/docs)

---

## Architecture

```
 Client app (i-medicus / RazorClinic / any stack)
        │  Authorization: <access token>, Client-Id, REQUEST-ID
        ▼
 ┌──────────────────── abdm-gateway (Flask) ────────────────────┐
 │  /api/v1/auth/session        /api/v1/abha/*                  │
 │  routes → services → ABDM v3 client → mappers → envelope     │
 │  tenant auth · validation · RSA encrypt · audit log · errors │
 └──────────────────────────────────────────────────────────────┘
        ▼
 ABDM gateway (dev|live.abdm.gov.in)  +  ABHA (abhasbx|abha.abdm.gov.in)
```

| Folder | What lives there |
|---|---|
| `src/config/` | `index.py` (env → config classes, `ABDM_ENV` sbx/prod switch), `logger.py` (JSON logs + masking) |
| `src/app/__init__.py` | `create_app()` factory |
| `src/app/extensions.py` | db, migrate, limiter, cache, restx `Api` (Swagger) |
| `src/app/api/` | HTTP namespaces (`health.py`, `v1/…`) |
| `src/app/services/` | business flows (one per use case) |
| `src/app/abdm/` | ABDM v3 HTTP client, gateway session, RSA crypto (Flask-free) |
| `src/app/mappers/` | ABDM response → our response shapes |
| `src/app/models/` | SQLAlchemy models |
| `src/app/common/response.py` | success / error envelope helpers |
| `src/app/errors.py` | exception classes + global error handlers |
| `src/app/middleware/` | request id, access log, tenant auth |
| `src/utils/` | Aadhaar Verhoeff validator, AES-GCM encryption, log masking |
| `scripts/` | `mock_abdm.py` (fake ABDM for local dev), `build_postman.py` |
| `tests/` | `unit/` (auth, ABDM client, skeleton), `contract/` (every M1 endpoint against mocked ABDM) |
| `docs/` | integration guide, error codes, i-medicus migration, Postman collection |

---

## Local setup

Requirements: Python 3.12+ (tested on 3.14). PostgreSQL and Redis are optional for local dev:
- If `DATABASE_URL` is empty, the app uses SQLite at `instance/abdm_gateway.db`.
- If `REDIS_URL` is empty, the cache and rate limits are kept in memory.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell: if blocked, first run
                                  #   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
                                  # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then fill SECRET_KEY, JWT_SECRET_KEY, ENCRYPTION_KEY, NHA_CLIENT_ID/SECRET
flask db upgrade                  # create / update the database schema
flask clients create --name "i-medicus"   # prints Client-Id + Client-Secret once
python run.py                     # http://localhost:5000  (Swagger: /docs)
```

Postgres + Redis through Docker (optional):
```bash
docker compose up -d db redis
# .env: DATABASE_URL=postgresql://abdm:abdm@localhost:5432/abdm_gateway
#       REDIS_URL=redis://localhost:6379/0
```

Tests (in-memory SQLite, no network):
```bash
python -m pytest -q
```

### Database migrations
The schema is managed only by Alembic (Flask-Migrate); the app never calls `create_all()`.
```bash
flask db upgrade                         # apply pending migrations
flask db migrate -m "describe change"    # after changing a model: generate a migration, review it, commit it
flask db downgrade                       # roll back one migration
```
Migrations live in `migrations/versions/` and are written so they run on both PostgreSQL and SQLite.

| Table | Purpose |
|---|---|
| `api_clients` | client apps (tenants): client_id, SHA-256 of the secret, token_version, hip_id, active flag, rate limit, IP allowlist |
| `abdm_transactions` | which client owns which ABDM txnId (one client can't continue another's flow) |
| `api_audit_logs` | one masked row per `/api/` request: client, route, status, error code, ABDM endpoint/status, latency, IP |
| `abdm_error_logs` | raw (masked) ABDM error per failed upstream call |

### Managing client apps
```bash
flask clients create --name "RazorClinic" [--rate-limit "300 per minute"] [--allowed-ips "1.2.3.4"] [--hip-id X]
flask clients list
flask clients rotate  <client_id>    # new secret; all existing tokens stop working
flask clients disable <client_id>    # block the client; tokens revoked
flask clients enable  <client_id>
flask audit purge [--days 90]        # delete old audit rows (default AUDIT_LOG_RETENTION_DAYS)
```

---

## Authentication (client apps)

There are **two different credential pairs**:

| Credential | Lives in | Issued by | Used for |
|---|---|---|---|
| Client-Id / Client-Secret | the client app (e.g. i-medicus `ABDM_CLIENT_ID/SECRET`) | us, via `flask clients create` | client app → this gateway |
| `NHA_CLIENT_ID` / `NHA_CLIENT_SECRET` | this gateway's `.env` only | NHA (sandbox.abdm.gov.in) | this gateway → ABDM |

Client apps never see the NHA credentials.

1. `POST /api/v1/auth/session` with `{"clientId": "...", "clientSecret": "..."}` (or the `Client-Id` / `Client-Secret` headers).
   The response is `{"success": true, "data": {"accessToken", "expiresIn", "refreshToken", "refreshExpiresIn", "tokenType"}}`.
2. Send `Authorization: <accessToken>` on every other call. `Bearer <accessToken>` works too.
   `Client-Id` is optional, but if sent it must match the token.
3. When the token expires, call `POST /api/v1/auth/refresh` with `{"refreshToken": "..."}` or create a new session.

Tokens are stateless JWTs, so creating a session writes no database row. Rotating or disabling a
client revokes all its tokens immediately.

Rate limits are applied per client (`api_clients.rate_limit`, default `DEFAULT_RATE_LIMIT`).
The session endpoint has its own per-IP limit (`SESSION_RATE_LIMIT`).

### Useful URLs
| URL | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET /ready` | DB + cache reachable, ABDM credentials configured |
| `GET /docs` | Swagger UI (disable with `SWAGGER_ENABLED=false`) |
| `GET /swagger.json` | OpenAPI spec (import into Postman, generate clients) |
| `POST /api/v1/auth/session` | get an access token |
| `POST /api/v1/auth/refresh` | refresh an access token |
| `GET /api/v1/auth/me` | check which client a token belongs to |

---

## M1 endpoints

All of these need `Authorization: <access token>`. Calls marked *xToken* also need the ABHA user token
from a verify response, sent as the `?xToken=` query param or the `X-Token` header.

| Flow | Method + path | Body / query |
|---|---|---|
| **Create ABHA** | `POST /api/v1/abha/enrol/aadhaar/otp` | `{aadhaarNumber}` |
| | `POST /api/v1/abha/enrol/aadhaar/verify` | `{txnId, otpValue, mobilenumber}` |
| | `POST /api/v1/abha/enrol/mobile/otp` | `{txnId, mobilenumber}` (if ABHA mobile differs) |
| | `POST /api/v1/abha/enrol/mobile/verify` | `{txnId, otp}` |
| | `GET /api/v1/abha/enrol/address-suggestions` | `?txnId=` |
| | `POST /api/v1/abha/enrol/address` | `{txnId, abhaAddress, preferred}` |
| | `POST /api/v1/abha/profile/email/verify-link` | `{email}` + *xToken* |
| **Login by mobile** | `POST /api/v1/abha/search/mobile` | `{mobileNumber}` |
| | `POST /api/v1/abha/login/mobile/otp` | `{txnId, index}` |
| | `POST /api/v1/abha/login/mobile/verify` | `{txnId, otp}` |
| | `POST /api/v1/abha/login/mobile/select-account` | `{txnId, abhaNumber}` + `T-Token` header (several ABHAs) |
| **Login by ABHA number** | `POST /api/v1/abha/search/abha-number` | `{abhaNumber}` |
| | `POST /api/v1/abha/login/abha-number/{mobile\|aadhaar}/otp` | `{abhaNumber}` |
| | `POST /api/v1/abha/login/abha-number/{mobile\|aadhaar}/verify` | `{txnId, otp}` |
| **Login by Aadhaar** | `POST /api/v1/abha/login/aadhaar/otp` | `{aadhaarNumber}` |
| | `POST /api/v1/abha/login/aadhaar/verify` | `{txnId, otp}` |
| **Login by ABHA address** | `POST /api/v1/abha/search/abha-address` | `{abhaAddress}` |
| | `POST /api/v1/abha/login/abha-address/otp` | `{abhaAddress, scope: mobile-verify\|aadhaar-verify}` |
| | `POST /api/v1/abha/login/abha-address/verify` | `{txnId, otp, scope}` |
| **Profile / card / QR** | `GET /api/v1/abha/profile` | *xToken* (JSON) |
| | `GET /api/v1/abha/profile/card` | *xToken* (PNG/PDF bytes) |
| | `GET /api/v1/abha/profile/qr` | *xToken* (PNG bytes) |
| | `GET /api/v1/abha/phr/profile` | *xToken* (after ABHA address login) |
| | `GET /api/v1/abha/phr/card` | *xToken* (after ABHA address login) |

Key behaviour:
- **Encryption:** Aadhaar, OTP, mobile, email, ABHA number/address and index are RSA-OAEP encrypted by the gateway
  with the ABDM public key, which is fetched from ABDM and cached. Clients send plain values over HTTPS.
- **txnId ownership:** a txnId belongs to the client app that started the flow. Any other client gets `403 TXN_FORBIDDEN`.
- **Duplicate requests:** while a POST is being processed, the same POST from the same client (same path and body)
  gets `409 DUPLICATE_REQUEST`, so a double click or client retry cannot send two OTPs. Once it finishes, it can be sent again.
  Clients that want safe retries can also send an `Idempotency-Key` header: a repeat with the same key gets the stored
  response (header `Idempotent-Replayed: true`) without calling ABDM again. Responses are kept for `IDEMPOTENCY_TTL_SECONDS`;
  errors are not kept, so a retry runs again. With several Gunicorn workers, set `REDIS_URL` so all workers share the locks.
- **Wrong OTP on a verify:** HTTP 200, `success: false`, `data.authResult: "failed"`.
- **Input aliases:** the gateway accepts aliases for some fields (`otp`/`otpValue`/`OTP`, `mobileNumber`/`mobilenumber`,
  `scope`/`scop`/`authMethod`) and ignores unknown keys.

### How a call flows through the code
```
route (src/app/api/v1/*.py)  → TenantResource: tenant auth + rate limit
  → service (src/app/services/*_service.py): validate input, txnId ownership, encrypt
    → ABDM call (src/app/abdm/*.py → http.abdm_request): headers, gateway token, retries, error mapping, abdm.log
  → mapper (src/app/mappers/abha.py) → envelope (src/app/common/response.py)
  → audit row (api_audit_logs) + access log
```

---

## Local development without NHA credentials (mock ABDM)

`scripts/mock_abdm.py` is a fake ABDM that implements every M1 endpoint the gateway uses. It has
its own RSA key, so the gateway's encryption is exercised for real.

```bash
python scripts/mock_abdm.py                      # http://127.0.0.1:5099
# gateway .env:
#   ABDM_GATEWAY_URL=http://127.0.0.1:5099
#   ABDM_ABHA_URL=http://127.0.0.1:5099
#   NHA_CLIENT_ID=mock-client
#   NHA_CLIENT_SECRET=mock-secret
python run.py
```

Mock test data:
- OTP `123456` succeeds; any other OTP fails.
- An Aadhaar ending in `0000` returns UIDAI error 111.
- An enrolment mobile ending in `1` simulates "the Aadhaar-linked mobile differs", which triggers the mobile-update step.
- ABHA addresses starting with `ramesh` exist.

Switch back to the real sandbox by clearing the URL overrides and setting the real `NHA_CLIENT_ID` / `NHA_CLIENT_SECRET`.

---

## Response format

Every JSON response uses the same envelope.

Success:
```json
{ "success": true, "data": { "txnId": "..." }, "requestId": "6f1c..." }
```

Error:
```json
{
  "success": false,
  "code": "error",
  "message": { "error": { "code": "INVALID_AADHAAR", "message": "Invalid Aadhaar number. Please check the number and try again." } },
  "requestId": "6f1c..."
}
```

- `requestId` is also returned in the `X-Request-ID` response header.
- If the client sends a `REQUEST-ID` (or `X-Request-ID`) header, that value is reused, so a single id can be traced end to end.
- ABDM errors keep ABDM's HTTP status, error code and message text (for example `ABDM-1204`, `UIDAI Error code : 111 …`,
  `You can request for new OTP after 30 seconds`), so client apps can show meaningful messages.

The full list of codes is in [docs/error-codes.md](docs/error-codes.md).

---

## Logging

- JSON lines on stderr and in `logs/app.log`. Every line has `request_id`, `client_id`, `route`, `status`, `latency_ms`.
- Outbound ABDM calls are also written to `logs/abdm.log` (endpoint, status, latency, ABDM error code/message).
- Failed ABDM calls are additionally stored, masked, in the `abdm_error_logs` table, and every `/api/` request
  gets a row in `api_audit_logs`. Both can be looked up by `request_id`.
- A masking filter runs on every record: Aadhaar, mobile and ABHA numbers keep only their last 4 digits, and OTPs, tokens, secrets and photos are replaced with `***`.
- Levels come from `LOG_LEVEL` (app) and `LOG_LEVEL_LIBS` (SQLAlchemy, urllib3, …).

---

## Configuration

See [.env.example](.env.example) for every variable.

`ABDM_ENV` picks the ABDM defaults:

| ABDM_ENV | Gateway | ABHA | X-CM-ID |
|---|---|---|---|
| `sbx` | https://dev.abdm.gov.in | https://abhasbx.abdm.gov.in | sbx |
| `prod` | https://live.abdm.gov.in | https://abha.abdm.gov.in | abdm |

`ABDM_GATEWAY_URL`, `ABDM_ABHA_URL` and `ABDM_X_CM_ID` override the defaults when set.

---

## Deployment

Not part of phase 1. `Dockerfile` and `wsgi.py` (gunicorn) are included for later.
