"""
The mobile / email calls exactly as i-medicus sends them (ABDMService.php):
- the body is whatever its controller received (`mobile`, `txnId`, `otp`, `email`, `patient_id`, ...)
- for an existing ABHA, `Authorization` carries the ABHA user token and Client-Id / Client-Secret identify the client
- a wrong OTP must be an HTTP error, because i-medicus saves the mobile / email when the call returns 200
"""
import json

import pytest
import responses

from tests.conftest import ABHA, decrypt

TXN = "a1b2c3d4-0000-1111-2222-333344445555"
PROFILE_OTP = f"{ABHA}/abha/api/v3/profile/account/request/otp"
PROFILE_VERIFY = f"{ABHA}/abha/api/v3/profile/account/verify"
ENROL_OTP = f"{ABHA}/abha/api/v3/enrollment/request/otp"
ENROL_VERIFY = f"{ABHA}/abha/api/v3/enrollment/auth/byAbdm"


@pytest.fixture
def legacy(api_client):
    """Headers of an existing-ABHA call: the ABHA token in Authorization, the client in Client-Id/Secret."""
    tenant, secret = api_client
    return {"Authorization": "abha-user-tok", "Client-Id": tenant.client_id, "Client-Secret": secret}


def _last(mock, url):
    calls = [c for c in mock.calls if c.request.url == url]
    assert calls, f"no call to {url}"
    return calls[-1].request


# ---- existing ABHA: ABHA token in Authorization -------------------------------------------------------------------

def test_mobile_otp_for_an_existing_abha(client, legacy, abdm):
    abdm.add(responses.POST, PROFILE_OTP, json={"txnId": TXN, "message": "OTP sent"})
    # UpdateMobile controller body: mobile + patient_id (no txnId)
    res = client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210", "patient_id": 5}, headers=legacy)

    assert res.status_code == 200 and res.get_json()["data"]["txnId"] == TXN
    req = _last(abdm, PROFILE_OTP)
    sent = json.loads(req.body)
    assert req.headers["X-token"] == "Bearer abha-user-tok" and req.headers["Authorization"] == "Bearer gw-token"
    assert sent["scope"] == ["abha-profile", "mobile-verify"] and decrypt(sent["loginId"]) == "9876543210"


def test_mobile_verify_for_an_existing_abha(client, legacy, abdm):
    abdm.add(responses.POST, PROFILE_OTP, json={"txnId": TXN, "message": "OTP sent"})
    abdm.add(responses.POST, PROFILE_VERIFY, json={"txnId": TXN, "authResult": "success", "message": "Done"})
    client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210"}, headers=legacy)

    res = client.post("/api/v1/abha/mobile/verify",
                      json={"otp": "123456", "txnId": TXN, "mobile": "9876543210", "patient_id": 5}, headers=legacy)

    assert res.status_code == 200 and res.get_json()["data"]["authResult"] == "success"
    req = _last(abdm, PROFILE_VERIFY)
    sent = json.loads(req.body)
    assert req.headers["X-token"] == "Bearer abha-user-tok"
    assert sent["scope"] == ["abha-profile", "mobile-verify"] and sent["authData"]["otp"]["txnId"] == TXN
    assert decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"


def test_email_verify_for_an_existing_abha(client, legacy, abdm):
    abdm.add(responses.POST, PROFILE_VERIFY, json={"txnId": TXN, "authResult": "success", "message": "Done"})

    # VerifyEmail controller body: email + otp + txnId + patient_id
    res = client.post("/api/v1/abha/email/verify",
                      json={"email": "a@b.co", "otp": "123456", "txnId": TXN, "patient_id": 5}, headers=legacy)

    assert res.status_code == 200 and res.get_json()["data"]["authResult"] == "success"
    sent = json.loads(_last(abdm, PROFILE_VERIFY).body)
    assert sent["scope"] == ["abha-profile", "email-verify"]


