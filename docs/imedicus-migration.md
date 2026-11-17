# i-medicus: moving from VCDoctor to abdm-gateway (M1, M2, M3)

i-medicus used to call the VCDoctor gateway (`abdmprod.vcdoctor.com`, `api/v2/ABDMVC/*`). Its M1, M2 (HIP) and M3 (HIU)
calls now go to abdm-gateway. Rule: i-medicus gets **path changes only**; ABDM, FHIR and crypto logic lives on the
gateway. The one exception is the video-call prescription flow (see "Legacy video-call flow").

The gateway keeps the request bodies and response shapes i-medicus already uses, so only the
**paths** changed. They now live in one place: `config/abdm.php` → `endpoints`.

## What changed in i-medicus (branch `feature/abdm-gateway-m1`)

1. `src/config/abdm.php`: added the `endpoints` map (gateway paths).
2. `src/app/Services/ABDMService.php` and `src/app/Http/Controllers/Api/ABHAController.php`:
   every M1 `'api/v2/ABDMVC/...'` string is replaced by `config('abdm.endpoints.<key>')`.
   That is 59 + 4 call sites. Request bodies and response handling are unchanged.
3. `ABDMService::updateMobileNumberVerify`: the `authResult` check is now case-insensitive.
   ABDM returns `failed`, and the old code only matched `Failed`.

Later phases moved the M2 and M3 paths too (sections below). Still on their old paths: see "Not on the gateway".

## Path mapping

| # | Old VCDoctor path | `config('abdm.endpoints.*')` key | Gateway path |
|---|---|---|---|
| 0 | `CreateSession` | `session` | `POST api/v1/auth/session` |
| 1 | `GetOtpAadhar` | `aadhaar_otp` | `POST api/v1/abha/enrol/aadhaar/otp` |
| 2 | `AadharOtpverifying` | `aadhaar_verify` | `POST api/v1/abha/enrol/aadhaar/verify` |
| 3 | `UdateMobileSendOtp` | `enrol_mobile_otp` | `POST api/v1/abha/enrol/mobile/otp` |
| 4 | `VerifyMobileOtp` | `enrol_mobile_verify` | `POST api/v1/abha/enrol/mobile/verify` |
| 5 | `GetAbhaAddressSuggestions?txnId=` | `address_suggestions` | `GET api/v1/abha/enrol/address-suggestions?txnId=` |
| 6 | `UpdateAbhaAddress` (v1 and v2) | `create_address` | `POST api/v1/abha/enrol/address` |
| 7 | `RequestEmailVerificationLink` | `email_verify_link` | `POST api/v1/abha/profile/email/verify-link` |
| 8 | `findAbha` | `search_mobile` | `POST api/v1/abha/search/mobile` |
| 9 | `sendOtpAbhaByFind` | `login_mobile_otp` | `POST api/v1/abha/login/mobile/otp` |
| 10 | `verifyFindOTPABHA` | `login_mobile_verify` | `POST api/v1/abha/login/mobile/verify` |
| 11 | `loginSearch` | `search_abha_number` | `POST api/v1/abha/search/abha-number` |
| 12 | `SendOtpVerifyAbha` | `login_abha_number_mobile_otp` | `POST api/v1/abha/login/abha-number/mobile/otp` |
| 13 | `SendOtpVerifyAadhar` | `login_abha_number_aadhaar_otp` | `POST api/v1/abha/login/abha-number/aadhaar/otp` |
| 14 | `VerifyOtpAbha` | `login_abha_number_mobile_verify` | `POST api/v1/abha/login/abha-number/mobile/verify` |
| 15 | `VerifyOtpAadhar` | `login_abha_number_aadhaar_verify` | `POST api/v1/abha/login/abha-number/aadhaar/verify` |
| 16 | `abhaLoginViaAadhar` | `login_aadhaar_otp` | `POST api/v1/abha/login/aadhaar/otp` |
| 17 | `verifyLoginViaAadhar` | `login_aadhaar_verify` | `POST api/v1/abha/login/aadhaar/verify` |
| 18 | `SearchAbhaByAddress` | `search_abha_address` | `POST api/v1/abha/search/abha-address` |
| 19 | `RequestLoginOtpByAbhaAddress` | `login_abha_address_otp` | `POST api/v1/abha/login/abha-address/otp` |
| 20 | `ABHAAddressVerifyLoginOtp` | `login_abha_address_verify` | `POST api/v1/abha/login/abha-address/verify` |
| 21 | `GetAbhaProfile?xToken=` | `profile` | `GET api/v1/abha/profile?xToken=` |
| 22 | `VerifyLoginOtpProfile?xToken=` | `phr_profile` | `GET api/v1/abha/phr/profile?xToken=` |
| 23 | `DownloadAbhaCard?xToken=` | `abha_card` | `GET api/v1/abha/profile/card?xToken=` |
| 24 | `GetAbhaPhrCard?xToken=` | `phr_card` | `GET api/v1/abha/phr/card?xToken=` |
| 25 | `GetQRCodeAbha?xToken=` | `qr_code` | `GET api/v1/abha/profile/qr?xToken=` |

## Mobile-app service (`MobileAbdmService`)

The older `MobileAbdmService` (patient-side flows) used `ABDM/*` paths and reads ABDM's answer **at the top level**
(`accounts`, `token`, `ABHAProfile`, ...), not inside `data`. Those calls go to `/api/v1/abha/app/*`, which use the same
ABDM logic as the normal endpoints but answer the way that service reads. Auth is the `Client-Id` + `Client-Secret`
headers it already sends.

