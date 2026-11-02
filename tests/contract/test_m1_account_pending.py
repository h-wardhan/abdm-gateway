"""
Contract tests for the M1 calls added from the ABDM Milestone-1 Postman collection: account delete / deactivate /
reactivate / re-KYC / password / profile PATCH / logout / refresh, driving-licence + biometric + child enrolment,
forgot ABHA / retrieval / biometric / password login, and the benefit APIs.
Each test pins the exact path, scope and body the collection uses.
"""
import json

import pytest
import responses

from tests.conftest import ABHA, decrypt

TXN = "a1b2c3d4-0000-1111-2222-333344445555"
USER = {"X-Token": "Bearer user-tok"}
ABHA_NO = "91-1234-5678-9012"
ACCT = f"{ABHA}/abha/api/v3/profile/account"
LOGIN = f"{ABHA}/abha/api/v3/profile/login"
ENROL = f"{ABHA}/abha/api/v3/enrollment"
PHR = f"{ABHA}/abha/api/v3/phr/web/login/abha"
OK = {"txnId": TXN, "message": "ok", "authResult": "success"}


def _sent(mock, url, method=None):
    calls = [c for c in mock.calls if c.request.url == url and (method is None or c.request.method == method)]
    assert calls, f"no call to {url}"
    return calls[-1].request, json.loads(calls[-1].request.body or "{}")


def _post(client, auth, path, body, headers=None):
    return client.post(path, json=body, headers={**auth, **(headers or {})})


# ---- delete / deactivate / re-KYC -----------------------------------------------------------------------------

@pytest.mark.parametrize("action,scope", [
    ("delete", ["abha-profile", "delete"]),
    ("deactivate", ["abha-profile", "de-activate"]),
    ("re-kyc", ["abha-profile", "re-kyc"]),
])
@pytest.mark.parametrize("method,system", [("aadhaar", "aadhaar"), ("abha", "abdm")])
def test_action_otp(client, auth, abdm, action, scope, method, system):
    abdm.add(responses.POST, f"{ACCT}/request/otp", json=OK)
    res = _post(client, auth, f"/api/v1/abha/account/{action}/otp", {"abhaNumber": ABHA_NO, "method": method}, USER)
    assert res.status_code == 200 and res.get_json()["data"]["txnId"] == TXN
    req, sent = _sent(abdm, f"{ACCT}/request/otp")
    assert req.headers["X-token"] == "Bearer user-tok"
    assert sent["scope"] == scope and sent["loginHint"] == "abha-number" and sent["otpSystem"] == system
    assert decrypt(sent["loginId"]) == ABHA_NO


@pytest.mark.parametrize("action,scope", [
    ("delete", ["abha-profile", "delete"]),
    ("deactivate", ["abha-profile", "de-activate"]),
])
def test_delete_and_deactivate_verify_with_otp_send_reasons(client, auth, abdm, action, scope):
    abdm.add(responses.POST, f"{ACCT}/verify", json=OK)
    res = _post(client, auth, f"/api/v1/abha/account/{action}/verify",
                {"method": "aadhaar", "txnId": TXN, "otp": "123456", "reasons": ["moved", "other"]}, USER)
    assert res.status_code == 200
    _, sent = _sent(abdm, f"{ACCT}/verify")
    assert sent["scope"] == scope and sent["reasons"] == ["moved", "other"]
    assert sent["authData"]["authMethods"] == ["otp"] and sent["authData"]["otp"]["txnId"] == TXN
    assert decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"


@pytest.mark.parametrize("action", ["delete", "deactivate"])
def test_delete_and_deactivate_verify_with_password(client, auth, abdm, action):
    abdm.add(responses.POST, f"{ACCT}/verify", json=OK)
    res = _post(client, auth, f"/api/v1/abha/account/{action}/verify",
                {"method": "password", "password": "Secret@123", "reasons": "no longer needed"}, USER)
    assert res.status_code == 200
    _, sent = _sent(abdm, f"{ACCT}/verify")
    assert sent["authData"]["authMethods"] == ["password"]
    assert decrypt(sent["authData"]["password"]["password"]) == "Secret@123"
    assert sent["reasons"] == ["no longer needed"]


