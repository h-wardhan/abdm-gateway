"""ABDM v3 enrolment (ABHA creation) APIs. Values passed in are already RSA-encrypted where ABDM requires it."""
from src.app.abdm.http import abdm_request, bearer

BASE = "/abha/api/v3/enrollment"


def request_aadhaar_otp(enc_aadhaar):
    return abdm_request("POST", f"{BASE}/request/otp", json={
        "txnId": "",
        "scope": ["abha-enrol"], 
        "loginHint": "aadhaar",
        "loginId": enc_aadhaar,
        "otpSystem": "aadhaar", 
    }, endpoint_name="enrollment/request/otp[aadhaar]")


def enrol_by_aadhaar(txn_id, enc_otp, mobile):
    return abdm_request("POST", f"{BASE}/enrol/byAadhaar", json={
        "authData": {
            "authMethods": ["otp"],
            "otp": {"txnId": txn_id, "otpValue": enc_otp, "mobile": mobile},
        },
        "consent": {"code": "abha-enrollment", "version": "1.4"},
    }, endpoint_name="enrollment/enrol/byAadhaar")


def request_mobile_update_otp(txn_id, enc_mobile):
    return abdm_request("POST", f"{BASE}/request/otp", json={
        "txnId": txn_id,
        "scope": ["abha-enrol", "mobile-verify"],
        "loginHint": "mobile",
        "loginId": enc_mobile,
        "otpSystem": "abdm",
    }, endpoint_name="enrollment/request/otp[mobile]")


def verify_mobile_update_otp(txn_id, enc_otp):
    return abdm_request("POST", f"{BASE}/auth/byAbdm", json={
        "scope": ["abha-enrol", "mobile-verify"],
        "authData": {"authMethods": ["otp"], "otp": {"txnId": txn_id, "otpValue": enc_otp}},
    }, endpoint_name="enrollment/auth/byAbdm")


def address_suggestions(txn_id):
    return abdm_request("GET", f"{BASE}/enrol/suggestion", headers={"Transaction_Id": txn_id},
                        endpoint_name="enrollment/enrol/suggestion")


def create_abha_address(txn_id, abha_address, preferred=1):
    return abdm_request("POST", f"{BASE}/enrol/abha-address", json={
        "txnId": txn_id,
        "abhaAddress": abha_address,
        "preferred": int(preferred),
    }, endpoint_name="enrollment/enrol/abha-address")


def request_email_verification_link(x_token, enc_email):
    return abdm_request("POST", "/abha/api/v3/profile/account/request/emailVerificationLink", json={
        "scope": ["abha-profile", "email-link-verify"],
        "loginHint": "email",
        "loginId": enc_email,
        "otpSystem": "abdm",
    }, headers={"X-token": bearer(x_token)}, endpoint_name="profile/account/request/emailVerificationLink")


# ---- Driving licence (document) enrolment --------------------------------------------------------------------
DL_SCOPE = ["abha-enrol", "mobile-verify", "dl-flow"]


def request_dl_otp(enc_mobile):
    return abdm_request("POST", f"{BASE}/request/otp", json={
        "scope": DL_SCOPE,
        "loginHint": "mobile",
        "loginId": enc_mobile,
        "otpSystem": "abdm",
    }, endpoint_name="enrollment/request/otp[dl]")


def verify_dl_otp(txn_id, enc_otp, timestamp):
    return abdm_request("POST", f"{BASE}/auth/byAbdm", json={
        "scope": DL_SCOPE,
        "authData": {"authMethods": ["otp"], "otp": {"timeStamp": timestamp, "txnId": txn_id, "otpValue": enc_otp}},
    }, endpoint_name="enrollment/auth/byAbdm[dl]")


def enrol_by_document(fields):
    return abdm_request("POST", f"{BASE}/enrol/byDocument", json={
        **fields,
        "consent": {"code": "abha-enrollment", "version": "1.4"},
    }, endpoint_name="enrollment/enrol/byDocument")


# ---- Other enrol/byAadhaar auth methods ----------------------------------------------------------------------

def enrol_by_auth_data(auth_data, name):
    return abdm_request("POST", f"{BASE}/enrol/byAadhaar", json={
        "authData": auth_data,
        "consent": {"code": "abha-enrollment", "version": "1.4"},
    }, endpoint_name=f"enrollment/enrol/byAadhaar[{name}]")


def enrol_child(x_token, child):
    return abdm_request("POST", f"{BASE}/enrol/byAadhaar", json={
        "authData": {"authMethods": ["child"], "child": child},
        "consent": {"code": "abha-enrollment", "version": "1.4"},
    }, headers={"X-token": bearer(x_token)}, endpoint_name="enrollment/enrol/byAadhaar[child]")


def list_children(x_token):
    return abdm_request("GET", f"{BASE}/profile/children", headers={"X-token": bearer(x_token)},
                        endpoint_name="enrollment/profile/children")