| Old path | `config('abdm.endpoints.*')` | Gateway path | Notes |
|---|---|---|---|
| `ABDM/get-otp` | `app_aadhaar_otp` | `POST api/v1/abha/app/aadhaar/otp` | |
| `ABDM/verify-otp` | `app_aadhaar_verify` | `POST api/v1/abha/app/aadhaar/verify` | `ABHAProfile`, `tokens`, `isNew` |
| `ABDM/verify/verifyByMobile` | `app_mobile_otp` | `POST api/v1/abha/app/mobile/otp` | one step (search + OTP, first ABHA), HTTP **201** |
| `ABDM/verify/verifyOTPMobile` | `app_mobile_verify` | `POST api/v1/abha/app/mobile/verify` | HTTP **201**; wrong OTP is HTTP 200 + `authResult: failed` |
| `ABDM/verify-email` | `app_email_verify_link` | `POST api/v1/abha/app/email/verify-link` | ABHA token in `x-token` |
| `ABDM/get-enrollment` | `app_address_suggestions` | `POST api/v1/abha/app/address-suggestions` | |
| `ABDM/update-address` | `app_address` | `POST api/v1/abha/app/address` | |
| `ABDM/verify/sendOTPVerifyABHA` | `app_abha_number_otp` | `POST api/v1/abha/app/abha-number/otp` | |
| `ABDM/verify/verifyOTPABHA` | `app_abha_number_verify` | `POST api/v1/abha/app/abha-number/verify` | |
| `ABDM/download-abha` | `app_card` | `GET api/v1/abha/app/profile/card` | bytes |
| `ABDM/QRCodeABHA` | `app_qr` | `GET api/v1/abha/app/profile/qr` | bytes |
| `ABDM/update-mobile`, `ABDM/verify-mobile` | `update_mobile`, `verify_mobile` | the normal `api/v1/abha/mobile/*` | same as `ABDMService` |

`getEnrollmentList` and `updateAbhaAddress` sent no headers before; they now send `getHeaders()` (the gateway
authenticates every call). Tests: `tests/contract/test_m1_app_compat.py`.

## Other M1 routes in the gateway (not called by i-medicus today)

Built from the ABDM Milestone-1 Postman collection; each has a contract test in
`tests/contract/test_m1_account_pending.py` that pins the collection's path, scope and body.

| Group | Gateway paths |
|---|---|
| Delete / deactivate / re-KYC | `POST api/v1/abha/account/{delete,deactivate,re-kyc}/{otp,verify}` (`method`: aadhaar / abha / password; `reasons` required for delete and deactivate) |
| Reactivate | `POST api/v1/abha/account/reactivate/{otp,verify}` |
| Password | `POST api/v1/abha/account/password`, `.../password/otp`, `.../password/verify`; login `POST .../account/login/password/{search,verify}` |
| Profile update, logout, refresh | `PATCH api/v1/abha/account/profile`, `GET .../account/logout`, `GET .../account/refresh-token` (`R-Token` header) |
| Driving licence enrolment | `POST api/v1/abha/enrol/dl/{otp,verify,create}` |
| Biometric / demographic / child enrolment | `POST api/v1/abha/enrol/biometric/{fingerprint,face,iris}`, `.../enrol/demo-auth`, `.../enrol/child` (GET lists children) |
| Forgot ABHA / retrieval | `POST api/v1/abha/login/forgot/{mobile,aadhaar}/{otp,verify}`, `.../login/retrieval/{otp,verify}` |
| Biometric login | `POST api/v1/abha/login/{abha-number,mobile,abha-address}/biometric/{fingerprint,face,iris}/{otp,verify}` |
| Benefit | `POST api/v1/abha/benefit/{search,link}`, `GET api/v1/abha/benefit/abha/<number>` |

## M2 (HIP) linking, consent and health-information acknowledgement (Days 9-11)

i-medicus keeps its handlers and bodies; only the path (and the base URL of the callback-answer client) changes.
Answers go to `/api/v1/hip/*`, authenticated with `Client-Id` + `Client-Secret`. Contract tests:
`tests/contract/test_m2_hip.py`, `test_m2_end_to_end.py`.

| Old VCDoctor path | `config('abdm.endpoints.*')` | Gateway path | Answer |
|---|---|---|---|
| `ABDMVC/OnDiscoverCareContext/...` | `hip_on_discover` | `POST api/v1/hip/care-context/on-discover` | 200 |
| `ABDMVC/OnInitLinkCareContext/...` | `hip_on_init` | `POST api/v1/hip/link/on-init` | 200 |
| `ABDMVC/OnConfirmLinkCareContext/...` | `hip_on_confirm` | `POST api/v1/hip/link/on-confirm` | 200 `success:true` |
| `ABDMVC/HIPGenerateToken/hip-link-token` | `hip_link_token` | `POST api/v1/hip/link-token` | 202 |
| `ABDMVC/LinkCareContext/hip-initiated-linking-single` | `hip_link_care_context` | `POST api/v1/hip/link/care-context` (header `x-link-token`) | 202 |
| `ABDMVC/NotifyCareContext/hip-initiated-linking-notify` | `hip_link_notify` | `POST api/v1/hip/link/notify` | 201 |
| `ABDMVC/SendSMSNotification/deep-linking-notify` | `hip_sms_notify` | `POST api/v1/hip/sms/notify` | 200 |
| `ABDMVC/NotifyConsentRequest/consent-hip-on-notify` (also `smsNotify`) | `hip_consent_on_notify` | `POST api/v1/hip/consent/on-notify` | 201 |
| `ABDM/hip-healthinformation-status-acknowledged` | `hip_hi_on_request` | `POST api/v1/hip/health-information/on-request` | 200, or 403 `CONSENT_INVALID` |

