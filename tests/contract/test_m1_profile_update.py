"""
Contract tests for changing the mobile / verifying the email of an existing ABHA (profile OTP flows).
They need the ABHA user token (X-token), unlike `enrol/mobile/*` which belongs to ABHA creation.
"""
import json

import pytest
import responses

from tests.conftest import ABHA, decrypt

TXN = "a1b2c3d4-0000-1111-2222-333344445555"
USER = {"X-Token": "Bearer user-tok"}
OTP_URL = f"{ABHA}/abha/api/v3/profile/account/request/otp"
VERIFY_URL = f"{ABHA}/abha/api/v3/profile/account/verify"
SCOPES = {"mobile": ["abha-profile", "mobile-verify"], "email": ["abha-profile", "email-verify"]}
VALUES = {"mobile": "9876543210", "email": "a@b.co"}


def _last(mock, url):
    calls = [c for c in mock.calls if c.request.url == url]
    assert calls, f"no call to {url}"
    return calls[-1].request


@pytest.mark.parametrize("kind", ["mobile", "email"])
def test_otp_is_sent_with_the_user_token_and_an_encrypted_value(client, auth, abdm, kind):
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})
    res = client.post(f"/api/v1/abha/profile/{kind}/otp", json={kind: VALUES[kind]}, headers={**auth, **USER})

    assert res.status_code == 200
    assert res.get_json()["data"] == {"txnId": TXN, "message": "OTP sent"}
    req = _last(abdm, OTP_URL)
    sent = json.loads(req.body)
    assert req.headers["X-token"] == "Bearer user-tok" and req.headers["Authorization"] == "Bearer gw-token"
    assert sent["scope"] == SCOPES[kind] and sent["loginHint"] == kind and sent["otpSystem"] == "abdm"
    assert decrypt(sent["loginId"]) == VALUES[kind]


def test_user_token_may_come_in_the_body_or_the_query(client, auth, abdm):
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})
    assert client.post("/api/v1/abha/profile/mobile/otp", json={"mobile": "9876543210", "xToken": "body-tok"},
                       headers=auth).status_code == 200
    assert _last(abdm, OTP_URL).headers["X-token"] == "Bearer body-tok"
    assert client.post("/api/v1/abha/profile/mobile/otp?xToken=query-tok", json={"mobileNumber": "9876543210"},
                       headers=auth).status_code == 200
    assert _last(abdm, OTP_URL).headers["X-token"] == "Bearer query-tok"


@pytest.mark.parametrize("kind", ["mobile", "email"])
def test_verify_sends_the_otp_encrypted_with_the_same_scope(client, auth, abdm, kind):
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})
    abdm.add(responses.POST, VERIFY_URL, json={"txnId": TXN, "authResult": "success", "message": "Done"})
    client.post(f"/api/v1/abha/profile/{kind}/otp", json={kind: VALUES[kind]}, headers={**auth, **USER})

    res = client.post(f"/api/v1/abha/profile/{kind}/verify", json={"txnId": TXN, "otp": "123456"},
                      headers={**auth, **USER})

    assert res.status_code == 200 and res.get_json()["success"] is True
    assert res.get_json()["data"]["authResult"] == "success"
    req = _last(abdm, VERIFY_URL)
    sent = json.loads(req.body)
    assert req.headers["X-token"] == "Bearer user-tok"
    assert sent["scope"] == SCOPES[kind]
    assert sent["authData"]["authMethods"] == ["otp"] and sent["authData"]["otp"]["txnId"] == TXN
    assert decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"


def test_wrong_otp_is_http_200_with_success_false(client, auth, abdm):
    abdm.add(responses.POST, VERIFY_URL, json={"txnId": TXN, "authResult": "failed", "message": "Wrong OTP"})
    res = client.post("/api/v1/abha/profile/mobile/verify", json={"txnId": TXN, "otp": "000000"},
                      headers={**auth, **USER})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is False and body["data"]["authResult"] == "failed"


def test_txn_of_another_client_is_forbidden(client, auth, other_auth, abdm):
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})
    client.post("/api/v1/abha/profile/mobile/otp", json={"mobile": "9876543210"}, headers={**auth, **USER})
    res = client.post("/api/v1/abha/profile/mobile/verify", json={"txnId": TXN, "otp": "123456"},
                      headers={**other_auth, **USER})
    assert res.status_code == 403
    assert res.get_json()["message"]["error"]["code"] == "TXN_FORBIDDEN"


@pytest.mark.parametrize("path,body,field", [
    ("mobile/otp", {"mobile": "12345"}, "mobileNumber"),
    ("mobile/otp", {}, "mobileNumber"),
    ("email/otp", {"email": "not-an-email"}, "email"),
    ("mobile/verify", {"txnId": TXN, "otp": "12"}, "otp"),
    ("email/verify", {"otp": "123456"}, "txnId"),
])
def test_invalid_input_is_rejected_without_calling_abdm(client, auth, abdm, path, body, field):
    res = client.post(f"/api/v1/abha/profile/{path}", json=body, headers={**auth, **USER})
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"]["field"] == field
    assert abdm.calls == [] or all("sessions" in c.request.url for c in abdm.calls)


@pytest.mark.parametrize("path", ["mobile/otp", "mobile/verify", "email/otp", "email/verify"])
def test_user_token_is_required(client, auth, abdm, path):
    body = {"mobile": "9876543210", "email": "a@b.co", "txnId": TXN, "otp": "123456"}
    res = client.post(f"/api/v1/abha/profile/{path}", json=body, headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "MISSING_X_TOKEN"


@pytest.mark.parametrize("path", ["mobile/otp", "email/otp", "mobile/verify", "email/verify"])
def test_requires_a_client_token(client, path):
    assert client.post(f"/api/v1/abha/profile/{path}", json={}).status_code == 401
