# ABDM callbacks

Some ABDM features are asynchronous: ABDM calls **us** when something happens (a patient scans the facility QR,
a care context is linked, a consent is granted). The gateway receives these calls, checks they are really from ABDM,
and delivers them to the client app that owns the facility. Client apps (i-medicus) keep their webhook handlers.

```
ABDM ──POST /api/v3/<path>──▶ gateway ──▶ stored + queued ──▶ 202 Accepted (at once)
                                              │
              worker (flask worker run) ──────┘──POST <callbackUrl>/api/v3/<path>──▶ client app
                                                  success (2xx) = done, anything else = retry
```

## What the client app receives

The same thing VCDoctor used to forward, so existing handlers keep working:

```
POST {callbackUrl}/api/v3/hip/patient/share
request-id: <ABDM REQUEST-ID>
X-Gateway-Event-Id: 42                     unique per callback; the same id on every retry
X-Gateway-Timestamp: 2026-10-22T10:30:00Z
X-Gateway-Signature: sha256=<hex>          HMAC-SHA256(webhookSecret, "<timestamp>.<raw body>")
<the body exactly as ABDM sent it>
```

- The part of the URL after the webhook URL (`api/v3/hip/patient/share`) says what the callback is about.
- Answer `2xx` once the callback is handled. A `5xx`, `408` or `429` is retried; any other `4xx` or a redirect is
  treated as a final failure (redirects are never followed).
- Delivery is **at least once**: after a timeout the same callback can arrive again. `X-Gateway-Event-Id` identifies it.
- Verifying `X-Gateway-Signature` is optional but recommended. The secret is shown once, when the HIP is registered.

## Registering a facility (HIP)

The callback URL is stored in the gateway database (not in code), per facility, so one gateway serves many
client apps and clinics.

```bash
flask hips add --client <client_id> --hip-id IN0110005723 --callback-url https://dev.example.com/api/abdm-webhook
flask hips list
flask hips set-url IN0110005723 https://other.example.com/api/abdm-webhook
flask hips rotate-secret IN0110005723
flask hips disable IN0110005723
```

or from the client app: `PUT /api/v1/hips/{hipId}` with `{"callbackUrl": "...", "name": "..."}` (see Swagger).

Rules for a callback URL: https, a public address (no private/internal IP), no credentials or query string, and
never a host in `CALLBACK_BLOCKED_HOSTS` (production webhooks). Staging servers without TLS need
`CALLBACK_ALLOW_HTTP=true`; local tests need `CALLBACK_ALLOW_PRIVATE_URLS=true`. The URL is checked again before
every delivery.

## One webhook for all your clinics

A client app such as i-medicus has one webhook for all its clinics, and new clinics are added all the time, so
registering every clinic one by one is not needed. Give the client app a **default webhook** once:

```bash
flask webhook set --client <client_id> --url https://dev.example.com/api/abdm-webhook   # prints the secret once
flask webhook show
flask webhook rotate-secret --client <client_id>
```

The first callback of a facility nobody registered is delivered to that URL and the gateway registers the facility
for the client from then on (`flask hips list` marks it `(auto)`); it follows the client's webhook if that changes.
This only works while **exactly one** active client has a default webhook. With two, an unknown facility is stored
`unrouted` (nothing is lost): register it with `flask hips add` and `flask callbacks reroute`. A facility
registered by hand always keeps its own URL.

## Scan & Share (patient scans the clinic QR)

```
patient scans the clinic QR in the ABHA app
ABDM ──POST /api/v3/hip/patient/share──▶ gateway ──▶ your webhook (profile of the patient, metaData.hipId = the clinic)
your app saves the patient and gives a token number
your app ──POST /api/v1/share/on-share──▶ gateway ──▶ ABDM (the patient sees the token)
```

```
POST /api/v1/share/on-share        Authorization: <access token>
{"requestId": "<request-id header of the callback>", "abhaAddress": "ravi@sbx", "tokenNumber": "5", "expiry": "1800"}
```

