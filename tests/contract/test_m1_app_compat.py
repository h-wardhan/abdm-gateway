"""
The calls of i-medicus' mobile-app service (MobileAbdmService.php) on /api/v1/abha/app/*.

It authenticates with Client-Id + Client-Secret headers (no gateway token) and reads ABDM's answer at the top level,
so every test sends the headers the way it does and checks the keys it reads:
- aadhaar/verify  -> ABHAProfile{ABHANumber, firstName, ...}, tokens{token, expiresIn}, isNew
- mobile/otp      -> HTTP 201 and txnId;  mobile/verify -> HTTP 201, accounts[0], token, expiresIn
- abha-number/*   -> txnId;  accounts[0].ABHANumber, token, refreshToken, expiresIn, refreshExpiresIn
- a wrong OTP is HTTP 200 with authResult "failed"
"""
import json

import pytest
import responses

from tests.conftest import ABHA, decrypt

TXN = "a1b2c3d4-0000-1111-2222-333344445555"
AADHAAR = "999941057058"
BASE = "/api/v1/abha/app"
ENROL = f"{ABHA}/abha/api/v3/enrollment"
PROFILE = f"{ABHA}/abha/api/v3/profile"

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
ABHA_PROFILE = {"ABHANumber": "91-1234-5678-9012", "firstName": "Ravi", "middleName": "", "lastName": "Kumar",
                "gender": "M", "dob": "15-01-1990", "mobile": "9876543210", "mobileVerified": True,
                "email": None, "photo": "", "preferredAddress": "ravi@sbx", "phrAddress": ["ravi@sbx"]}
LOGIN_OK = {"txnId": TXN, "authResult": "success", "message": "OTP verified successfully", "token": "x-tok",
            "expiresIn": 1800, "refreshToken": "r-tok", "refreshExpiresIn": 1296000,
            "accounts": [{"ABHANumber": "91-1234-5678-9012", "name": "Ravi Kumar", "preferredAbhaAddress": "ravi@sbx"}]}
LOGIN_FAILED = {"txnId": TXN, "authResult": "failed", "message": "Please enter a valid OTP"}


@pytest.fixture
def creds(api_client):
    """Headers MobileAbdmService::getHeaders() sends."""
    tenant, secret = api_client
    return {"Client-Id": tenant.client_id, "Client-Secret": secret, "Content-Type": "application/json"}


def _body(mock, part):
    calls = [c for c in mock.calls if part in c.request.url]
    assert calls, f"no call to {part}"
    return json.loads(calls[-1].request.body)


def _urls(mock):
    return [c.request.url for c in mock.calls]


