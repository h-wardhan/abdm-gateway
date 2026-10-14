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
