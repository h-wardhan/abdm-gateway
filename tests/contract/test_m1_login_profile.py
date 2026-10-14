"""Contract tests: ABHA search, login, profile, card, QR."""
import json

import responses

from tests.conftest import ABHA, decrypt

TXN = "b1b2c3d4-0000-1111-2222-333344445555"
VERIFY_OK = {
    "txnId": TXN, "authResult": "success", "message": "OTP verified successfully",
    "token": "x-tok", "expiresIn": 1800, "refreshToken": "r-tok", "refreshExpiresIn": 1296000,
    "accounts": [{"ABHANumber": "91-1234-5678-9012", "preferredAbhaAddress": "ramesh.k@sbx", "name": "Ramesh Kumar"}],
}


def _sent(mock, path_part):
    return json.loads([c for c in mock.calls if path_part in c.request.url][-1].request.body)


# ---- mobile --------------------------------------------------------------------------

def test_search_by_mobile_returns_list_imedicus_reads(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/account/abha/search", json=[
        {"txnId": TXN, "ABHA": [{"index": 1, "ABHANumber": "xx-xxxx-xxxx-9012", "name": "Ramesh", "gender": "M"}]}
    ])
    res = client.post("/api/v1/abha/search/mobile", json={"mobileNumber": "9876543210"}, headers=auth)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data[0]["txnId"] == TXN and data[0]["ABHA"][0]["index"] == 1   # login.js / verifyByMobile
    sent = _sent(abdm, "abha/search")
    assert sent["scope"] == ["search-abha"] and decrypt(sent["mobile"]) == "9876543210"


def test_mobile_login_otp_and_verify(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/request/otp",
             json={"txnId": TXN, "message": "OTP sent to ******3210"})
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/verify", json=VERIFY_OK)

    res = client.post("/api/v1/abha/login/mobile/otp", json={"txnId": TXN, "index": 1}, headers=auth)
    body = res.get_json()
    assert res.status_code == 200 and body["success"] is True and body["message"]   # verifyByMobile checks success+message
    assert body["data"]["txnId"] == TXN
    sent = _sent(abdm, "login/request/otp")
    assert sent["scope"] == ["abha-login", "search-abha", "mobile-verify"]
    assert sent["loginHint"] == "index" and decrypt(sent["loginId"]) == "1" and sent["txnId"] == TXN

    res = client.post("/api/v1/abha/login/mobile/verify", json={"otp": "123456", "txnId": TXN, "_token": "csrf"}, headers=auth)
    data = res.get_json()["data"]
    for key in ("token", "expiresIn", "refreshToken", "refreshExpiresIn"):   # verifyOTPMobile
        assert data[key]
    sent = _sent(abdm, "login/verify")
    assert sent["scope"] == ["abha-login", "mobile-verify"]
    assert decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"


def test_login_verify_wrong_otp(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/verify",
             json={"txnId": TXN, "authResult": "failed", "message": "Please enter a valid OTP"})
    res = client.post("/api/v1/abha/login/mobile/verify", json={"otp": "000000", "txnId": TXN}, headers=auth)
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is False and body["data"]["authResult"] == "failed"
    assert body["data"]["message"] == "Please enter a valid OTP"


