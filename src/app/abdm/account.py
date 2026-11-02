"""ABDM v3 account lifecycle: delete / deactivate / re-KYC / password / profile PATCH / logout / token refresh,
plus reactivate (a login-side call). Bodies follow the ABDM Milestone-1 Postman collection."""
from src.app.abdm.http import abdm_request, bearer

ACCOUNT = "/abha/api/v3/profile/account"
PROFILE = "/abha/api/v3/profile"


def _x(token):
    return {"X-token": bearer(token)}


def account_otp(x_token, scope, login_hint, enc_login_id, otp_system, name):
    return abdm_request("POST", f"{ACCOUNT}/request/otp", json={
        "scope": scope,
        "loginHint": login_hint,
        "loginId": enc_login_id,
        "otpSystem": otp_system,
    }, headers=_x(x_token), endpoint_name=f"profile/account/request/otp[{name}]")


def account_verify(x_token, scope, auth_data, name, reasons=None):
    payload = {"scope": scope, "authData": auth_data}
    if reasons:
        payload["reasons"] = reasons
    return abdm_request("POST", f"{ACCOUNT}/verify", json=payload, headers=_x(x_token),
                        endpoint_name=f"profile/account/verify[{name}]")


def login_verify(scope, auth_data, name):
    """profile/login/verify with any auth method (reactivate by password, biometric login)."""
    return abdm_request("POST", f"{PROFILE}/login/verify", json={"scope": scope, "authData": auth_data},
                        endpoint_name=f"profile/login/verify[{name}]")


def login_search(abha_number):
    """Password login, step 1: ABDM answers with the auth methods / txnId for this ABHA number."""
    return abdm_request("POST", f"{PROFILE}/login/search", json={"ABHANumber": abha_number},
                        endpoint_name="profile/login/search")


def patch_profile(fields, x_token=None):
    headers = _x(x_token) if x_token else None
    return abdm_request("PATCH", ACCOUNT, json=fields, headers=headers, endpoint_name="profile/account[PATCH]")


def logout(x_token):
    return abdm_request("GET", f"{ACCOUNT}/request/logout", headers=_x(x_token),
                        endpoint_name="profile/account/request/logout")


def refresh_token(r_token):
    return abdm_request("GET", f"{ACCOUNT}/request/token", headers={"R-token": bearer(r_token)},
                        endpoint_name="profile/account/request/token")
