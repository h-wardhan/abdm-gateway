"""ABDM v3 profile, card and QR APIs. All need the ABHA user token (X-token)."""
from src.app.abdm.http import abdm_request, bearer

ACCOUNT = "/abha/api/v3/profile/account"
PHR_PROFILE = "/abha/api/v3/phr/web/login/profile"


def _x(token):
    return {"X-token": bearer(token)}


def get_account_profile(x_token):
    return abdm_request("GET", ACCOUNT, headers=_x(x_token), endpoint_name="profile/account")


def get_phr_profile(x_token):
    return abdm_request("GET", f"{PHR_PROFILE}/abha-profile", headers=_x(x_token),
                        endpoint_name="phr/web/login/profile/abha-profile")


def get_abha_card(x_token):
    headers = {**_x(x_token), "Accept": "*/*"}
    return abdm_request("GET", f"{ACCOUNT}/abha-card", headers=headers, raw=True, endpoint_name="profile/account/abha-card")


def get_phr_card(x_token):
    headers = {**_x(x_token), "Accept": "*/*"}
    return abdm_request("GET", f"{PHR_PROFILE}/abha/phr-card", headers=headers, raw=True,
                        endpoint_name="phr/web/login/profile/abha/phr-card")


def get_qr_code(x_token):
    headers = {**_x(x_token), "Accept": "*/*"}
    return abdm_request("GET", f"{ACCOUNT}/qrCode", headers=headers, raw=True, endpoint_name="profile/account/qrCode")


# Changing the mobile / verifying the email of an existing ABHA: OTP to the new value, then verify.
# Both need the ABHA user token; the scope names what is being verified.
PROFILE_SCOPES = {"mobile": ["abha-profile", "mobile-verify"], "email": ["abha-profile", "email-verify"]}


def request_profile_otp(x_token, kind, enc_value):
    return abdm_request("POST", f"{ACCOUNT}/request/otp", json={
        "scope": PROFILE_SCOPES[kind],
        "loginHint": kind,
        "loginId": enc_value,
        "otpSystem": "abdm",
    }, headers=_x(x_token), endpoint_name=f"profile/account/request/otp[{kind}]")


def verify_profile_otp(x_token, kind, txn_id, enc_otp):
    return abdm_request("POST", f"{ACCOUNT}/verify", json={
        "scope": PROFILE_SCOPES[kind],
        "authData": {"authMethods": ["otp"], "otp": {"txnId": txn_id, "otpValue": enc_otp}},
    }, headers=_x(x_token), endpoint_name=f"profile/account/verify[{kind}]")