- You do not send the clinic: the gateway finds it from `requestId` (the scan it received). You can only answer scans
  of your own clinics; anything else is `404 SHARE_REQUEST_NOT_FOUND`. ABDM gets the clinic's id in `X-HIP-ID`.
- `expiry` (seconds, default 1800) is optional. `200 {"status": "sent"}`; `202 {"status": "queued"}` when ABDM
  is down: the worker retries it.
- The gateway sends it to `POST /api/hiecm/patient-share/v3/on-share` in ABDM's format
  (`acknowledgement` + `response.requestId`).

### The clinic QR

```
POST /api/v1/share/qr        Authorization: <access token>
{"hipId": "IN0810041408", "callbackUrl": "<optional, accepted for i-medicus>", "counterId": "<optional>"}
-> {"success": true, "type": "Success", "data": "data:image/png;base64,...", "code": "IN0810041408",
    "payload": {"hipId": "...", "hipName": "..."}, "requestId": "..."}
```

The body is the one i-medicus already sends and the answer keeps the shape its dashboard reads (`type`, `data` as
`<img src>`). `payload` is the JSON the QR holds. Only your own registered clinics (`404 HIP_NOT_FOUND` otherwise).
The payload layout (`hipId`, `hipName`, `counterId`) is built in `share_service.facility_qr`; check it against ABDM's
Scan & Share spec in the sandbox.

## How the gateway finds the facility

ABDM does not always put the facility id in the same place, so the gateway looks in this order: the `X-HIP-ID`
header, then the body (`metaData.hipId`, `hip.id`, `hip.hipId`, `hipId`, `notification.consentDetail.hip.id`,
`consentDetail.hip.id`). If none is found and exactly one facility is registered, that one is used. Otherwise the
callback is stored with status `unrouted` and logged as an error: nothing is lost, it just is not delivered.

## Authenticating ABDM

ABDM signs each callback with a JWT (RS256, `Authorization: Bearer ...`). The gateway verifies it against ABDM's
public keys (`ABDM_JWKS_URL`, cached; refreshed once when a new key id shows up).

| `CALLBACK_AUTH_MODE` | Behaviour |
|---|---|
| `jwt` (default) | invalid or missing token: `401 CALLBACK_AUTH_FAILED`, nothing stored |
| `observe` | verify, store the result in `callback_events.auth_result`, accept anyway. For looking at real sandbox callbacks first. |
| `off` | no verification. Local mock ABDM only. |

With `ABDM_ENV=prod` only `jwt` is accepted (the gateway refuses to start otherwise).

## Duplicates

ABDM may send the same callback twice. A callback with the same `REQUEST-ID` and path as one already stored is
answered `202` and dropped.

## Retries and the worker

Run the worker next to the web app (`docker compose` has a `worker` service):

```bash
flask worker run          # loops; SIGTERM / Ctrl+C stop it after the current batch
flask worker run --once   # one pass (cron, debugging)
```

A failed delivery is retried after 2 s, 10 s, 1 min, 5 min, 15 min and 1 h (`OUTBOX_BACKOFF_SECONDS`, +-20 %
jitter), 6 tries in all (`OUTBOX_MAX_ATTEMPTS`). Then the job is `dead` and the callback `failed`; its body is
kept so it can be inspected. Several workers can run at once: a job is claimed atomically, and a job whose worker
crashed is taken over after `OUTBOX_LOCK_SECONDS`.

## Registering the bridge URL with ABDM (sandbox)

ABDM sends callbacks to the *bridge URL* of the NHA client. These commands change settings on ABDM, so they only
run against the **sandbox** (`ABDM_ENV=sbx`), refuse production hosts, and print what they would send unless you add
`--confirm`:

```bash
flask abdm bridge-url set --url https://abdm-gateway.example.com            # dry run
flask abdm bridge-url set --url https://abdm-gateway.example.com --confirm  # sends it
flask abdm services add --hip-id IN0110005723 --name "City Clinic" --confirm
```