@pytest.mark.parametrize("action", ["delete", "deactivate"])
def test_reasons_are_required_and_nothing_reaches_abdm(client, auth, abdm, action):
    res = _post(client, auth, f"/api/v1/abha/account/{action}/verify",
                {"method": "aadhaar", "txnId": TXN, "otp": "123456"}, USER)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"]["field"] == "reasons"
    assert all("sessions" in c.request.url for c in abdm.calls)


def test_rekyc_verify_sends_no_reasons(client, auth, abdm):
    abdm.add(responses.POST, f"{ACCT}/verify", json=OK)
    _post(client, auth, "/api/v1/abha/account/re-kyc/verify", {"method": "aadhaar", "txnId": TXN, "otp": "123456"}, USER)
    _, sent = _sent(abdm, f"{ACCT}/verify")
    assert sent["scope"] == ["abha-profile", "re-kyc"] and "reasons" not in sent


@pytest.mark.parametrize("path", ["delete/otp", "deactivate/verify", "re-kyc/otp", "password", "logout"])
def test_user_token_is_required(client, auth, abdm, path):
    body = {"abhaNumber": ABHA_NO, "method": "aadhaar", "txnId": TXN, "otp": "123456", "reasons": ["x"],
            "newPassword": "Secret@123"}
    res = client.get(f"/api/v1/abha/account/{path}", headers=auth) if path == "logout" else _post(
        client, auth, f"/api/v1/abha/account/{path}", body)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "MISSING_X_TOKEN"


def test_txn_of_another_client_is_forbidden(client, auth, other_auth, abdm):
    abdm.add(responses.POST, f"{ACCT}/request/otp", json=OK)
    _post(client, auth, "/api/v1/abha/account/delete/otp", {"abhaNumber": ABHA_NO}, USER)
    res = _post(client, other_auth, "/api/v1/abha/account/delete/verify",
                {"method": "aadhaar", "txnId": TXN, "otp": "123456", "reasons": ["x"]}, USER)
    assert res.status_code == 403


# ---- reactivate (login endpoints, no ABHA token) --------------------------------------------------------------

@pytest.mark.parametrize("method,scope,system", [
    ("aadhaar", ["abha-login", "aadhaar-verify", "re-activate"], "aadhaar"),
    ("abha", ["abha-login", "mobile-verify", "re-activate"], "abdm"),
])
def test_reactivate_otp_and_verify(client, auth, abdm, method, scope, system):
    abdm.add(responses.POST, f"{LOGIN}/request/otp", json=OK)
    abdm.add(responses.POST, f"{LOGIN}/verify", json={**OK, "token": "t"})
    _post(client, auth, "/api/v1/abha/account/reactivate/otp", {"abhaNumber": ABHA_NO, "method": method})
    _, sent = _sent(abdm, f"{LOGIN}/request/otp")
    assert sent["scope"] == scope and sent["loginHint"] == "abha-number" and sent["otpSystem"] == system
    assert decrypt(sent["loginId"]) == ABHA_NO

    res = _post(client, auth, "/api/v1/abha/account/reactivate/verify", {"method": method, "txnId": TXN, "otp": "123456"})
    assert res.status_code == 200
    req, sent = _sent(abdm, f"{LOGIN}/verify")
    assert sent["scope"] == scope and "X-token" not in req.headers
    assert decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"


def test_reactivate_by_password(client, auth, abdm):
    abdm.add(responses.POST, f"{LOGIN}/verify", json=OK)
    _post(client, auth, "/api/v1/abha/account/reactivate/verify",
          {"method": "password", "abhaNumber": ABHA_NO, "password": "Secret@123"})
    _, sent = _sent(abdm, f"{LOGIN}/verify")
    assert sent["scope"] == ["abha-login", "password-verify", "re-activate"]
    assert sent["authData"]["password"]["ABHANumber"] == ABHA_NO
    assert decrypt(sent["authData"]["password"]["password"]) == "Secret@123"


# ---- password -------------------------------------------------------------------------------------------------

def test_change_password_encrypts_all_three(client, auth, abdm):
    abdm.add(responses.POST, f"{ACCT}/verify", json=OK)
    _post(client, auth, "/api/v1/abha/account/password",
          {"oldPassword": "Old@1234", "newPassword": "New@1234", "confirmPassword": "New@1234"}, USER)
    _, sent = _sent(abdm, f"{ACCT}/verify")
    pw = sent["authData"]["password"]
    assert sent["scope"] == ["abha-profile", "change-password"] and sent["authData"]["authMethods"] == ["password"]
    assert (decrypt(pw["oldPassword"]), decrypt(pw["newPassword"]), decrypt(pw["confirmPassword"])) == (
        "Old@1234", "New@1234", "New@1234")


