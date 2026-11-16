# M1 (ABHA) security controls and what was verified

Controls the gateway applies to every M1 flow (ABHA creation, login, profile, card and QR, Scan & Share), where each one is,
and how it was checked. i-medicus keeps its screens; the controls live in the gateway.

| Control | How | Where | Verified by |
|---|---|---|---|
| Only authenticated clients | No token, a wrong secret or a garbage token is 401 (`MISSING_TOKEN`, `INVALID_CLIENT`, `INVALID_TOKEN`) | `middleware/tenant_auth.py` | Live on 13 Nov 2026 and `tests/contract` |
| Aadhaar, OTP and mobile never travel in clear to ABDM | Encrypted with ABDM's public key (RSA/ECB/OAEP SHA-1, MGF1) before the call; the key is fetched from ABDM | `abdm/crypto.py` | Contract tests decrypt the value the gateway sent (`test_m1_login_profile.py`) |
| Aadhaar, OTP, mobile, tokens and secrets masked in logs and the audit table | `********2346`, `******3210`, `***` | `utils/masking.py`, `middleware/audit.py` | Live: a request with a fake Aadhaar, OTP, mobile and secret left no raw value in `api_audit_logs` |
| The ABHA user token stays out of logs | The app log records the path only; gunicorn and nginx leave out the query string, because profile/card/QR GETs can carry `?xToken=` | `middleware/request_context.py`, `docker-entrypoint.sh`, the server's gunicorn and nginx config | Live on the sandbox server: a request with `?xToken=TESTVALUE` is logged without it |
| A client can continue only its own ABDM transactions | The `txnId` of every OTP flow is recorded with the client that started it; another client gets 403 | `services/txn_service.py`, `models/abdm_transaction.py` | `test_txn_of_another_client_is_forbidden` (enrolment and account flows), `test_aadhaar_verify_refuses_another_clients_txn` |
| Input is validated before ABDM is called | Aadhaar number format, OTP, mobile, auth method/scope; a bad body is 400 `VALIDATION_ERROR` or `INVALID_AUTH_METHOD`, never a 500 | `common/validators.py`, the services | Live and contract tests (an empty body, malformed JSON, a wrong scope) |
| Double clicks and client retries do no harm | Same POST while the first runs is 409; with an `Idempotency-Key` the first answer is replayed | `middleware/idempotency.py` | Live on 13 Nov 2026 (`Idempotent-Replayed: true`; same key with another body is 422) |
| ABDM's rate limit reaches the user | An OTP that ABDM refuses with 429 ("request for new OTP after N seconds") is passed through with its text | `abdm/http.py` | `test_m1_*` contract tests |
| Errors do not expose internals | One error shape with a code and a message; unhandled errors are 500 `INTERNAL_ERROR` with the details only in the log | `errors.py` | An unhandled error answers a fixed text ("Something went wrong. Please contact support with the requestId."); seen live as `INTERNAL_ERROR` when a local database was one migration behind |
| Per-client rate limit and allowed IPs | `rate_limit` and `allowed_ips` on the client | `models/api_client.py`, `middleware/tenant_auth.py` | `tests/unit` |

## Sandbox run through the gateway (13 Nov 2026)

ABHA address `singh_singh22.2202@sbx`, a test patient, from the i-medicus local setup:

| Step | Result |
|---|---|
| `POST auth/session` | token for the client |
| `POST abha/search/abha-address` | the ABHA number and address found, auth methods `MOBILE_OTP`, `AADHAAR_OTP` |
| `POST abha/login/abha-address/otp` (`scope: mobile-verify`) | OTP sent to the patient's mobile (`******3981`), a `txnId` returned |
| `POST abha/login/abha-address/verify` | `authResult: success`, a user token and the user's ABHA address |
| `GET abha/phr/profile` | 200, the profile with the ABHA number |
| `GET abha/phr/card` | 200, `image/png`, 197 KB |

Notes: a wrong `scope` is refused by the gateway (`INVALID_AUTH_METHOD`); the PHR token (ABHA address login) works with
`abha/phr/profile` and `abha/phr/card`, while `abha/profile` and `abha/profile/qr` need the ABHA-account token of a login
by ABHA number (ABDM answers 401 to a PHR token there). ABHA **creation** needs a real Aadhaar number and its OTP, so it
was not run by hand on the sandbox; every step of it (Aadhaar OTP, verify, mobile OTP and verify, address suggestions and
creation, email link) is covered by the contract tests in `tests/contract/test_m1_enrolment.py` and
`test_m1_app_compat.py`, which pin ABDM's path, headers and body, and by the PHP smoke test of i-medicus' own Guzzle
calls against the gateway (33 of 33 checks).

## Login by ABHA number, profile, card and QR (13 Nov 2026)

Same test patient, by ABHA number with a mobile OTP:

| Step | Result |
|---|---|
| `POST abha/login/abha-number/mobile/otp` | OTP sent to the mobile, a `txnId` returned |
| `POST abha/login/abha-number/mobile/verify` | `authResult: success`; the answer holds `token`, `expiresIn`, `refreshToken`, `refreshExpiresIn`, `accounts` (one account, so the token is the ABHA-account token and `login/mobile/select-account` is not needed) |
| `GET abha/profile` (`X-Token`) | 200, name, ABHA number and ABHA address |
| `GET abha/profile/card` | 200, `image/png`, 202 KB |
| `GET abha/profile/qr` | 200, `image/png`, 13 KB |
| `GET abha/profile` without a token | 400 `MISSING_X_TOKEN`, ABDM is not called |
| `GET abha/profile` with a garbage token | 400 `ABDM_ERROR`, ABDM's own refusal passed through |

Every call has a row in `api_audit_logs` (route, HTTP status, error code, the ABDM endpoint and its status, latency), and the
OTP flows have a row in `abdm_transactions` (flow `login_abha_number_mobile`, step `verified`). With several ABHAs on one
mobile number the verify answer carries a T-token that `login/mobile/select-account` exchanges for the account token.

## Scan & Share (13 Nov 2026)

| Step | Result |
|---|---|
| Clinic QR from `POST /api/v1/share/qr` | A URL, `https://phrsbx.abdm.gov.in/share-profile?hip-id=<facility>&counter-id=1`. The ABHA sandbox app answered "Invalid QR code" to a JSON payload and to a URL without `counter-id`; with `counter-id` (`1`, `desk1`, `12345`) it opened the share screen |
| Patient scans and shares in the ABHA app | ABDM's `POST /api/v3/hip/patient/share` reached the gateway (through the tunnel) and was accepted with 202; the facility was found from the callback |
| Forward to i-medicus and the `share/on-share` reply | Not run live. The local i-medicus is on `127.0.0.1`, which the callback URL guard refuses (`callbackUrl must not point to a private or internal address`, `CALLBACK_ALLOW_PRIVATE_URLS` is for local development only); a later scan did not reach the gateway. Covered by `tests/contract/test_share_*.py` and the callback forwarding tests |

Open: repeat the forward and `on-share` leg on the sandbox server, where i-medicus has a public address.
