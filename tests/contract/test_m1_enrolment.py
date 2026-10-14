"""
Contract tests for ABHA enrolment. For each endpoint:
- what the gateway sends to ABDM (path, scopes, encrypted fields)
- that the keys i-medicus reads (ABDMService.php) are present in the response
"""
import json

import responses

from tests.conftest import ABHA, decrypt

AADHAAR = "999941057058"  # passes the Verhoeff check (ABDM sandbox test number)
TXN = "a1b2c3d4-0000-1111-2222-333344445555"


def _sent(mock, path_part):
    calls = [c for c in mock.calls if path_part in c.request.url]
    assert calls, f"no call to {path_part}"
    return json.loads(calls[-1].request.body)


def test_aadhaar_otp(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/request/otp",
             json={"txnId": TXN, "message": "OTP sent to Aadhaar registered mobile number ending with ******1234"})

    res = client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": AADHAAR}, headers=auth)

    assert res.status_code == 200
    body = res.get_json()
    assert body["data"]["txnId"] == TXN          # register.js reads data.data.data.txnId
    assert "OTP sent" in body["data"]["message"]
    sent = _sent(abdm, "enrollment/request/otp")
    assert sent["scope"] == ["abha-enrol"] and sent["loginHint"] == "aadhaar" and sent["otpSystem"] == "aadhaar"
    assert decrypt(sent["loginId"]) == AADHAAR
    req = [c for c in abdm.calls if "enrollment/request/otp" in c.request.url][-1].request
    assert req.headers["Authorization"] == "Bearer gw-token"
    assert req.headers["X-CM-ID"] == "sbx" and req.headers["REQUEST-ID"] and req.headers["TIMESTAMP"]


def test_aadhaar_otp_rejects_invalid_aadhaar_without_calling_abdm(client, auth, abdm):
    res = client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": "999941057059"}, headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "INVALID_AADHAAR"
    assert not [c for c in abdm.calls if "enrollment" in c.request.url]


def test_aadhaar_otp_rate_limit_keeps_abdm_status_and_text(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/request/otp", status=429,
             json={"message": "You can request for new OTP after 30 seconds"})
    res = client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": AADHAAR}, headers=auth)
    assert res.status_code == 429
    # AbdmErrorTranslator::forAadhaarOtp matches "request for new OTP" + "N seconds"
    assert "request for new OTP after 30 seconds" in res.get_json()["message"]["error"]["message"]


def test_aadhaar_otp_uidai_error_text_passed_through(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/request/otp", status=422,
             json={"error": {"code": "ABDM-1204", "message": "UIDAI Error code : 111 : Aadhaar does not have verified mobile"}})
    res = client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": AADHAAR}, headers=auth)
    assert res.status_code == 422
    err = res.get_json()["message"]["error"]
    assert err["code"] == "ABDM-1204"
    assert "UIDAI Error code : 111" in err["message"]


def test_aadhaar_verify_maps_profile_to_imedicus_keys(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/enrol/byAadhaar", json={
        "message": "Account created successfully",
        "txnId": TXN,
        "tokens": {"token": "x-tok", "expiresIn": 1800, "refreshToken": "r-tok", "refreshExpiresIn": 1296000},
        "ABHAProfile": {
            "firstName": "Ramesh", "middleName": "", "lastName": "Kumar", "dob": "01-01-1990", "gender": "M",
            "mobile": "9876543210", "mobileVerified": True, "email": None, "phrAddress": ["ramesh.k@sbx"],
            "address": "Delhi", "districtCode": "1", "stateCode": "7", "pinCode": "110001",
            "abhaType": "STANDARD", "stateName": "DELHI", "districtName": "New Delhi",
            "ABHANumber": "91-1234-5678-9012", "abhaStatus": "ACTIVE", "preferredAddress": "ramesh.k@sbx",
        },
        "isNew": True,
    })

    res = client.post("/api/v1/abha/enrol/aadhaar/verify",
                      json={"txnId": TXN, "otpValue": "123456", "mobilenumber": "9876543210"}, headers=auth)

    assert res.status_code == 200
    data = res.get_json()["data"]
    profile = data["abhaProfile"]
    # keys read by ABDMService::verifyAadhaarOtp
    for key in ("abhaNumber", "firstName", "middleName", "lastName", "gender", "email", "dob", "mobile",
                "mobileVerified", "preferredAddress", "phrAddress", "abhaStatus", "abhaType", "stateCode",
                "districtCode", "stateName", "districtName", "pinCode", "address"):
        assert key in profile, key
    assert profile["abhaNumber"] == "91-1234-5678-9012"
    assert data["tokens"]["token"] == "x-tok" and data["tokens"]["refreshExpiresIn"] == 1296000
    assert data["isNew"] is True and data["txnId"] == TXN

    sent = _sent(abdm, "enrol/byAadhaar")
    assert decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"
    assert sent["authData"]["otp"]["mobile"] == "9876543210"
    assert sent["consent"] == {"code": "abha-enrollment", "version": "1.4"}


