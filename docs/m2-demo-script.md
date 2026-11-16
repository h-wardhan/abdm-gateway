# M2 demo script (for i-medicus), about 15 minutes

Audience: the i-medicus team. Message: **i-medicus only changed paths; every ABDM / FHIR / crypto step now runs on the
gateway.**

## Before the demo (once)
- Two tunnels up (gateway, i-medicus), gateway + worker + i-medicus running, `flask abdm bridge-url set --confirm` done.
- `ABDM_CLINIC_ID` in i-medicus `.env` = the HIU facility; both clinics registered with their `hfr_id`.
- ABHA SBX app logged in as the test patient; Swagger open at `<gateway>/docs`.

## Flow (say, do, show)
| # | Say | Do | Show |
|---|---|---|---|
| 1 | "Discovery and linking go through the gateway." | HIP clinic admin links a care context (appointment list, Send Data to ABHA). | Gateway log `hip/link/...`; `<id>_carecontext_...` under Linked Facilities in the ABHA app. |
| 2 | "A second clinic (HIU) asks for the data." | HIU doctor: ABHA Consents, + Request Consent. | `consent_requests` row; the request in the ABHA app. |
| 3 | "The patient decides." | Approve in the ABHA app. | Callbacks `consent/request/hip/notify` and `health-information/request` stored and forwarded. |
| 4 | "i-medicus sends its payload, the gateway builds the FHIR Bundle." | Swagger: `POST /fhir/<type>` with the sample body. | A valid Bundle with its entries. |
| 5 | "Keys, encryption, push and status are four calls." | Swagger: `GET /fhir/keys`, `POST /fhir/encrypt`, `POST /hip/health-information/push`, `POST /hip/health-information/notify`. | The push goes straight to the HIU, never through ABDM; `GET /hip/transfers/<txn>` = `NOTIFIED`. |
| 6 | "The HIU sees it." | HIU: ABHA Consents, eye icon. | The viewer with hospital, doctor, patient and the Bundle. |

## Failure cases to show (about 1 minute each)
| Case | How | Expected |
|---|---|---|
| Consent revoked before the push | Grant, revoke in the ABHA app (Consents, Granted, Revoke), then call push. | 403 `CONSENT_INVALID` "Consent is REVOKED", transfer `FAILED`, ABDM told (it answers `ABDM-1080`). **Run on the sandbox.** |
| Consent expired | Request with a short expiry (the i-medicus modal only takes a date: use a helper), wait, call push. | 403 `CONSENT_INVALID`, same as above. Automated test. |
| HIU rejects the push | HIU `DATA_PUSH_URL` pointing at a path that answers 404. | 502 `HIU_REJECTED_PUSH`, ABDM told `FAILED`. Automated test. |
| HIU down | Stop the HIU for one push, start it again. | 202 `queued`, the worker retries and lands it. Automated test. |
| Duplicates | Send the same push and notify twice. | `already_sent`, nothing is sent twice. Automated test. |

## What i-medicus changed (close the demo with this)
- Path swaps to `config('abdm.endpoints.*')`; `GenerateKeys` and `Encrypt` now send the credentials headers.
- `config('abdm.encryption_base_url')` is the gateway URL.
- The video-call prescription flow uses gateway routes too, with its old bodies unchanged: the gateway builds the Bundle from them and fixes the key material.
- Upstream bugs found while testing are in the Bug Sheet (not changed by us).

## Fallbacks
- A PHR / HIU transaction expires within minutes: push at once, or request again.
- Same-day care contexts need a `toDate` with a time (ABDM refuses a future `toDate`; the i-medicus modal sends a date).
- A same-day consent expiry makes i-medicus' health-information request fail (it sends a date-only expiry): use tomorrow.
- If the i-medicus consent-notify handler races, show steps 4 and 5 from Swagger.