def test_set_password_without_old_one(client, auth, abdm):
    abdm.add(responses.POST, f"{ACCT}/verify", json=OK)
    _post(client, auth, "/api/v1/abha/account/password", {"newPassword": "New@1234"}, USER)
    _, sent = _sent(abdm, f"{ACCT}/verify")
    assert set(sent["authData"]["password"]) == {"newPassword"}


def test_password_reset_by_otp(client, auth, abdm):
    abdm.add(responses.POST, f"{ACCT}/request/otp", json=OK)
    abdm.add(responses.POST, f"{ACCT}/verify", json=OK)
    _post(client, auth, "/api/v1/abha/account/password/otp", {"password": "New@1234", "method": "aadhaar"}, USER)
    _, sent = _sent(abdm, f"{ACCT}/request/otp")
    assert sent["scope"] == ["abha-profile", "change-password"] and sent["loginHint"] == "password"
    assert sent["otpSystem"] == "aadhaar"
    _post(client, auth, "/api/v1/abha/account/password/verify", {"txnId": TXN, "otp": "123456"}, USER)
    _, sent = _sent(abdm, f"{ACCT}/verify")
    assert sent["authData"]["authMethods"] == ["otp"]


def test_password_login(client, auth, abdm):
    abdm.add(responses.POST, f"{LOGIN}/search", json={"txnId": TXN})
    abdm.add(responses.POST, f"{LOGIN}/verify", json={**OK, "token": "t"})
    _post(client, auth, "/api/v1/abha/account/login/password/search", {"abhaNumber": ABHA_NO})
    assert _sent(abdm, f"{LOGIN}/search")[1] == {"ABHANumber": ABHA_NO}
    _post(client, auth, "/api/v1/abha/account/login/password/verify", {"abhaNumber": ABHA_NO, "password": "Secret@123"})
    _, sent = _sent(abdm, f"{LOGIN}/verify")
    assert sent["scope"] == ["abha-login", "password-verify"]
    assert decrypt(sent["authData"]["password"]["password"]) == "Secret@123"


# ---- profile PATCH / logout / refresh -------------------------------------------------------------------------

def test_profile_photo_patch_uses_the_user_token(client, auth, abdm):
    abdm.add(responses.PATCH, ACCT, json={"message": "updated"})
    res = client.patch("/api/v1/abha/account/profile", json={"profilePhoto": "BASE64", "junk": 1},
                       headers={**auth, **USER})
    assert res.status_code == 200
    req, sent = _sent(abdm, ACCT, "PATCH")
    assert sent == {"profilePhoto": "BASE64"} and req.headers["X-token"] == "Bearer user-tok"


def test_profile_patch_by_abha_number_needs_no_token(client, auth, abdm):
    abdm.add(responses.PATCH, ACCT, json={"message": "updated"})
    client.patch("/api/v1/abha/account/profile",
                 json={"abhaNumber": ABHA_NO, "mobile": "9876543210", "accountStatus": "ACTIVE"}, headers=auth)
    req, sent = _sent(abdm, ACCT, "PATCH")
    assert sent == {"abhaNumber": ABHA_NO, "mobile": "9876543210", "accountStatus": "ACTIVE"}
    assert "X-token" not in req.headers


def test_profile_patch_without_anything_to_identify_the_account(client, auth, abdm):
    res = client.patch("/api/v1/abha/account/profile", json={"name": "A"}, headers=auth)
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "MISSING_X_TOKEN"
    assert client.patch("/api/v1/abha/account/profile", json={}, headers={**auth, **USER}).status_code == 400


def test_logout_and_refresh(client, auth, abdm):
    abdm.add(responses.GET, f"{ACCT}/request/logout", json={"message": "bye"})
    abdm.add(responses.GET, f"{ACCT}/request/token", json={"token": "new"})
    assert client.get("/api/v1/abha/account/logout", headers={**auth, **USER}).status_code == 200
    assert _sent(abdm, f"{ACCT}/request/logout")[0].headers["X-token"] == "Bearer user-tok"
    res = client.get("/api/v1/abha/account/refresh-token", headers={**auth, "R-Token": "refresh-tok"})
    assert res.get_json()["data"]["token"] == "new"
    assert _sent(abdm, f"{ACCT}/request/token")[0].headers["R-token"] == "Bearer refresh-tok"
    assert client.get("/api/v1/abha/account/refresh-token", headers=auth).status_code == 400


