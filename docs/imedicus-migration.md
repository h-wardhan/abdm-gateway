# i-medicus: moving from VCDoctor to abdm-gateway (M1)

i-medicus used to call the VCDoctor gateway (`abdmprod.vcdoctor.com`, `api/v2/ABDMVC/*`). Its M1
calls now go to abdm-gateway.

The gateway keeps the request bodies and response shapes i-medicus already uses, so only the
**paths** changed. They now live in one place: `config/abdm.php` → `endpoints`.

## What changed in i-medicus (branch `feature/abdm-gateway-m1`)

1. `src/config/abdm.php`: added the `endpoints` map (gateway paths).
2. `src/app/Services/ABDMService.php` and `src/app/Http/Controllers/Api/ABHAController.php`:
   every M1 `'api/v2/ABDMVC/...'` string is replaced by `config('abdm.endpoints.<key>')`.
   That is 59 + 4 call sites. Request bodies and response handling are unchanged.
3. `ABDMService::updateMobileNumberVerify`: the `authResult` check is now case-insensitive.
   ABDM returns `failed`, and the old code only matched `Failed`.

Not changed:
- M2/M3 paths (care-context linking, consent, Fidelius, HIP QR, `WebhookTrait`). These come in a later phase.
- The legacy `ABDM/*` paths in `MobileAbdmService`.

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

## Environment (`i-medicus_web/src/.env`)
```
ABDM_BASE_URL=https://abdm-gateway.example.com/   # gateway URL (local dev: http://localhost:5000/)
ABDM_CLIENT_ID=<from: flask clients create --name "i-medicus" --hip-id IN0110005723_240>
ABDM_CLIENT_SECRET=<shown once by the same command>
ABDM_CLINIC_ID=IN0110005723_240               # unchanged (HIP id, used from M2 onwards)
```
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
- **Still to do:** an end-to-end run against the real ABDM sandbox once NHA sandbox credentials are available.