Gateway behaviour: an answer is matched to ABDM's callback by `response.requestId` (only the client's own facilities,
else 404 `CALLBACK_NOT_FOUND`); sent once (a repeat returns `status: already_sent`); queued and retried when ABDM is
down. The gateway stores every consent notification (`hip_consents`) and refuses to acknowledge a health-information
request whose consent is unknown, revoked, expired, or whose date range is outside the consent.
In i-medicus the six callback handlers are dispatched from `ABHAController::webhook`; `CreateSession` now uses the
gateway session.

## M2 FHIR: health records as ABDM document Bundles (Days 12-13)

i-medicus builds the record payload it always built (`WebhookTrait::FHIRApi` and the `*Record` methods) and posts it to
the gateway, which answers the **Bundle itself at the top level** (no `data` envelope). The Bundle follows the NRCeS ABDM
FHIR R4 IG 6.5.0; the Organization is the client's HIP. A payload that cannot become a valid record is HTTP 422
`INVALID_RECORD` with the field in `details`.

| `config('abdm.endpoints.*')` | Gateway path | Record | Payload keys of its own |
|---|---|---|---|
| `fhir_prescription` | `POST api/v1/fhir/prescription` | PrescriptionRecord | `medications[]`, `binaryDocument` |
| `fhir_op_consultation` | `POST api/v1/fhir/op-consultation` | OPConsultRecord | chief complaints, vitals, allergies, procedures, ... |
| `fhir_diagnostic_report` | `POST api/v1/fhir/diagnostic-report` | DiagnosticReportRecord | `diagnosticReports[]`, `documentReference` |
| `fhir_discharge_summary` | `POST api/v1/fhir/discharge-summary` | DischargeSummaryRecord | as OP consult + diagnoses, care plan |
| `fhir_immunization` | `POST api/v1/fhir/immunization` | ImmunizationRecord | `immunizations[]`, `documentReference` |
| `fhir_health_document` | `POST api/v1/fhir/health-document` | HealthDocumentRecord | `documentReference[]` |
| `fhir_wellness` | `POST api/v1/fhir/wellness` | WellnessRecord | vitals, measurements, ... |
| `fhir_invoice` | `POST api/v1/fhir/invoice` | InvoiceRecord | `lineItems[]` |