def test_select_account_sends_t_token(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/verify/user", json={"token": "x-tok", "expiresIn": 1800})
    res = client.post("/api/v1/abha/login/mobile/select-account",
                      json={"txnId": TXN, "abhaNumber": "91123456789012"}, headers={**auth, "T-Token": "t-tok"})
    assert res.status_code == 200
    call = [c for c in abdm.calls if "verify/user" in c.request.url][-1]
    assert call.request.headers["T-token"] == "Bearer t-tok"
    assert json.loads(call.request.body) == {"ABHANumber": "91-1234-5678-9012", "txnId": TXN}


# ---- ABHA number ---------------------------------------------------------------------

def test_search_abha_number_validates_locally(client, auth, abdm):
    res = client.post("/api/v1/abha/search/abha-number", json={"abhaNumber": "91-1234-5678-9012"}, headers=auth)
    assert res.status_code == 200 and res.get_json()["success"] is True
    assert res.get_json()["data"]["authMethods"] == ["MOBILE_OTP", "AADHAAR_OTP"]
    res = client.post("/api/v1/abha/search/abha-number", json={"abhaNumber": "123"}, headers=auth)
    assert res.status_code == 400


def test_abha_number_otp_mobile_and_aadhaar(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/request/otp", json={"txnId": TXN, "message": "sent"})

    client.post("/api/v1/abha/login/abha-number/mobile/otp", json={"abhaNumber": "91-1234-5678-9012"}, headers=auth)
    sent = _sent(abdm, "login/request/otp")
    assert sent["scope"] == ["abha-login", "mobile-verify"] and sent["otpSystem"] == "abdm"
    assert sent["loginHint"] == "abha-number" and decrypt(sent["loginId"]) == "91-1234-5678-9012"

    client.post("/api/v1/abha/login/abha-number/aadhaar/otp", json={"abhaNumber": "91123456789012"}, headers=auth)
    sent = _sent(abdm, "login/request/otp")
    assert sent["scope"] == ["abha-login", "aadhaar-verify"] and sent["otpSystem"] == "aadhaar"


def test_abha_number_verify_uses_method_scope(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/verify", json=VERIFY_OK)
    res = client.post("/api/v1/abha/login/abha-number/aadhaar/verify", json={"txnId": TXN, "otp": "123456"}, headers=auth)
    assert res.status_code == 200 and res.get_json()["data"]["token"] == "x-tok"
    assert _sent(abdm, "login/verify")["scope"] == ["abha-login", "aadhaar-verify"]


def test_abha_number_unknown_method(client, auth, abdm):
    res = client.post("/api/v1/abha/login/abha-number/email/otp", json={"abhaNumber": "91123456789012"}, headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "INVALID_AUTH_METHOD"


# ---- Aadhaar -------------------------------------------------------------------------

def test_aadhaar_login(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/request/otp", json={"txnId": TXN, "message": "sent"})
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/login/verify", json=VERIFY_OK)

    res = client.post("/api/v1/abha/login/aadhaar/otp", json={"aadhaarNumber": "999941057058"}, headers=auth)
    assert res.get_json()["data"]["txnId"] == TXN
    sent = _sent(abdm, "login/request/otp")
    assert sent["loginHint"] == "aadhaar" and sent["scope"] == ["abha-login", "aadhaar-verify"]
    assert decrypt(sent["loginId"]) == "999941057058"

    res = client.post("/api/v1/abha/login/aadhaar/verify", json={"otp": "123456", "txnId": TXN}, headers=auth)
    assert res.get_json()["data"]["refreshToken"] == "r-tok"


# ---- ABHA address --------------------------------------------------------------------

def test_abha_address_search_otp_verify(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/phr/web/login/abha/search",
             json={"healthIdNumber": "91-1234-5678-9012", "abhaAddress": "ramesh.k@sbx", "authMethods": ["MOBILE_OTP", "AADHAAR_OTP"]})
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/phr/web/login/abha/request/otp", json={"txnId": TXN, "message": "sent"})
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/phr/web/login/abha/verify", json={
        "message": "OTP verified", "authResult": "success",
        "users": [{"abhaAddress": "ramesh.k@sbx", "fullName": "Ramesh Kumar", "kycStatus": "VERIFIED"}],
        "tokens": {"token": "x-tok", "expiresIn": 1800, "refreshToken": "r-tok", "refreshExpiresIn": 1296000},
    })

    res = client.post("/api/v1/abha/search/abha-address", json={"abhaAddress": "ramesh.k@sbx"}, headers=auth)
    assert res.get_json()["data"]["authMethods"] == ["MOBILE_OTP", "AADHAAR_OTP"]   # login.js builds the dropdown

    # verifyByAddress sends scope as a plain string
    res = client.post("/api/v1/abha/login/abha-address/otp",
                      json={"abhaAddress": "ramesh.k@sbx", "scope": "aadhaar-verify", "otpSystem": "aadhaar"}, headers=auth)
    body = res.get_json()
    assert body["success"] is True and body["data"]["txnId"] == TXN
    sent = _sent(abdm, "abha/request/otp")
    assert sent["scope"] == ["abha-address-login", "aadhaar-verify"] and sent["otpSystem"] == "aadhaar"
    assert sent["loginHint"] == "abha-address" and decrypt(sent["loginId"]) == "ramesh.k@sbx"

    # JS sends otp, otpValue, scope, scop, txnId
    res = client.post("/api/v1/abha/login/abha-address/verify",
                      json={"otp": "123456", "otpValue": "123456", "scope": "aadhaar-verify", "scop": "aadhaar-verify", "txnId": TXN},
                      headers=auth)
    data = res.get_json()["data"]
    assert data["tokens"]["token"] == "x-tok" and data["users"][0]["abhaAddress"] == "ramesh.k@sbx"
    assert _sent(abdm, "abha/verify")["scope"] == ["abha-address-login", "aadhaar-verify"]


# ---- profile / card / QR -------------------------------------------------------------

def test_profile_passes_abdm_keys_through(client, auth, abdm):
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/profile/account", json={
        "ABHANumber": "91-1234-5678-9012", "yearOfBirth": "1990", "monthOfBirth": "1", "dayOfBirth": "1",
        "pincode": "110001", "preferredAbhaAddress": "ramesh.k@sbx", "mobile": "9876543210", "firstName": "Ramesh",
    })
    res = client.get("/api/v1/abha/profile?xToken=x-tok", headers=auth)
    data = res.get_json()["data"]
    assert data["ABHANumber"] == "91-1234-5678-9012" and data["yearOfBirth"] == "1990"   # verifyOTPMobile
    call = [c for c in abdm.calls if c.request.url.endswith("/profile/account")][-1]
    assert call.request.headers["X-token"] == "Bearer x-tok"


def test_phr_profile_unwraps_abha_profile(client, auth, abdm):
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/phr/web/login/profile/abha-profile",
             json={"ABHAProfile": {"kycStatus": "PENDING", "abhaAddress": "ramesh.k@sbx"}})
    res = client.get("/api/v1/abha/phr/profile", headers={**auth, "X-Token": "x-tok"})
    assert res.get_json()["data"]["kycStatus"] == "PENDING"