def _start_txn(client, creds, abdm):
    """Aadhaar OTP, so TXN belongs to this client (later steps check ownership)."""
    abdm.add(responses.POST, f"{ENROL}/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    client.post(f"{BASE}/aadhaar/otp", json={"aadharNumber": AADHAAR}, headers=creds)


# ---- ABHA creation ------------------------------------------------------------------------------------------------

def test_aadhaar_otp(client, creds, abdm):
    abdm.add(responses.POST, f"{ENROL}/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    res = client.post(f"{BASE}/aadhaar/otp", json={"aadharNumber": AADHAAR}, headers=creds)
    assert res.status_code == 200 and res.get_json()["txnId"] == TXN
    assert decrypt(_body(abdm, "enrollment/request/otp")["loginId"]) == AADHAAR


def test_aadhaar_otp_rejects_invalid_aadhaar_without_calling_abdm(client, creds, abdm):
    res = client.post(f"{BASE}/aadhaar/otp", json={"aadharNumber": "999941057059"}, headers=creds)
    assert res.status_code == 400 and not [u for u in _urls(abdm) if "enrollment" in u]


def test_aadhaar_otp_rate_limit_keeps_abdm_status(client, creds, abdm):
    abdm.add(responses.POST, f"{ENROL}/request/otp", status=429,
             json={"message": "You can request for new OTP after 30 seconds"})
    res = client.post(f"{BASE}/aadhaar/otp", json={"aadharNumber": AADHAAR}, headers=creds)
    assert res.status_code == 429 and "30 seconds" in res.get_json()["message"]["error"]["message"]


def test_aadhaar_verify_returns_abdm_profile_and_tokens_at_top_level(client, creds, abdm):
    _start_txn(client, creds, abdm)
    abdm.add(responses.POST, f"{ENROL}/enrol/byAadhaar", json={
        "txnId": TXN, "message": "Account created", "isNew": True,
        "tokens": {"token": "x-tok", "expiresIn": 1800, "refreshToken": "r-tok", "refreshExpiresIn": 1296000},
        "ABHAProfile": ABHA_PROFILE})

    # MobileAbdmService::verifyAadhaarOtpMobile body: txnId, otpValue, mobile
    res = client.post(f"{BASE}/aadhaar/verify", json={"txnId": TXN, "otpValue": "123456", "mobile": "9876543210"},
                      headers=creds)

    data = res.get_json()
    assert res.status_code == 200 and "code" not in data
    assert data["ABHAProfile"]["ABHANumber"] == "91-1234-5678-9012" and data["ABHAProfile"]["firstName"] == "Ravi"
    assert data["tokens"]["token"] == "x-tok" and data["tokens"]["expiresIn"] == 1800 and data["isNew"] is True
    sent = _body(abdm, "enrol/byAadhaar")["authData"]["otp"]
    assert sent["txnId"] == TXN and sent["mobile"] == "9876543210" and decrypt(sent["otpValue"]) == "123456"


@pytest.mark.parametrize("body", [
    {"txnId": TXN, "otpValue": "12", "mobile": "9876543210"},         # short OTP
    {"txnId": TXN, "otpValue": "123456", "mobile": "12345"},          # bad mobile
    {"otpValue": "123456", "mobile": "9876543210"},                   # no txnId
])
def test_aadhaar_verify_rejects_bad_input_without_calling_abdm(client, creds, abdm, body):
    res = client.post(f"{BASE}/aadhaar/verify", json=body, headers=creds)
    assert res.status_code == 400 and not [u for u in _urls(abdm) if "enrol/byAadhaar" in u]


def test_aadhaar_verify_with_wrong_otp_is_an_abdm_error(client, creds, abdm):
    _start_txn(client, creds, abdm)
    abdm.add(responses.POST, f"{ENROL}/enrol/byAadhaar", status=422,
             json={"error": {"code": "ABDM-1039", "message": "Invalid OTP"}})
    res = client.post(f"{BASE}/aadhaar/verify", json={"txnId": TXN, "otpValue": "000000", "mobile": "9876543210"},
                      headers=creds)
    assert res.status_code == 422


def test_aadhaar_verify_refuses_another_clients_txn(client, creds, other_auth, abdm):
    abdm.add(responses.POST, f"{ENROL}/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": AADHAAR}, headers=other_auth)

    res = client.post(f"{BASE}/aadhaar/verify", json={"txnId": TXN, "otpValue": "123456", "mobile": "9876543210"},
                      headers=creds)
    assert res.status_code == 403 and not [u for u in _urls(abdm) if "enrol/byAadhaar" in u]


# ---- ABHA address -------------------------------------------------------------------------------------------------

def test_address_suggestions_and_create(client, creds, abdm):
    _start_txn(client, creds, abdm)
    abdm.add(responses.GET, f"{ENROL}/enrol/suggestion", json={"txnId": TXN, "abhaAddressList": ["ravi.k@sbx", "ravi90@sbx"]})
    abdm.add(responses.POST, f"{ENROL}/enrol/abha-address", json={"txnId": TXN, "healthIdNumber": "91-1234-5678-9012",
                                                                  "preferredAbhaAddress": "ravi.k@sbx"})

    # getEnrollmentList sends txnId only; updateAbhaAddress sends txnId, abhaAddress, preferred
    res = client.post(f"{BASE}/address-suggestions", json={"txnId": TXN}, headers=creds)
    assert res.status_code == 200 and res.get_json()["abhaAddressList"] == ["ravi.k@sbx", "ravi90@sbx"]

    res = client.post(f"{BASE}/address", json={"txnId": TXN, "abhaAddress": "ravi.k@sbx", "preferred": 1}, headers=creds)
    assert res.status_code == 200 and res.get_json()["preferredAbhaAddress"] == "ravi.k@sbx"
    assert _body(abdm, "enrol/abha-address") == {"txnId": TXN, "abhaAddress": "ravi.k@sbx", "preferred": 1}


def test_address_create_rejects_invalid_address(client, creds, abdm):
    _start_txn(client, creds, abdm)
    res = client.post(f"{BASE}/address", json={"txnId": TXN, "abhaAddress": "a b", "preferred": 1}, headers=creds)
    assert res.status_code == 400 and not [u for u in _urls(abdm) if "abha-address" in u]


def test_address_suggestions_need_a_txn_id(client, creds, abdm):
    assert client.post(f"{BASE}/address-suggestions", json={}, headers=creds).status_code == 400


# ---- login by mobile (one step in the old API) --------------------------------------------------------------------

def test_mobile_login_otp_searches_and_sends_in_one_call(client, creds, abdm):
    abdm.add(responses.POST, f"{PROFILE}/account/abha/search",
             json=[{"txnId": TXN, "ABHA": [{"index": 1, "ABHANumber": "xx-xxxx-xxxx-9012", "name": "Ravi"}]}])
    abdm.add(responses.POST, f"{PROFILE}/login/request/otp", json={"txnId": TXN, "message": "OTP sent"})

    res = client.post(f"{BASE}/mobile/otp", json={"mobileNumber": "9876543210"}, headers=creds)

    assert res.status_code == 201                        # MobileAbdmService::sendOtpByMobile checks 201 + txnId
    assert res.get_json()["txnId"] == TXN
    assert decrypt(_body(abdm, "account/abha/search")["mobile"]) == "9876543210"
    sent = _body(abdm, "login/request/otp")
    assert sent["loginHint"] == "index" and decrypt(sent["loginId"]) == "1" and sent["txnId"] == TXN


def test_mobile_login_otp_when_no_abha_on_that_mobile(client, creds, abdm):
    abdm.add(responses.POST, f"{PROFILE}/account/abha/search", json=[])
    res = client.post(f"{BASE}/mobile/otp", json={"mobileNumber": "9876543210"}, headers=creds)
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "ABHA_NOT_FOUND"
    assert not [u for u in _urls(abdm) if "login/request/otp" in u]


def test_mobile_login_otp_rejects_invalid_mobile(client, creds, abdm):
    res = client.post(f"{BASE}/mobile/otp", json={"mobileNumber": "12345"}, headers=creds)
    assert res.status_code == 400 and not [u for u in _urls(abdm) if "abha/search" in u]


def test_mobile_login_verify(client, creds, abdm):
    abdm.add(responses.POST, f"{PROFILE}/account/abha/search", json=[{"txnId": TXN, "ABHA": [{"index": 1}]}])
    abdm.add(responses.POST, f"{PROFILE}/login/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    abdm.add(responses.POST, f"{PROFILE}/login/verify", json=LOGIN_OK)
    client.post(f"{BASE}/mobile/otp", json={"mobileNumber": "9876543210"}, headers=creds)

    # verifyOTPMobile sends the controller's body as-is
    res = client.post(f"{BASE}/mobile/verify",
                      json={"txnId": TXN, "otp": "123456", "mobileNumber": "9876543210", "_token": "csrf"}, headers=creds)

    data = res.get_json()
    assert res.status_code == 201                        # verifyOTPMobile saves the ABHA only on 201
    assert data["accounts"][0]["ABHANumber"] == "91-1234-5678-9012"
    assert data["token"] == "x-tok" and data["expiresIn"] == 1800
    sent = _body(abdm, "login/verify")
    assert sent["scope"] == ["abha-login", "mobile-verify"] and decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"


def test_mobile_login_verify_wrong_otp_is_200_failed(client, creds, abdm):
    abdm.add(responses.POST, f"{PROFILE}/account/abha/search", json=[{"txnId": TXN, "ABHA": [{"index": 1}]}])
    abdm.add(responses.POST, f"{PROFILE}/login/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    abdm.add(responses.POST, f"{PROFILE}/login/verify", json=LOGIN_FAILED)
    client.post(f"{BASE}/mobile/otp", json={"mobileNumber": "9876543210"}, headers=creds)

    res = client.post(f"{BASE}/mobile/verify", json={"txnId": TXN, "otp": "000000"}, headers=creds)

    assert res.status_code == 200 and res.get_json()["authResult"] == "failed"   # not 201: nothing gets saved


# ---- login by ABHA number -----------------------------------------------------------------------------------------

def test_abha_number_otp_and_verify(client, creds, abdm):
    abdm.add(responses.POST, f"{PROFILE}/login/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    abdm.add(responses.POST, f"{PROFILE}/login/verify", json=LOGIN_OK)

    res = client.post(f"{BASE}/abha-number/otp", json={"abhaNumber": "91-1234-5678-9012"}, headers=creds)
    assert res.status_code == 200 and res.get_json()["txnId"] == TXN
    sent = _body(abdm, "login/request/otp")
    assert sent["scope"] == ["abha-login", "mobile-verify"] and sent["loginHint"] == "abha-number"
    assert decrypt(sent["loginId"]) == "91-1234-5678-9012"

    res = client.post(f"{BASE}/abha-number/verify", json={"txnId": TXN, "OTP": "123456"}, headers=creds)
    data = res.get_json()
    assert res.status_code == 200 and data["accounts"][0]["ABHANumber"] == "91-1234-5678-9012"
    for key in ("token", "refreshToken", "expiresIn", "refreshExpiresIn", "txnId"):
        assert data[key]
    assert decrypt(_body(abdm, "login/verify")["authData"]["otp"]["otpValue"]) == "123456"


def test_abha_number_verify_wrong_otp_is_failed(client, creds, abdm):
    abdm.add(responses.POST, f"{PROFILE}/login/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    abdm.add(responses.POST, f"{PROFILE}/login/verify", json=LOGIN_FAILED)
    client.post(f"{BASE}/abha-number/otp", json={"abhaNumber": "91-1234-5678-9012"}, headers=creds)
    res = client.post(f"{BASE}/abha-number/verify", json={"txnId": TXN, "OTP": "000000"}, headers=creds)
    assert res.status_code == 200 and res.get_json()["authResult"] == "failed" and "accounts" not in res.get_json()


def test_abha_number_otp_rejects_invalid_number(client, creds, abdm):
    res = client.post(f"{BASE}/abha-number/otp", json={"abhaNumber": "123"}, headers=creds)
    assert res.status_code == 400 and not [u for u in _urls(abdm) if "login/request/otp" in u]


# ---- email link, card, QR (ABHA user token in x-token) ------------------------------------------------------------

def test_email_verify_link(client, creds, abdm):
    abdm.add(responses.POST, f"{PROFILE}/account/request/emailVerificationLink",
             json={"txnId": TXN, "message": "Link sent"})
    res = client.post(f"{BASE}/email/verify-link", json={"email": "a@b.co"}, headers={**creds, "x-token": "Bearer tok"})
    assert res.status_code == 200 and res.get_json()["txnId"] == TXN
    req = [c for c in abdm.calls if "emailVerificationLink" in c.request.url][-1].request
    assert req.headers["X-token"] == "Bearer tok" and decrypt(json.loads(req.body)["loginId"]) == "a@b.co"


def test_email_verify_link_needs_token_and_valid_email(client, creds, abdm):
    assert client.post(f"{BASE}/email/verify-link", json={"email": "a@b.co"}, headers=creds).status_code == 400
    res = client.post(f"{BASE}/email/verify-link", json={"email": "nope"}, headers={**creds, "x-token": "tok"})
    assert res.status_code == 400


@pytest.mark.parametrize("x_token", ["Bearer tok", "tok"])
def test_card_and_qr_return_bytes_with_the_token_in_x_token(client, creds, abdm, x_token):
    abdm.add(responses.GET, f"{PROFILE}/account/abha-card", body=PNG, content_type="image/png")
    abdm.add(responses.GET, f"{PROFILE}/account/qrCode", body=PNG, content_type="application/octet-stream")
    headers = {**creds, "x-token": x_token, "Accept": "application/octet-stream"}   # showQrCode / downloadAbhaCard

    for path in ("profile/card", "profile/qr"):
        res = client.get(f"{BASE}/{path}", headers=headers)
        assert res.status_code == 200 and res.data == PNG and res.mimetype == "image/png"
    assert [c.request.headers["X-token"] for c in abdm.calls if "account/" in c.request.url] == ["Bearer tok"] * 2


def test_card_with_an_expired_token_is_a_401_json(client, creds, abdm):
    abdm.add(responses.GET, f"{PROFILE}/account/abha-card", status=401, json={"code": "ABDM-1006", "message": "expired"})
    res = client.get(f"{BASE}/profile/card", headers={**creds, "x-token": "old"})
    assert res.status_code == 401 and res.get_json()["message"]["error"]["code"] == "ABDM-1006"


def test_card_and_qr_need_the_abha_token(client, creds, abdm):
    assert client.get(f"{BASE}/profile/card", headers=creds).status_code == 400
    assert client.get(f"{BASE}/profile/qr", headers=creds).status_code == 400


# ---- auth ---------------------------------------------------------------------------------------------------------

ALL = [("post", "aadhaar/otp"), ("post", "aadhaar/verify"), ("post", "mobile/otp"), ("post", "mobile/verify"),
       ("post", "email/verify-link"), ("post", "address-suggestions"), ("post", "address"),
       ("post", "abha-number/otp"), ("post", "abha-number/verify"), ("get", "profile/card"), ("get", "profile/qr")]


@pytest.mark.parametrize("method,path", ALL)
def test_every_route_needs_client_credentials(client, abdm, api_client, method, path):
    tenant, _ = api_client
    call = getattr(client, method)
    assert call(f"{BASE}/{path}", json={}).status_code == 401                       # nothing
    wrong = {"Client-Id": tenant.client_id, "Client-Secret": "wrong"}
    assert call(f"{BASE}/{path}", json={}, headers=wrong).status_code == 401        # wrong secret
    assert not [u for u in _urls(abdm) if "/abha/api/v3" in u]


def test_a_gateway_token_works_too(client, auth, abdm):
    abdm.add(responses.POST, f"{ENROL}/request/otp", json={"txnId": TXN, "message": "OTP sent"})
    res = client.post(f"{BASE}/aadhaar/otp", json={"aadharNumber": AADHAAR}, headers=auth)
    assert res.status_code == 200 and res.get_json()["txnId"] == TXN