def test_aadhaar_verify_accepts_otp_alias(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/enrol/byAadhaar",
             json={"txnId": TXN, "ABHAProfile": {"ABHANumber": "1"}, "tokens": {}})
    res = client.post("/api/v1/abha/enrol/aadhaar/verify",
                      json={"txnId": TXN, "otp": "123456", "mobileNumber": "9876543210"}, headers=auth)
    assert res.status_code == 200


def test_mobile_update_otp_and_verify(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/request/otp",
             json={"txnId": TXN, "message": "OTP sent to mobile number ending with ******3210"})
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/auth/byAbdm",
             json={"txnId": TXN, "authResult": "success", "message": "Mobile verified"})

    res = client.post("/api/v1/abha/enrol/mobile/otp", json={"txnId": TXN, "mobilenumber": "9876543210"}, headers=auth)
    assert res.status_code == 200 and res.get_json()["data"]["message"]
    sent = _sent(abdm, "enrollment/request/otp")
    assert sent["scope"] == ["abha-enrol", "mobile-verify"] and sent["txnId"] == TXN
    assert decrypt(sent["loginId"]) == "9876543210"

    res = client.post("/api/v1/abha/enrol/mobile/verify", json={"txnId": TXN, "otp": "654321"}, headers=auth)
    assert res.status_code == 200
    assert res.get_json()["data"]["authResult"] == "success"
    assert decrypt(_sent(abdm, "auth/byAbdm")["authData"]["otp"]["otpValue"]) == "654321"


def test_mobile_verify_wrong_otp_is_200_success_false(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/auth/byAbdm",
             json={"txnId": TXN, "authResult": "failed", "message": "Please enter a valid OTP"})
    res = client.post("/api/v1/abha/enrol/mobile/verify", json={"txnId": TXN, "otp": "000000"}, headers=auth)
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is False
    assert body["data"]["authResult"] == "failed"


def test_address_suggestions_and_create(client, auth, abdm):
    abdm.add(responses.GET, f"{ABHA}/abha/api/v3/enrollment/enrol/suggestion",
             json={"txnId": TXN, "abhaAddressList": ["ramesh.k", "ramesh1990"]})
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/enrol/abha-address",
             json={"txnId": TXN, "healthIdNumber": "91-1234-5678-9012", "preferredAbhaAddress": "ramesh.k@sbx"})

    res = client.get(f"/api/v1/abha/enrol/address-suggestions?txnId={TXN}", headers=auth)
    assert res.status_code == 200
    assert res.get_json()["data"] == {"txnId": TXN, "abhaAddressList": ["ramesh.k", "ramesh1990"]}
    suggestion_call = [c for c in abdm.calls if "enrol/suggestion" in c.request.url][-1]
    assert suggestion_call.request.headers["Transaction_Id"] == TXN

    res = client.post("/api/v1/abha/enrol/address",
                      json={"txnId": TXN, "abhaAddress": "ramesh.k", "preferred": 1}, headers=auth)
    assert res.status_code == 200
    assert _sent(abdm, "enrol/abha-address") == {"txnId": TXN, "abhaAddress": "ramesh.k", "preferred": 1}


def test_email_verification_link_uses_x_token(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/profile/account/request/emailVerificationLink",
             json={"txnId": TXN, "message": "Verification link sent"})
    res = client.post("/api/v1/abha/profile/email/verify-link", json={"email": "a@b.co"},
                      headers={**auth, "X-Token": "Bearer user-tok"})
    assert res.status_code == 200 and res.get_json()["data"]["txnId"] == TXN
    call = [c for c in abdm.calls if "emailVerificationLink" in c.request.url][-1]
    assert call.request.headers["X-token"] == "Bearer user-tok"
    assert decrypt(json.loads(call.request.body)["loginId"]) == "a@b.co"


def test_txn_of_another_client_is_forbidden(client, auth, other_auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/request/otp", json={"txnId": TXN, "message": "sent"})
    client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": AADHAAR}, headers=auth)

    res = client.post("/api/v1/abha/enrol/aadhaar/verify",
                      json={"txnId": TXN, "otpValue": "123456", "mobilenumber": "9876543210"}, headers=other_auth)
    assert res.status_code == 403
    assert res.get_json()["message"]["error"]["code"] == "TXN_FORBIDDEN"


def test_requires_access_token(client, abdm):
    res = client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": AADHAAR})
    assert res.status_code == 401