The bridge URL belongs to the NHA client id (`NHA_CLIENT_ID`): setting it redirects *all* callbacks of that client.
Never run it with a client id that another system still uses.

## Data at rest

Callback bodies are health data. They are stored encrypted (AES-256-GCM, `ENCRYPTION_KEY`; set the same key on
every gateway instance and keep it safe: without it stored callbacks cannot be read). `flask audit purge` removes
finished callbacks older than `CALLBACK_RETENTION_DAYS`.

## Trying it locally (no ABDM, no client app)

One command plays the whole story and prints PASS / FAIL for every step (own mock ABDM, gateway, worker and fake
client app, throwaway database, nothing real is contacted):

```bash
python scripts/e2e_local.py          # add --keep to leave everything running and poke at it yourself
```

The same thing by hand:

```bash
python scripts/mock_abdm.py        # fake ABDM on :5099, also signs and sends callbacks
python scripts/fake_client.py      # fake client webhook on :5055
# .env: ABDM_GATEWAY_URL=http://127.0.0.1:5099  ABDM_ABHA_URL=http://127.0.0.1:5099
#       NHA_CLIENT_ID=mock-client NHA_CLIENT_SECRET=mock-secret
#       CALLBACK_ALLOW_HTTP=true CALLBACK_ALLOW_PRIVATE_URLS=true
flask hips add --client <client_id> --hip-id IN0110005723 --callback-url http://127.0.0.1:5055/api/abdm-webhook
python run.py &  flask worker run &
curl -X POST http://127.0.0.1:5099/mock/callback -H "Content-Type: application/json" \
  -d '{"path": "/api/v3/hip/patient/share", "body": {"metaData": {"hipId": "IN0110005723"}}}'
```

## Tests

```bash
pytest --cov=src.app.callbacks --cov=src.app.outbox --cov=src.app.api.v1.hips --cov=src.app.abdm.fidelius --cov-fail-under=100
```

## Fidelius (health record encryption, used from M2)

`src/app/abdm/fidelius.py` encrypts and decrypts health records exchanged between a HIP and a HIU. ABDM uses
Curve25519 in its short Weierstrass form (not X25519), nonce XOR, HKDF-SHA256 and AES-256-GCM. The implementation is
checked against the test vector published with ABDM's reference tool
([fidelius-cli](https://github.com/mgrmtech/fidelius-cli)): public keys, ciphertext and X.509 encoding match byte
for byte (`tests/unit/test_fidelius.py`).

```python
from src.app.abdm import fidelius

mine = fidelius.generate_key_material()                 # new key pair + nonce for ONE transfer
block = fidelius.key_material_block(mine)               # the `keyMaterial` object to send to ABDM
theirs_key, theirs_nonce = fidelius.read_key_material_block(their_block)   # rejects expired / malformed blocks

encrypted = fidelius.encrypt(fhir_json, mine.private_key_b64, mine.nonce_b64, theirs_key, theirs_nonce)
plain = fidelius.decrypt(encrypted, mine.private_key_b64, mine.nonce_b64, theirs_key, theirs_nonce)
```

Public keys may be given raw (65 bytes) or as X.509; both are accepted. Keys are validated (on the curve, prime
order subgroup) before use. The arithmetic is plain Python and not constant time: use a new key pair per transfer.

## Sending ABDM replies that must not be lost

`src/app/outbox/abdm_calls.py` -> `send_or_queue(path, body)` calls ABDM at once and, if ABDM is down or answers with
a temporary error, queues the call for the worker (same backoff as above). It only works for calls to the ABDM
gateway; calls to the ABHA service (OTPs, logins) are never queued, since they must not be sent twice.

## Looking at the queue

```bash
flask outbox stats                       # pending / processing / dead, oldest waiting time
flask outbox list --status dead
flask outbox retry <job_id>              # give a dead job a fresh set of attempts
flask callbacks list --status unrouted   # callbacks no HIP matched
flask callbacks reroute <event_id> --hip-id IN0110005723
```

`GET /ready` also shows the queue (`pending`, `processing`, `dead`, `oldestPendingSeconds`).