def test_card_and_qr_return_bytes(client, auth, abdm):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/profile/account/abha-card", body=png, content_type="image/png")
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/profile/account/qrCode", body=png, content_type="application/octet-stream")
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/phr/web/login/profile/abha/phr-card", body=b"%PDF-1.4 x",
             content_type="application/pdf")

    res = client.get("/api/v1/abha/profile/card?xToken=x-tok", headers=auth)
    assert res.status_code == 200 and res.mimetype == "image/png" and res.data == png
    res = client.get("/api/v1/abha/profile/qr?xToken=x-tok", headers=auth)
    assert res.mimetype == "image/png"   # sniffed from bytes
    res = client.get("/api/v1/abha/phr/card?xToken=x-tok", headers=auth)
    assert res.mimetype == "application/pdf"


def test_card_from_base64_json(client, auth, abdm):
    import base64
    png = b"\x89PNG\r\n\x1a\nabc"
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/profile/account/abha-card",
             json={"abhaCard": base64.b64encode(png).decode()})
    res = client.get("/api/v1/abha/profile/card?xToken=x-tok", headers=auth)
    assert res.data == png and res.mimetype == "image/png"


def test_expired_user_token_is_401_json(client, auth, abdm):
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/profile/account/abha-card", status=401,
             json={"code": "ABDM-1006", "message": "Token expired"})
    res = client.get("/api/v1/abha/profile/card?xToken=old", headers=auth)
    assert res.status_code == 401
    assert res.get_json()["message"]["error"]["code"] == "ABDM-1006"


def test_missing_x_token(client, auth, abdm):
    res = client.get("/api/v1/abha/profile", headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "MISSING_X_TOKEN"
