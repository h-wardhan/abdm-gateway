"""ABDM v3 search + login APIs (ABHA number / mobile / Aadhaar / ABHA address)."""
from src.app.abdm.http import abdm_request, bearer

PROFILE = "/abha/api/v3/profile"
PHR = "/abha/api/v3/phr/web/login/abha"


def search_by_mobile(enc_mobile):
    return abdm_request("POST", f"{PROFILE}/account/abha/search", json={
        "scope": ["search-abha"],
        "mobile": enc_mobile,
    }, endpoint_name="profile/account/abha/search")


def request_login_otp(scope, login_hint, enc_login_id, otp_system, txn_id=None):
    payload = {
        "scope": scope,
        "loginHint": login_hint,
        "loginId": enc_login_id,
        "otpSystem": otp_system,
    }
    if txn_id:
        payload["txnId"] = txn_id
    return abdm_request("POST", f"{PROFILE}/login/request/otp", json=payload,
                        endpoint_name=f"profile/login/request/otp[{login_hint}]")


def verify_login_otp(scope, txn_id, enc_otp):
    return abdm_request("POST", f"{PROFILE}/login/verify", json={
        "scope": scope,
        "authData": {"authMethods": ["otp"], "otp": {"txnId": txn_id, "otpValue": enc_otp}},
    }, endpoint_name="profile/login/verify")


def verify_login_user(t_token, abha_number, txn_id):
    """When one mobile has several ABHAs: pick the account after OTP verify (T-token from verify)."""
    return abdm_request("POST", f"{PROFILE}/login/verify/user", json={
        "ABHANumber": abha_number,
        "txnId": txn_id,
    }, headers={"T-token": bearer(t_token)}, endpoint_name="profile/login/verify/user")


def search_abha_address(abha_address):
    return abdm_request("POST", f"{PHR}/search", json={"abhaAddress": abha_address},
                        endpoint_name="phr/web/login/abha/search")


def request_abha_address_otp(scope, enc_abha_address, otp_system):
    return abdm_request("POST", f"{PHR}/request/otp", json={
        "scope": scope,
        "loginHint": "abha-address",
        "loginId": enc_abha_address,
        "otpSystem": otp_system,
    }, endpoint_name="phr/web/login/abha/request/otp")


def verify_abha_address_otp(scope, txn_id, enc_otp):
    return abdm_request("POST", f"{PHR}/verify", json={
        "scope": scope,
        "authData": {"authMethods": ["otp"], "otp": {"txnId": txn_id, "otpValue": enc_otp}},
    }, endpoint_name="phr/web/login/abha/verify")
