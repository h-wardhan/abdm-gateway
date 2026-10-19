# Error codes

Every error uses the same envelope:
```json
{
  "success": false,
  "code": "error",
  "message": { "error": { "code": "INVALID_AADHAAR", "message": "Invalid Aadhaar number. ...", "details": {"field": "aadhaarNumber"} } },
  "requestId": "6f1c..."
}
```
`details` is only present on some validation errors. The `requestId` can be used to find the
request in `logs/app.log`, `logs/abdm.log`, `api_audit_logs` and `abdm_error_logs`.

## Gateway errors

| HTTP | code | Meaning / what to do |
|---|---|---|
| 400 | `VALIDATION_ERROR` | A required field is missing (`details.field`). |
| 400 | `INVALID_AADHAAR` | Not 12 digits, or the Verhoeff checksum failed. ABDM is not called. |
| 400 | `INVALID_OTP` | The OTP must be 6 digits. |
| 400 | `INVALID_MOBILE` | Must be a 10-digit Indian mobile number (a leading `91` / `+91` is accepted). |
| 400 | `INVALID_ABHA_NUMBER` | Must be 14 digits, with or without hyphens. |
| 400 | `INVALID_ABHA_ADDRESS` | Not a valid ABHA address. |
| 400 | `INVALID_EMAIL` | Not a valid email address. |
| 400 | `INVALID_TXN_ID` | The `txnId` is malformed. |
| 400 | `INVALID_AUTH_METHOD` | The login method must be `mobile` / `aadhaar` (or `mobile-verify` / `aadhaar-verify`). |
| 400 | `MISSING_X_TOKEN` | The ABHA user token is missing. Send `xToken` or the `X-Token` header. |
| 400 | `INVALID_IDEMPOTENCY_KEY` | `Idempotency-Key` must be 1-128 characters: letters, digits, `.`, `_`, `:` or `-`. |
| 401 | `MISSING_TOKEN` | No `Authorization` header. |
| 401 | `INVALID_TOKEN` | The access token is malformed or has a bad signature. |
| 401 | `TOKEN_EXPIRED` | The access token has expired. Create a new session or refresh. |
| 401 | `TOKEN_REVOKED` | The client secret was rotated or the client was disabled after this token was issued. |
| 401 | `INVALID_CLIENT` | Wrong clientId or clientSecret. |
| 401 | `CLIENT_MISMATCH` | The `Client-Id` header does not match the token. |
| 403 | `CLIENT_DISABLED` | This client app has been disabled. |
| 403 | `IP_NOT_ALLOWED` | The caller's IP is not in the client's allowlist. |
| 403 | `TXN_FORBIDDEN` | The `txnId` was started by another client app. |
| 409 | `DUPLICATE_REQUEST` | The same POST (or the same `Idempotency-Key`) is still being processed. Wait for the first response instead of sending it again. |
| 422 | `IDEMPOTENCY_KEY_REUSED` | This `Idempotency-Key` was already used for a different request. Use a new key per request. |
| 404 | `NOT_FOUND` | Unknown route. |
| 429 | `RATE_LIMITED` | The gateway's per-client (or per-IP, for session) rate limit was exceeded. |
| 500 | `INTERNAL_ERROR` | Unexpected bug. Look up the `requestId` in the logs. |

## ABDM errors
When ABDM rejects a request, the gateway keeps **ABDM's HTTP status, code and message text**, so
client apps can show meaningful messages.

| HTTP | code | Example message | Meaning |
|---|---|---|---|
| 400 | `ABDM-1100` | `Invalid OTP value.` | Wrong or expired OTP during enrolment |
| 400 | `ABDM-VALIDATION` | `Invalid LoginId` | ABDM field validation (all fields joined) |
| 401 | `ABHA_TOKEN_INVALID` or ABDM code | `Token expired` | The ABHA user token (`xToken`) is invalid or expired: log in to ABHA again |
| 404 | ABDM code | `ABHA address not found` | Unknown ABHA address |
| 422 | `ABDM-1204` | `UIDAI Error code : 111 : ...` | UIDAI errors: 110/111/112 mean no verified mobile is linked to the Aadhaar |
| 429 | ABDM code | `You can request for new OTP after 30 seconds` | ABDM's OTP throttling |
| 502 | `ABDM_SERVER_ERROR` | | ABDM returned a 5xx |
| 502 | `ABDM_AUTH_FAILED` | | ABDM rejected **the gateway's** NHA credentials. This is a gateway configuration problem, not the client's |
| 502 | `ABDM_EMPTY_FILE` | | ABDM returned no card or QR bytes |
| 503 | `ABDM_UNAVAILABLE` | | ABDM is not reachable |
| 503 | `NHA_CREDENTIALS_MISSING` | | `NHA_CLIENT_ID` / `NHA_CLIENT_SECRET` are not set on the gateway |
| 504 | `ABDM_TIMEOUT` | | ABDM did not answer within `ABDM_TIMEOUT_READ` |

## Wrong OTP on login verify
ABDM answers a wrong OTP on login verify with HTTP 200 and `authResult: "failed"`. The gateway keeps
that as HTTP 200, but sets `success: false`:
```json
{ "success": false, "data": { "authResult": "failed", "message": "Please enter a valid OTP...", "txnId": "..." }, "message": "...", "requestId": "..." }
```