# ---- driving licence enrolment --------------------------------------------------------------------------------

DL = {"txnId": TXN, "documentId": "DL-0420110012345", "firstName": "Ramesh", "middleName": "K", "lastName": "Kumar",
      "dob": "1990-05-04", "gender": "M", "frontSidePhoto": "F64", "backSidePhoto": "B64", "address": "Street 1",
      "state": "Delhi", "district": "New Delhi", "pinCode": "110001"}


def test_dl_enrolment_flow(client, auth, abdm):
    abdm.add(responses.POST, f"{ENROL}/request/otp", json=OK)
    abdm.add(responses.POST, f"{ENROL}/auth/byAbdm", json=OK)
    abdm.add(responses.POST, f"{ENROL}/enrol/byDocument", json={"message": "created"})
    scope = ["abha-enrol", "mobile-verify", "dl-flow"]

    _post(client, auth, "/api/v1/abha/enrol/dl/otp", {"mobile": "9876543210"})
    _, sent = _sent(abdm, f"{ENROL}/request/otp")
    assert sent["scope"] == scope and sent["loginHint"] == "mobile" and sent["otpSystem"] == "abdm"
    assert decrypt(sent["loginId"]) == "9876543210"

    _post(client, auth, "/api/v1/abha/enrol/dl/verify", {"txnId": TXN, "otp": "123456"})
    _, sent = _sent(abdm, f"{ENROL}/auth/byAbdm")
    assert sent["scope"] == scope and sent["authData"]["otp"]["txnId"] == TXN
    assert sent["authData"]["otp"]["timeStamp"].endswith("Z")
    assert decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"

    assert _post(client, auth, "/api/v1/abha/enrol/dl/create", DL).status_code == 200
    _, sent = _sent(abdm, f"{ENROL}/enrol/byDocument")
    assert sent == {**DL, "documentType": "DRIVING_LICENCE", "consent": {"code": "abha-enrollment", "version": "1.4"}}


def test_dl_create_requires_the_photos(client, auth, abdm):
    res = _post(client, auth, "/api/v1/abha/enrol/dl/create", {k: v for k, v in DL.items() if k != "frontSidePhoto"})
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"]["field"] == "frontSidePhoto"


# ---- biometric / demographic / child enrolment ----------------------------------------------------------------

AADHAAR = "234567890124"


@pytest.mark.parametrize("method,auth_method,pid_key", [
    ("fingerprint", "bio", "fingerPrintAuthPid"), ("face", "face", "rdPidData"), ("iris", "iris", "Pid")])
def test_biometric_enrolment(client, auth, abdm, method, auth_method, pid_key):
    abdm.add(responses.POST, f"{ENROL}/enrol/byAadhaar", json={"txnId": TXN, "ABHAProfile": {"ABHANumber": ABHA_NO}})
    res = _post(client, auth, f"/api/v1/abha/enrol/biometric/{method}",
                {"aadhaarNumber": AADHAAR, "pid": "PID-XML", "mobile": "9876543210"})
    assert res.status_code == 200
    assert res.get_json()["data"]["abhaProfile"]["abhaNumber"] == ABHA_NO
    _, sent = _sent(abdm, f"{ENROL}/enrol/byAadhaar")
    assert sent["authData"]["authMethods"] == [auth_method]
    inner = sent["authData"][auth_method]
    assert inner[pid_key] == "PID-XML" and inner["mobile"] == "9876543210" and decrypt(inner["aadhaar"]) == AADHAAR


def test_unknown_biometric_method(client, auth, abdm):
    assert _post(client, auth, "/api/v1/abha/enrol/biometric/palm", {}).status_code == 400


