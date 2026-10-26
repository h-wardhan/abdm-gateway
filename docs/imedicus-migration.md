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