@pytest.mark.parametrize("path,body", [
    ("mobile/verify", {"otp": "000000", "txnId": TXN, "mobile": "9876543210"}),
    ("email/verify", {"email": "a@b.co", "otp": "000000", "txnId": TXN}),
])
def test_wrong_otp_is_an_http_error(client, legacy, abdm, path, body):
    abdm.add(responses.POST, PROFILE_VERIFY, json={"txnId": TXN, "authResult": "failed", "message": "Wrong OTP"})
    res = client.post(f"/api/v1/abha/{path}", json=body, headers=legacy)
    assert res.status_code == 400
    assert res.get_json()["success"] is False
    assert res.get_json()["message"]["error"]["code"] == "INVALID_OTP"


# ---- ABHA being created: gateway token + txnId --------------------------------------------------------------------

def test_mobile_otp_and_verify_for_an_abha_being_created(client, auth, abdm):
    abdm.add(responses.POST, ENROL_OTP, json={"txnId": TXN, "message": "OTP sent"})
    abdm.add(responses.POST, ENROL_VERIFY, json={"txnId": TXN, "authResult": "success", "message": "Linked"})

    res = client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210", "txnId": TXN}, headers=auth)
    assert res.status_code == 200
    assert json.loads(_last(abdm, ENROL_OTP).body)["scope"] == ["abha-enrol", "mobile-verify"]

    res = client.post("/api/v1/abha/mobile/verify", json={"otp": "123456", "txnId": TXN, "mobile": "9876543210"},
                      headers=auth)
    assert res.status_code == 200 and res.get_json()["data"]["authResult"] == "success"
    assert PROFILE_OTP not in [c.request.url for c in abdm.calls]


def test_wrong_otp_while_creating_an_abha_is_an_http_error(client, auth, abdm):
    abdm.add(responses.POST, ENROL_VERIFY, json={"txnId": TXN, "authResult": "failed", "message": "Wrong OTP"})
    res = client.post("/api/v1/abha/mobile/verify", json={"otp": "000000", "txnId": TXN}, headers=auth)
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "INVALID_OTP"


# ---- input and auth -----------------------------------------------------------------------------------------------

def test_neither_abha_token_nor_txn_id_asks_for_the_token(client, auth, abdm):
    res = client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210"}, headers=auth)
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "MISSING_X_TOKEN"


def test_email_verify_needs_the_abha_token(client, auth, abdm):
    res = client.post("/api/v1/abha/email/verify", json={"email": "a@b.co", "otp": "123456", "txnId": TXN},
                      headers=auth)
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "MISSING_X_TOKEN"


def test_invalid_mobile_is_rejected_without_calling_abdm(client, legacy, abdm):
    res = client.post("/api/v1/abha/mobile/otp", json={"mobile": "12345"}, headers=legacy)
    assert res.status_code == 400
    assert all("sessions" in c.request.url for c in abdm.calls)


def test_wrong_client_secret_is_rejected(client, legacy, abdm):
    res = client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210"},
                      headers={**legacy, "Client-Secret": "wrong"})
    assert res.status_code == 401 and res.get_json()["message"]["error"]["code"] == "INVALID_CLIENT"


def test_a_user_token_without_client_credentials_is_rejected(client, abdm):
    res = client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210"},
                      headers={"Authorization": "abha-user-tok"})
    assert res.status_code == 401


def test_no_authorization_at_all_is_rejected(client, abdm):
    assert client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210"}).status_code == 401


def test_client_credentials_alone_are_enough_for_the_card_style_call(client, api_client, abdm):
    """No Authorization, just Client-Id/Secret plus the ABHA token header: the client is still identified."""
    tenant, secret = api_client
    abdm.add(responses.POST, PROFILE_OTP, json={"txnId": TXN, "message": "OTP sent"})
    res = client.post("/api/v1/abha/mobile/otp", json={"mobile": "9876543210"},
                      headers={"Client-Id": tenant.client_id, "Client-Secret": secret, "X-Token": "Bearer abha-user-tok"})
    assert res.status_code == 200


def test_other_endpoints_do_not_accept_client_credentials(client, api_client, abdm):
    """Only the i-medicus compatibility endpoints take Client-Id/Secret in place of a gateway token."""
    tenant, secret = api_client
    res = client.post("/api/v1/abha/profile/mobile/otp", json={"mobile": "9876543210"},
                      headers={"Authorization": "abha-user-tok", "Client-Id": tenant.client_id,
                               "Client-Secret": secret})
    assert res.status_code == 401