def test_child_abha(client, auth, abdm):
    abdm.add(responses.POST, f"{ENROL}/enrol/byAadhaar", json={"message": "child created"})
    abdm.add(responses.GET, f"{ENROL}/profile/children", json={"children": []})
    res = _post(client, auth, "/api/v1/abha/enrol/child",
                {"name": "Tiny", "gender": "M", "dayOfBirth": 4, "monthOfBirth": 5, "yearOfBirth": 2020}, USER)
    assert res.status_code == 200
    req, sent = _sent(abdm, f"{ENROL}/enrol/byAadhaar")
    assert req.headers["X-token"] == "Bearer user-tok" and sent["authData"]["authMethods"] == ["child"]
    child = sent["authData"]["child"]
    assert child["dayOfBirth"] == "4" and child["parentConsent"] == "true" and child["name"] == "Tiny"
    assert client.get("/api/v1/abha/enrol/child", headers={**auth, **USER}).status_code == 200


# ---- forgot ABHA / retrieval ----------------------------------------------------------------------------------

@pytest.mark.parametrize("path,body,scope,hint,system,plain", [
    ("forgot/mobile", {"mobile": "9876543210"}, ["abha-login", "mobile-verify"], "mobile", "abdm", "9876543210"),
    ("forgot/aadhaar", {"aadhaarNumber": AADHAAR}, ["abha-login", "aadhaar-verify"], "aadhaar", "aadhaar", AADHAAR),
    ("retrieval", {"mobile": "9876543210"}, ["abha-login", "mobile-verify", "retrieval"], "mobile", "abdm",
     "9876543210"),
])
def test_forgot_and_retrieval(client, auth, abdm, path, body, scope, hint, system, plain):
    abdm.add(responses.POST, f"{LOGIN}/request/otp", json=OK)
    abdm.add(responses.POST, f"{LOGIN}/verify", json={**OK, "accounts": []})
    _post(client, auth, f"/api/v1/abha/login/{path}/otp", body)
    _, sent = _sent(abdm, f"{LOGIN}/request/otp")
    assert sent["scope"] == scope and sent["loginHint"] == hint and sent["otpSystem"] == system
    assert decrypt(sent["loginId"]) == plain
    assert _post(client, auth, f"/api/v1/abha/login/{path}/verify", {"txnId": TXN, "otp": "123456"}).status_code == 200
    _, sent = _sent(abdm, f"{LOGIN}/verify")
    assert sent["scope"] == scope and decrypt(sent["authData"]["otp"]["otpValue"]) == "123456"


# ---- biometric login ------------------------------------------------------------------------------------------

BIO = [("fingerprint", "aadhaar-bio-verify", "bio", "fingerPrintAuthPid"),
       ("face", "aadhaar-face-verify", "face", "faceAuthPid"),
       ("iris", "aadhaar-iris-verify", "iris", "irisAuthPid")]


@pytest.mark.parametrize("method,token,auth_method,pid_key", BIO)
def test_biometric_login_by_abha_number(client, auth, abdm, method, token, auth_method, pid_key):
    abdm.add(responses.POST, f"{LOGIN}/request/otp", json=OK)
    abdm.add(responses.POST, f"{LOGIN}/verify", json={**OK, "token": "t"})
    _post(client, auth, f"/api/v1/abha/login/abha-number/biometric/{method}/otp", {"abhaNumber": ABHA_NO})
    _, sent = _sent(abdm, f"{LOGIN}/request/otp")
    assert sent["scope"] == ["abha-login", token] and sent["loginHint"] == "abha-number"
    assert sent["otpSystem"] == "aadhaar" and decrypt(sent["loginId"]) == ABHA_NO

    _post(client, auth, f"/api/v1/abha/login/abha-number/biometric/{method}/verify", {"txnId": TXN, "pid": "PID"})
    _, sent = _sent(abdm, f"{LOGIN}/verify")
    assert sent["scope"] == ["abha-login", token]
    assert sent["authData"] == {"authMethods": [auth_method], auth_method: {"txnId": TXN, pid_key: "PID"}}


def test_biometric_login_after_mobile_search(client, auth, abdm):
    abdm.add(responses.POST, f"{LOGIN}/request/otp", json=OK)
    _post(client, auth, "/api/v1/abha/login/mobile/biometric/face/otp", {"txnId": TXN, "index": 1})
    _, sent = _sent(abdm, f"{LOGIN}/request/otp")
    assert sent["scope"] == ["abha-login", "search-abha", "aadhaar-face-verify"]
    assert sent["loginHint"] == "index" and sent["txnId"] == TXN and decrypt(sent["loginId"]) == "1"