Common blocks in every payload: `patient` (`name`, `healthId` = ABHA address, `gender`, `birthDate`, `phone`),
`practitioner` (`name`, `licenseNumber`), `organization` (`name`; the facility id is the client's HIP), `encounter`
(`encounterId`, `classCode`). `POST api/v1/fhir/validate` checks a Bundle someone else made. The structural check needs
`fhir.resources`; `FHIR_STRUCTURAL_CHECK=false` turns it off for a development machine that cannot load it (always on
in production). Tests: `tests/fhir/`, `tests/contract/test_fhir_routes.py`.

## M2 data transfer and M3 HIU: keys, encryption, push, status, consent requests (Days 14-15)

i-medicus is both a HIP (it sends records) and a HIU (it asks other clinics and receives the push on its own
`/api/abdm-webhook`), so both roles moved. Same rule: i-medicus keeps its bodies, only the paths changed.

**HIP: after the health-information request is acknowledged**

| Old VCDoctor path | `config('abdm.endpoints.*')` | Gateway path | What the gateway adds |
|---|---|---|---|
| `FideliusEncryptionDecryption/GenerateKeys/keys` (GET) | `fhir_keys` | `GET api/v1/fhir/keys` | `{privateKey, publicKey, nonce}`; the public key is the raw 88-character point (i-medicus' key columns are varchar(255)); not stored by the gateway |
| `FideliusEncryptionDecryption/Encrypt/encrypt` | `fhir_encrypt` | `POST api/v1/fhir/encrypt` | `{encryptedData, keyToShare}`; refuses a `senderPublicKey` that is not the private key's |
| `ABDMVC/SendNotification/calling-data-push-url` | `hip_hi_push` | `POST api/v1/hip/health-information/push` | see below |
| `ABDMVC/SendNotificatin/data-flow-notification-request` | `hip_hi_notify` | `POST api/v1/hip/health-information/notify` | see below |
| (new) | - | `GET api/v1/hip/transfers/<transactionId>` | state of a transfer: status, care contexts, attempts, last error |

`hi_on_request` (the acknowledgement i-medicus already sends) records a **transfer** (`hip_transfers`, migration `0005`)
with the push URL, the consent and the HIU's key expiry **as ABDM gave them**. Status: `ACKNOWLEDGED -> PUSH_QUEUED ->
PUSHED -> NOTIFIED | FAILED`.

- **push.** Pushes only to the URL of the acknowledged request (a different `pushUrl`/`dataPushUrl` is 400
  `PUSH_URL_MISMATCH`), https only and never a private address (`PUSH_ALLOW_HTTP` / `PUSH_ALLOW_PRIVATE_URLS` for
  staging and local runs). It re-checks the consent first (403 `CONSENT_INVALID`: revoked, expired) and that every
  `careContextReference` is covered (403 `CARE_CONTEXT_NOT_CONSENTED`), then the key material. It adds
  `pageNumber`/`pageCount`, replaces i-medicus' placeholder `checksum` with the MD5 of the content, sends only ABDM's
  fields and no token, and follows no redirect. HIU unreachable / 5xx / 429: **HTTP 202 `queued`**, the worker retries
  with backoff (`OUTBOX_*`). HIU answers 4xx: **HTTP 502 `HIU_REJECTED_PUSH`** (with what the HIU said). In both
  permanent cases the gateway tells ABDM `FAILED` itself. A repeated push is `already_sent`.
- **notify.** `TRANSFERRED` / `FAILED`. Every care context pushed is reported (i-medicus reports only the first), the
  notifier is the facility, not what the body says. `TRANSFERRED` while the push is still queued is held (HTTP 202) and
  sent when the push lands; before any push it is 400 `NOT_PUSHED`. ABDM down: queued and retried. Repeats are
  `already_sent`. ABDM answers `ABDM-1080 Invalid Consent artefact id` to a status for a consent that is revoked or a
  transaction that has expired; the gateway keeps that in the transfer's `lastError`.

**HIU**

| Old VCDoctor path | `config('abdm.endpoints.*')` | Gateway path | ABDM call |
|---|---|---|---|
| `ABDMVC/InitiateConsentRequest/consent-init-request-by-hiu` | `hiu_consent_init` | `POST api/v1/hiu/consent/init` (202) | `consent/v3/request/init`, `REQUEST-ID` = the `requestId` i-medicus keeps |
| `ABDMVC/NotifyConsentHiuRequest/consent-request-hui-on-notify` | `hiu_consent_on_notify` | `POST api/v1/hiu/consent/on-notify` | `consent/v3/request/hiu/on-notify`, matched by `response.requestId`, sent once |
| `ABDMVC/FetchConsent/fetch-consent-detail` | `hiu_consent_fetch` | `POST api/v1/hiu/consent/fetch` | `consent/v3/fetch` |
| (new, i-medicus does not call it yet) | - | `POST api/v1/hiu/consent/status` | `consent/v3/request/status` for `consentRequestId`; the answer arrives as the `hiu/consent/request/on-status` callback; queued if ABDM is down |
| `ABDMVC/SendHealthInformationRequest/health-information-request` | `hiu_hi_request` | `POST api/v1/hiu/health-information/request` (202) | `data-flow/v3/health-information/request`, with our key and `dataPushUrl` |
| `FideliusEncryptionDecryption/Decrypt/decrypt` | `fhir_decrypt` | `POST api/v1/fhir/decrypt` | answers a JSON **string** holding `{"decryptedData": "<plain text>"}` (i-medicus decodes twice and keeps the object in `consent_health_data.fhir_json`; its viewer reads `decryptedData`) |

The facility is the client's own `hipId` (the id is the same in both roles) and goes to ABDM as `X-HIU-ID`. Consent init
and the health-information request create things at ABDM and are never queued; the on-notify answer and the fetch are.
HIU callbacks (`api/v3/hiu/...`) are routed by `X-HIU-ID`, or by the `hiu` id in the body. The pushed data goes
**straight from the HIP to the HIU's `dataPushUrl`**, never through ABDM or the gateway; i-medicus' webhook keeps
receiving it.

**Changes in i-medicus beyond a path.** `GenerateKeys` and `Encrypt` now send the credentials headers (they sent none),
and `config('abdm.encryption_base_url')` is the gateway's URL (`ABDM_DATA_ENCRYPTION_URL` is no longer read).
The sender private key is still kept in i-medicus' database, as before.

## M3 HIU: consent tracking and expiry block (Days 16-19)

The gateway keeps what ABDM's callbacks say about the consents the facility asked for (`hiu_consents`, `hiu_transfers`,
migration 0007), so that rules do not depend on i-medicus remembering them. i-medicus' bodies are unchanged; the new
behaviour is on routes it already calls plus two read routes.

| Callback (stored when it arrives) | Kept |
|---|---|
| `hiu/consent/request/notify` | status of the request (GRANTED, DENIED, REVOKED, EXPIRED) and the artefact ids |
| `hiu/consent/on-fetch` | the artefact: granted date range, hi types, `dataEraseAt`, the HIP |
| `hiu/health-information/on-request` | the `transactionId` of our request (matched by its REQUEST-ID) |

- `POST /api/v1/hiu/health-information/request` refuses a consent the gateway knows to be denied, revoked, expired
  (`dataEraseAt` passed) or not covering `fromDate`/`toDate`: 403 `CONSENT_INVALID` with `details.reason`
  (`REVOKED`, `DENIED`, `EXPIRED`, `OUT_OF_RANGE`); nothing is sent to ABDM and no transaction is opened. A consent the
  gateway never heard of (made before this was deployed) is let through; `HIU_REQUIRE_KNOWN_CONSENT=true` refuses those too.
- `GET /api/v1/hiu/consent/{consentId}`: status, `usable` / `reason`, date range, erase date and the requests made under
  it (`transactionId`, status). A client sees only its own consents (404 `CONSENT_NOT_FOUND`).
- `POST /api/v1/fhir/decrypt` accepts an optional `transactionId` or `consentId`; when that consent has ended the records
  are not opened (403 `CONSENT_INVALID`). i-medicus does not send either today.

What stays in i-medicus: the received records (`consent_health_data`) and the push receiver (the HIP pushes straight to
`DATA_PUSH_URL`, not through the gateway). Erasing records after a consent ends is therefore not done by the gateway, and
it is out of scope by decision. The decrypt guard works only once i-medicus sends the `transactionId` with the push
(not done).

## M2 on the sandbox with the staging i-medicus: what the gateway does so a user needs no manual step (16 Nov 2026)

Found by running the whole HIP flow from the i-medicus screens against the ABDM sandbox. Two of the problems are i-medicus'
ordering; the gateway absorbs them, so i-medicus is not changed.

**Link token** (`services/link_token_service.py`, table `hip_link_tokens`). ABDM issues the token asynchronously (callback
`hip/token/on-generate-token`). i-medicus asks for it and links at once (ABDM: "Invalid X Auth token"), and asks again
(ABDM: "Duplicate Link token request"). The gateway keeps the token from the callback and:
- a `link/care-context` or `link/notify` call uses the token ABDM issued, waits up to `HIP_LINK_TOKEN_WAIT_SECONDS` (10) for one
  that is on its way, and falls back to the client's token when there is none;
- `link-token` for a patient whose token exists is not sent to ABDM again: the stored callback is delivered to i-medicus
  once more (it saves the token) and the answer says `linkToken: already issued`; one already requested in the last
  `HIP_LINK_TOKEN_PENDING_SECONDS` (60) is not requested twice (`linkToken: requested`); a request ABDM refused is forgotten.

**The gateway acknowledges a GRANTED consent notice itself and holds it until its request arrives** (`services/hip_pairing.py`,
`outbox/worker.py`, `outbox_jobs.hold_consent_id`). On `consent/request/hip/notify` i-medicus sleeps 5 s, saves the consent id on its
rows, acknowledges, sleeps 10 s, then builds the records with the transaction id and keys it holds at that moment. ABDM sends the
HIP the health-information request (transaction id + HIU key) only **after** the acknowledgement, about 14 s later, and the HIU
may send it much later (eight record types make eight artefacts, which i-medicus as HIU handles one by one). With the
acknowledgement coming from i-medicus (+5 s) the request is handled after the records were built: they go out under the previous
round's transaction (ABDM: "Invalid Consent artefact id") or without keys (`fhir/encrypt` 400). Holding i-medicus' acknowledgement
until the request arrives cannot work either (the 25 s ran out eleven times in one test).

What the gateway does now (needs nothing from i-medicus):
1. It acknowledges the notice to ABDM **at once** after storing it (`ack_notice_now`; a held notice in the database is all ABDM asks
   of a HIP). i-medicus' own acknowledgement later finds it sent (`already_sent`) and returns at once. `HIP_ACK_NOTICE_AT_ONCE`
   (true; false = the older behaviour, with `HIP_CONSENT_ACK_WAIT_SECONDS` as a fallback wait).
2. The notice waits **in the database** (not in a thread) until the request of its consent has arrived, at most
   `HIP_NOTICE_HOLD_SECONDS` (900); the request releases it.
3. The request goes to i-medicus only once its notice has been running for `HIP_PAIR_DELAY_SECONDS` (8): after i-medicus saved the
   consent id (+5 s), before it builds the records (+16 s). The worker checks this (`worker.request_ready`) and keeps slots for
   requests (up to `WORKER_CONCURRENCY` beside the notices) so a request always finds one.
4. Notices of one patient go one at a time (the worker's lanes), because they share i-medicus' rows.

**The delivery context: the gateway does not trust i-medicus' rows** (`hip_transfer_service.active_transfer / fit_encrypt / _resolve`,
`HIP_ONE_NOTICE_PER_CLIENT`, `HIP_TRANSFER_WAIT_SECONDS`, migration 0009). Acknowledging and holding alone left a window of a few
seconds (the sandbox run of 17 Nov 2026: the request reached i-medicus 8 s after its notice, i-medicus' request handler then took
~10 s before it saved the keys, the records were built 1 s earlier: `fhir/encrypt` 400 `senderPrivateKey is required`, nothing
pushed). i-medicus builds inside its notice handler and reads transaction and keys from its own rows, so a late or overwritten
row means empty keys or a previous consent's transaction. The gateway now:
1. stores ABDM's health-information request when it **arrives** (transaction, consent, HIU key and nonce, push URL) and
   acknowledges it itself (`ack_request_now`); i-medicus' acknowledgement later is `already_sent`;
2. lets **one consent notice at a time per client app** run (worker lane), so while i-medicus handles a notice every
   `fhir/encrypt`, `push` and `notify` call of that client belongs to that notice's consent (`active_transfer`);
3. `fhir/encrypt` then uses the HIU key and nonce of that request whatever the body says, and one sender key for all records of
   the transfer (the client's if usable, else a fresh one kept sealed in `hip_transfers.sender_private_key_enc` and dropped when
   the transfer ends); `push` goes out under that transaction, to the URL ABDM gave, with the gateway's own `keyMaterial` (the
   client's has a null nonce when its keys are empty); `notify` tells ABDM about that transaction and consent. Only when the
   care contexts of the call are covered by that consent; otherwise the client's transaction is used as before;
4. a call that arrives before the request waits up to `HIP_TRANSFER_WAIT_SECONDS` (30) for it. With no notice being handled (the
   video-call flow), or two at once, nothing changes: the old rules apply.
`HIP_ONE_NOTICE_PER_CLIENT=false` turns this off (one notice at a time per patient, as before).

**Verified** (`tests/contract/test_m2_delivery_context.py`, 18 tests; `scripts/simulate_hip_flow.py`: real receiver, worker and
routes, a fake i-medicus with its rows and sleeps, a fake HIU that decrypts every page): the old gateway fails the simulation
(`encrypt 400` for every consent, as on the sandbox); the new one delivers every consent exactly once under its own transaction and
tells ABDM TRANSFERRED once each: 8/8 (4 patients x 2 consents), 30/30 (10 x 3), 12/12 with an i-medicus request handler of 60 s.
Automated: two record types, all eight types, one 3 MB record, one consent of 60 care contexts, all with empty i-medicus keys.

**Cost and limits, stated plainly.** One notice at a time per client app means throughput of one consent per client per
17-45 s (i-medicus' handler), about 100-200 an hour per facility owner, many clients in parallel. That is correctness first; it is
not lakhs an hour for one client. The lasting fix for throughput is still in i-medicus (below). Not tested: Postgres under load,
ABDM under load. A builder that already ran cannot run again (one-shot), so a repeated request logs a 422. ABDM's expiry of a
transaction is not known. If two consents cover the same care contexts, the call is given to the notice being handled.

**The worker runs jobs side by side** (`WORKER_CONCURRENCY`, 16; `outbox/worker.py`). Jobs run in a pool; the HIP consent notices
go **one at a time per patient**. `WORKER_CONCURRENCY=1` is the old behaviour.

**Scale and what limits it.** i-medicus holds a PHP process for 17-45 s per consent notice (5 s sleep, 10 s sleep, the PDF, the
keys), and the gateway holds a thread for the same time (it waits for i-medicus' answer). Throughput is therefore
`threads / ~30 s`: 4 threads about 500 notices an hour, 16 about 1,900, 200 about 24,000. Lakhs an hour would need ~850 threads
here and as many Apache processes in i-medicus, which is not practical: the lasting fix is in i-medicus (drop the two sleeps and
build the records at the end of the health-information request handler, once its keys exist), after which a notice takes
milliseconds and the gateway's ack wait is no longer needed. What the gateway does meanwhile: `WORKER_CONCURRENCY` (16)
jobs side by side; consent notices of **one patient** one at a time (they overwrite each other's row in i-medicus), of
different patients together; gunicorn `gthread` (`GUNICORN_THREADS`, 16 per process) so a held answer costs a thread, not a
process; at most `HIP_MAX_ACK_WAITS` (48) answers held per web process, beyond that they are answered at once.

**One record per page** (`HIP_PUSH_ENTRIES_PER_PAGE`, 1; 0 = off). i-medicus builds one entry per record (Prescription,
OPConsultation...) and sends them in one push, but its receiver decrypts only `entries[0]` (`WebhookTrait::decryptData`): on the
sandbox a consent for two record types saved one row in `consent_health_data` (the Prescription) and the OP Consultation was
never shown. The gateway now pushes each record as its own ABDM page (`pageNumber` from 0, `pageCount`, same transaction and key
material), one after the other; i-medicus saves a row per page and the viewer shows every bundle. A failure on page *n* retries
only the pages not delivered yet; a push where the client already chose its paging is left as it is. ABDM's status
notification is still one per transaction.

**What was tested** (16 Nov 2026). Automated (`tests/`, 1,080+ tests): all eight record types (Prescription, OPConsultation,
DiagnosticReport, DischargeSummary, ImmunizationRecord, HealthDocumentRecord, WellnessRecord, Invoice) convert, validate, and
survive Fidelius encrypt and decrypt byte for byte (`test_m2_all_record_types.py`); one consent covering all eight is pushed as
eight pages, a HIU reads every page and ABDM gets one TRANSFERRED status for all eight care contexts. Load
(`scripts/load_test_callbacks.py`, real threads, a real HTTP webhook): 900 callbacks (100 patients x 3 consents) were all
delivered exactly once, none lost, never more than one notice of the same patient at a time, never more than 16 webhook calls at
once. Not tested: Postgres under load (the run above is on a SQLite file, where every claim costs ~12 ms of disk sync, so its
throughput says nothing about production), the held acknowledgement under many concurrent web threads, and ABDM itself under load.

**What i-medicus does in M2 and M3, and what it reads from its own database** (read from `WebhookTrait` / `ABDMService`, 17 Nov 2026).
- M2 (HIP, it holds the records): `onRequestHipNotify` finds `hip_link_requests` rows by `care_reference_number` and saves consent
  id, status, request id, purpose, date range, hip id on them, acknowledges, then `ConvertRecordToFHIR` loads all rows with that
  `consent_id`, builds each record (`FHIRApi`: appointment, prescription, diagnostic, invoice... tables), and takes receiver key and
  nonce, sender keys, expiry and push URL from `hip_link_request_keys` and the transaction from `hip_link_requests.transaction_id`.
  `onHealthInformationRequest` finds the row by consent id, saves the transaction id, acknowledges and saves the keys.
  Everything ABDM-specific (transaction, HIU key, push URL, consent, keys, encryption, status) is therefore also held by the
  gateway; i-medicus' rows only matter for **which records to build**.
- M3 (HIU, it asks for records): `hiu/consent/request/notify` fills `consent_artifacts`, `hiu/consent/on-fetch`
  `consent_artifact_details` (and the HIU key pair in `consent_keys`), `hiu/health-information/on-request` stores the transaction
  id on the detail, and the HIP's push to `/api/abdm-webhook` is decrypted (`decryptHealthInfo` -> `Decrypt/decrypt`) and saved in
  `consent_health_data` (one row per page, which is why the gateway pushes one record per page). The gateway needs nothing from
  these tables.

**The lasting fix for throughput, in i-medicus (not applied)** (`WebhookTrait`). Stop sleeping in `onRequestHipNotify`: save the
consent id and acknowledge only; build, encrypt and push in `onHealthInformationRequest`. A notice then takes milliseconds
instead of 17-45 s and a facility owner is no longer limited to one consent at a time. The gateway works without it.

**Consent range** (`hiu_service._range_end`). i-medicus' consent form takes a date and sends `toDate` as midnight
(`2026-11-16T00:00:00.000Z`). ABDM then leaves out the care contexts of that day: on 16 Nov 2026 the patient's app listed only old
visits, and with a real time in `toDate` the new ones appeared. The gateway now treats a midnight `toDate` as "to the end of
that day, and never later than a minute ago" (ABDM refuses a future date); a `toDate` with another time is sent as it is.

**Notify with another consent id.** i-medicus sends the consent id of its own `hip_link_requests` row, and a second consent
notice for the same care context overwrites it (two artefacts), so the status notification named a consent that is not the one of
the transaction: the gateway refused it (400 `CONSENT_MISMATCH`) after the records had been pushed and ABDM was never told.
The gateway now logs a warning and sends the consent ABDM named in the health-information request (the one stored on the
transfer); the transaction belongs to the calling client, so nothing else can be reported this way.

Also: the description of a FAILED transfer notification is now letters, digits, spaces and `. _ -` only (ABDM answered
"Invalid description" to the HIU's JSON error copied in).

Still in i-medicus data, not fixable on the gateway: several `abha_patients` rows for one ABHA address (the callback saves the
token on the first, linking uses the latest), a patient's `dob` saved as today's date (ABDM-1207, "details do not match
Aadhaar"), and an appointment without a payment (the Invoice record has no lines: 422 `INVALID_RECORD`).

## Legacy video-call flow (`ABDMService::sendPrescription`, `sendWellnessRecord`, `acknowledgeConsent`, `fetchConsent`)

The v1 prescription flow of a video call used the old `ABDM/*` and `api/v1/ABDMVC/ConvertPrescription` paths. In
i-medicus **only the path (and the credentials headers, which the gateway needs on every call) changed; no body and no
flow**. The gateway handles what the old bodies lack or get wrong:

| Old path | `config('abdm.endpoints.*')` | Gateway path |
|---|---|---|
| `api/v1/ABDMVC/ConvertPrescription` | `fhir_legacy_prescription` | `POST api/v1/fhir/legacy-prescription` |
| `api/v2/FideliusEncryptionDecryption/GenerateKeys/keys` (a POST that actually encrypted) | `fhir_encrypt` | `POST api/v1/fhir/encrypt` |
| `ABDM/calling-data-push-url` | `hip_hi_push` | `POST api/v1/hip/health-information/push` |
| `ABDM/data-flow-notification-request` | `hip_hi_notify` | `POST api/v1/hip/health-information/notify` |
| `ABDM/consent-init-request-by-hiu` (the on-notify acknowledgement) | `hiu_consent_on_notify` | `POST api/v1/hiu/consent/on-notify` |
| `ABDM/fetch-consent-detail` | `hiu_consent_fetch` | `POST api/v1/hiu/consent/fetch` |

- **The FHIR body.** The old converter took only `prescriptionId`, `date`, `patient{id, name}`, `doctor{id, name}` and
  `medicines[{name, dosage, frequency, duration, instructions}]`. `fhir/legacy-prescription` builds the standard
  Prescription Bundle from it. The body has no ABHA address, licence number or clinic name, so the patient's and the
  doctor's ids stand in for the identifier values (an optional `healthId` / `licenseNumber` in the body wins) and the
  Organization is the client. That Bundle is valid, but its Patient identifier is an i-medicus id, not an ABHA address.
- **The key material of the push.** The legacy bodies send the *receiver's* nonce (and in the wellness variant the
  receiver's own key as `keyValue`), which the HIU cannot decrypt with. The gateway remembers, per transfer, the sender
  key and nonce of the Encrypt call made for that HIU key (`hip_transfers.sender_public_key` / `sender_nonce`, migration
  `0006`) and puts them into the push when the body carries others (a warning is logged). A push for a key that was
  never used with the Encrypt route is sent as given.
- The placeholder `checksum` is replaced by the real one, `dataPushUrl` is accepted as well as `pushUrl`, and the
  notifier is always the facility.

`sendWellnessRecord` still converts a *prescription* (it did before as well). Tests:
`tests/contract/test_legacy_video_call.py`.

## Not on the gateway

Checked by searching `app/`, `config/`, `routes/` and `resources/` of i-medicus for every HTTP call to ABDM or VCDoctor.

| Where | What | Why it is left |
|---|---|---|
| `ABDMService::getSession` (`ABDM/get-session`) | old session call | no caller anywhere (dead code) |
| `ABDMService` `GetResponseUrl` (`ABDMVC/GetResponseUrl/<id>`) | look up ABDM's answer by request id | its only caller is commented out (dead code) |
| `ProviderApiController::abhaSission`, route `GET api/abhaSission` | calls ABDM's v0.5 sessions API directly with `SESSION_CLIENT_ID/SECRET` from `global_constants.php` | no caller in this repository; a mobile app might use the route, so it is not touched |
| `AdminAjaxdataController::getAPI`, route `GET /getAPI` | debug call to ABDM v0.5 sessions and the v2 `generateOtp` | not called by any view or script. It carries the sandbox client id and secret in the code, and the route is not behind a login: **rotate that secret and remove the route** |
| commented-out old calls in `ABDMService`, `WebhookTrait`, `MobileAbdmService` | | comments only |

## Verified on the ABDM sandbox (12 Nov 2026)

Two facilities on one bridge (HIP `IN0810041408` "cradle hospital", HIU `IN0810041412` "Shri brij fracture").
- All 8 record types linked, pushed and notified (`NOTIFIED`) twice: to ABDM's PHR app as HIU, and to i-medicus as HIU
  (one consent per type; all 8 decrypted into `consent_health_data` and shown in the i-medicus viewer).
- **Revoked consent:** the patient revoked a granted consent in the ABHA app; the push was refused with 403
  `CONSENT_INVALID` ("Consent is REVOKED"), the transfer became `FAILED` and ABDM was told (it answers `ABDM-1080`).
- Expired consent, a HIU that rejects the push, a HIU that is down and duplicate callbacks/pushes are covered by the
  automated tests (`tests/contract/test_m2_transfer.py`); not yet run on the sandbox: expired consent, HIU reject.

ABDM sandbox behaviour worth knowing: a future `toDate` is refused (`ABDM-9999`); a date-only `toDate` leaves out care
contexts linked the same day; a PHR "Pull Records" makes one transaction per care context and the transaction expires
within minutes, so push at once.

**Findings in i-medicus (reported, not changed):** the patient-list Consent / Link Token buttons send no
`appointment_id` (needed by `hipLinking`/`generateToken`); the consent modal's type select is empty (Choices is
initialised twice) and its dates are date-only; `onConsentFetch` sends `expiryDate`/`fromDate`/`toDate` as date-only
midnight, so a same-day expiry is "not in the future" and the health-information request fails
(`WebhookTrait.php:3571-3578`); the link-token callback is saved on the oldest `abha_patients` row of an ABHA number,
so a patient registered at two clinics gets the token on the wrong row; the consent-notify handler sleeps 10 s and
pushes only the last care context with `hipKeys[0]`, so it races the health-information request.

**Verification.** `pytest tests` (1009 pass), `scripts/e2e_transfer_local.py` (real processes over HTTP against the mock
ABDM) and i-medicus' own PHP calling keys / encrypt / decrypt / the legacy payload builder.

## Environment (`i-medicus_web/src/.env`)
```
ABDM_BASE_URL=https://abdm-gateway.example.com/   # gateway URL (local dev: http://localhost:5000/)
ABDM_CLIENT_ID=<from: flask clients create --name "i-medicus" --hip-id IN0110005723_240>
ABDM_CLIENT_SECRET=<shown once by the same command>
ABDM_CLINIC_ID=IN0110005723_240               # unchanged (the facility id; HIU calls read it directly)
```
- HIP calls take the facility id from `clinic_admin.hfr_id` first (`CustomHelper::clinicAdminDetails`), the HIU calls
  (`consentInit`, `onConsentFetch`, ...) read `ABDM_CLINIC_ID`. `clinic_admin.hfr_id` is varchar(15).
- `DATA_PUSH_URL` is the public https URL of i-medicus' `/api/abdm-webhook` (the HIU's data-push address).
- `ABDM_CLIENT_ID` / `ABDM_CLIENT_SECRET` are now **issued by the gateway**. They are not NHA credentials.
- The NHA credentials live only in the gateway's `.env` (`NHA_CLIENT_ID` / `NHA_CLIENT_SECRET`).
- After changing `.env`, run `php artisan config:clear`.

## Behaviour notes
- **Session:** i-medicus still creates a session before every call (`getBearerToken` forgets the
  cached token on purpose, at `ABDMService.php:2881`). The gateway's session endpoint is cheap
  (no DB write), so this works. Removing the two `session()->forget` lines would save one round-trip
  per call; that is optional.
- **Error messages:**
  - `AbdmErrorTranslator` keeps working: gateway errors carry `message.error.message`.
  - ABDM's rate-limit text ("request for new OTP after N seconds", HTTP 429) is passed through.
  - UIDAI error text (`UIDAI Error code : 111`) is passed through.
  - Invalid Aadhaar is rejected by the gateway with "Invalid Aadhaar number…", which the translator recognises.
- **Wrong OTP on login verify:** HTTP 200, `data.authResult = "failed"`, `success: false`. i-medicus
  already handles this with `strtolower(...) == 'failed'`.
- **`loginSearch` (#11):** ABDM v3 has no direct lookup by ABHA number, so the gateway validates the
  format and returns `authMethods: [MOBILE_OTP, AADHAAR_OTP]`. If the number does not exist, ABDM
  rejects the OTP request (#12/#13) instead.

## Verified
- Gateway contract tests (`pytest tests/contract`) cover every row above, checking both the request
  sent to ABDM and the keys i-medicus reads.
- PHP smoke test, run with i-medicus's own Guzzle and `AbdmErrorTranslator` against the gateway and
  `scripts/mock_abdm.py`: 33/33 checks passed, across all M1 flows, card/QR bytes and the error translations.
- M1 has run against the ABDM sandbox; the M2 and M3 sandbox results are in "Verified on the ABDM sandbox".
