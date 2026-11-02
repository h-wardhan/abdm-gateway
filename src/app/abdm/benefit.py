"""ABDM v3 benefit APIs (link / de-link / search). Values passed in are already RSA-encrypted."""
from src.app.abdm.http import abdm_request, bearer

BENEFIT = "/abha/api/v3/profile/benefit"


def _benefit(name, headers=None):
    """Every benefit call names the benefit programme in the BENEFIT_NAME header."""
    return {"BENEFIT_NAME": name, **(headers or {})}


def search(benefit_name, login_hint, enc_login_id):
    return abdm_request("POST", f"{BENEFIT}/search", json={
        "scope": ["search"], "loginHint": login_hint, "loginId": enc_login_id,
    }, headers=_benefit(benefit_name), endpoint_name="profile/benefit/search")


def link_and_delink(benefit_name, action, login_hint=None, enc_login_id=None, x_token=None):
    """action: "link" or "de-link". With an ABHA user token only the scope is sent."""
    payload = {"scope": [action]}
    if login_hint:
        payload.update({"loginHint": login_hint, "loginId": enc_login_id})
    headers = _benefit(benefit_name, {"X-token": bearer(x_token)} if x_token else None)
    return abdm_request("POST", f"{BENEFIT}/linkAndDelink", json=payload, headers=headers,
                        endpoint_name=f"profile/benefit/linkAndDelink[{action}]")


def abha_by_number(abha_number, benefit_name=None):
    headers = _benefit(benefit_name) if benefit_name else None
    return abdm_request("GET", f"{BENEFIT}/abha/{abha_number}", headers=headers, endpoint_name="profile/benefit/abha")