def test_biometric_login_by_abha_address(client, auth, abdm):
    abdm.add(responses.POST, f"{PHR}/request/otp", json=OK)
    abdm.add(responses.POST, f"{PHR}/verify", json=OK)
    _post(client, auth, "/api/v1/abha/login/abha-address/biometric/iris/otp", {"abhaAddress": "ramesh.kumar@sbx"})
    _, sent = _sent(abdm, f"{PHR}/request/otp")
    assert sent["scope"] == ["abha-login", "aadhaar-iris-verify"] and sent["loginHint"] == "abha-address"
    _post(client, auth, "/api/v1/abha/login/abha-address/biometric/iris/verify", {"txnId": TXN, "pid": "PID"})
    _, sent = _sent(abdm, f"{PHR}/verify")
    assert sent["authData"]["iris"] == {"txnId": TXN, "irisAuthPid": "PID"}


# ---- benefit --------------------------------------------------------------------------------------------------

BENEFIT = f"{ABHA}/abha/api/v3/profile/benefit"


def test_benefit_search(client, auth, abdm):
    abdm.add(responses.POST, f"{BENEFIT}/search", json={"found": True})
    _post(client, auth, "/api/v1/abha/benefit/search", {"xmlUid": "UID123", "benefitName": "PMJAY"})
    req, sent = _sent(abdm, f"{BENEFIT}/search")
    assert req.headers["BENEFIT_NAME"] == "PMJAY"
    _, sent = _sent(abdm, f"{BENEFIT}/search")
    assert sent["scope"] == ["search"] and sent["loginHint"] == "xmlUid" and decrypt(sent["loginId"]) == "UID123"
    _post(client, auth, "/api/v1/abha/benefit/search", {"abhaNumber": ABHA_NO, "benefitName": "PMJAY"})
    _, sent = _sent(abdm, f"{BENEFIT}/search")
    assert sent["loginHint"] == "abha-number" and decrypt(sent["loginId"]) == ABHA_NO


def test_benefit_link_and_delink(client, auth, abdm):
    abdm.add(responses.POST, f"{BENEFIT}/linkAndDelink", json={"message": "ok"})
    _post(client, auth, "/api/v1/abha/benefit/link", {"action": "link", "benefitName": "PMJAY"}, USER)
    req, sent = _sent(abdm, f"{BENEFIT}/linkAndDelink")
    assert sent == {"scope": ["link"]} and req.headers["X-token"] == "Bearer user-tok"

    _post(client, auth, "/api/v1/abha/benefit/link", {"action": "link", "abhaNumber": ABHA_NO, "benefitName": "PMJAY"})
    req, sent = _sent(abdm, f"{BENEFIT}/linkAndDelink")
    assert sent["scope"] == ["link"] and sent["loginHint"] == "abha-number" and "X-token" not in req.headers

    _post(client, auth, "/api/v1/abha/benefit/link", {"action": "de-link", "xmlUid": "UID123", "benefitName": "PMJAY"})
    _, sent = _sent(abdm, f"{BENEFIT}/linkAndDelink")
    assert sent["scope"] == ["de-link"] and sent["loginHint"] == "xmlUid"
    assert _post(client, auth, "/api/v1/abha/benefit/link", {"action": "x", "benefitName": "PMJAY"}).status_code == 400
    assert _post(client, auth, "/api/v1/abha/benefit/link", {"action": "link", "abhaNumber": ABHA_NO}).status_code == 400  # no benefitName


def test_benefit_abha_lookup(client, auth, abdm):
    abdm.add(responses.GET, f"{BENEFIT}/abha/{ABHA_NO}", json={"abhaNumber": ABHA_NO})
    res = client.get(f"/api/v1/abha/benefit/abha/{ABHA_NO}", headers={**auth, "BENEFIT_NAME": "PMJAY"})
    assert res.status_code == 200 and res.get_json()["data"]["abhaNumber"] == ABHA_NO


# ---- every new route needs a client token ---------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "account/delete/otp", "account/reactivate/verify", "account/password", "enrol/dl/create",
    "enrol/biometric/face", "enrol/child", "login/forgot/mobile/otp", "login/retrieval/verify",
    "login/abha-number/biometric/face/otp", "benefit/search", "benefit/link",
])
def test_new_routes_require_a_client_token(client, path):
    assert client.post(f"/api/v1/abha/{path}", json={}).status_code == 401
