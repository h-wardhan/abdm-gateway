"""ABHA account lifecycle (M1): delete, deactivate, re-activate, re-KYC, password, profile PATCH, logout, refresh."""
from src.app.abdm import account as abdm
from src.app.abdm.crypto import encrypt
from src.app.common import validators as v
from src.app.errors import ValidationError
from src.app.services import txn_service

FLOW = "account"

# Actions done with the ABHA user token on profile/account/{request/otp,verify}
ACCOUNT_SCOPES = {
    "delete": ["abha-profile", "delete"],
    "deactivate": ["abha-profile", "de-activate"],
    "re-kyc": ["abha-profile", "re-kyc"],
    "password": ["abha-profile", "change-password"],
}
# Actions that need a reason list
NEEDS_REASONS = ("delete", "deactivate")

# How the OTP is delivered: "aadhaar" = UIDAI OTP, "abha" = ABDM OTP to the ABHA mobile
OTP_SYSTEMS = {"aadhaar": "aadhaar", "abha": "abdm", "mobile": "abdm", "abdm": "abdm"}

# Reactivate goes through the login endpoints (no ABHA token: the account is inactive)
REACTIVATE_SCOPES = {
    "aadhaar": ["abha-login", "aadhaar-verify", "re-activate"],
    "abha": ["abha-login", "mobile-verify", "re-activate"],
    "password": ["abha-login", "password-verify", "re-activate"],
}
PROFILE_FIELDS = ("profilePhoto", "abhaNumber", "mobile", "accountStatus", "name", "dob", "gender")


def _method(data, allowed):
    key = str(v.pick(data, "method", "authMethod") or "").strip().lower()
    if key not in allowed:
        raise ValidationError(f"method must be one of: {', '.join(allowed)}", code="INVALID_AUTH_METHOD",
                              details={"field": "method"})
    return key


def _otp_system(method):
    if method not in OTP_SYSTEMS:
        raise ValidationError("method must be 'aadhaar' or 'abha' for an OTP", code="INVALID_AUTH_METHOD",
                              details={"field": "method"})
    return OTP_SYSTEMS[method]


def _reasons(data):
    reasons = data.get("reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    reasons = [str(r).strip() for r in (reasons or []) if str(r).strip()]
    if not reasons:
        raise ValidationError("reasons is required (at least one)", details={"field": "reasons"})
    return reasons


def _password_auth(data):
    password = v.require(v.pick(data, "password"), "password")
    return {"authMethods": ["password"], "password": {"password": encrypt(password)}}


def _otp_auth(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    otp = v.otp(v.pick(data, "otp", "otpValue", "OTP"))
    txn_service.assert_owner(txn_id)
    return {"authMethods": ["otp"], "otp": {"txnId": txn_id, "otpValue": encrypt(otp)}}


# ---- delete / deactivate / re-KYC ----------------------------------------------------------------------------

def send_action_otp(action, data):
    token = v.x_token(data)
    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    method = str(v.pick(data, "method", "authMethod") or "aadhaar").lower()
    result = abdm.account_otp(token, ACCOUNT_SCOPES[action], "abha-number", encrypt(abha_number),
                              _otp_system(method), action)
    txn_service.record_from(result, f"{FLOW}_{action}", "otp_sent")
    return {"txnId": result.get("txnId"), "message": result.get("message")}


def verify_action(action, data):
    token = v.x_token(data)
    allowed = ("aadhaar", "abha", "mobile", "password") if action in NEEDS_REASONS else ("aadhaar", "abha", "mobile")
    method = _method(data, allowed)
    auth = _password_auth(data) if method == "password" else _otp_auth(data)
    reasons = _reasons(data) if action in NEEDS_REASONS else None
    result = abdm.account_verify(token, ACCOUNT_SCOPES[action], auth, action, reasons=reasons)
    txn_service.record_from(result, f"{FLOW}_{action}", "verified", status="completed")
    return result


# ---- reactivate ----------------------------------------------------------------------------------------------

def send_reactivate_otp(data):
    from src.app.abdm import login as login_abdm

    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    method = _method(data, ("aadhaar", "abha"))
    result = login_abdm.request_login_otp(REACTIVATE_SCOPES[method], "abha-number", encrypt(abha_number),
                                          _otp_system(method))
    txn_service.record_from(result, f"{FLOW}_reactivate", "otp_sent")
    return {"txnId": result.get("txnId"), "message": result.get("message")}


def verify_reactivate(data):
    method = _method(data, ("aadhaar", "abha", "password"))
    if method == "password":
        abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
        auth = {"authMethods": ["password"],
                "password": {"ABHANumber": abha_number, "password": _password_auth(data)["password"]["password"]}}
    else:
        auth = _otp_auth(data)
    result = abdm.login_verify(REACTIVATE_SCOPES[method], auth, "reactivate")
    txn_service.record_from(result, f"{FLOW}_reactivate", "verified", status="completed")
    return result


# ---- password ------------------------------------------------------------------------------------------------

def change_password(data):
    """Set or change the ABHA password with the ABHA user token (oldPassword when one exists)."""
    token = v.x_token(data)
    new = v.require(v.pick(data, "newPassword"), "newPassword")
    password = {"newPassword": encrypt(new)}
    old = v.pick(data, "oldPassword")
    if old:
        password["oldPassword"] = encrypt(old)
    confirm = v.pick(data, "confirmPassword")
    if confirm:
        password["confirmPassword"] = encrypt(confirm)
    return abdm.account_verify(token, ACCOUNT_SCOPES["password"],
                               {"authMethods": ["password"], "password": password}, "password")


def password_otp(data):
    """OTP to reset the password (Aadhaar OTP or ABDM OTP)."""
    token = v.x_token(data)
    method = str(v.pick(data, "method", "authMethod") or "abha").lower()
    password = v.require(v.pick(data, "password", "newPassword"), "password")
    result = abdm.account_otp(token, ACCOUNT_SCOPES["password"], "password", encrypt(password),
                              _otp_system(method), "password")
    txn_service.record_from(result, f"{FLOW}_password", "otp_sent")
    return {"txnId": result.get("txnId"), "message": result.get("message")}


def password_otp_verify(data):
    token = v.x_token(data)
    result = abdm.account_verify(token, ACCOUNT_SCOPES["password"], _otp_auth(data), "password[otp]")
    txn_service.record_from(result, f"{FLOW}_password", "verified", status="completed")
    return result


def login_search(data):
    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    result = abdm.login_search(abha_number)
    txn_service.record_from(result, "login_password", "searched")
    return result


def login_by_password(data):
    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    auth = {"authMethods": ["password"],
            "password": {"ABHANumber": abha_number, "password": _password_auth(data)["password"]["password"]}}
    return abdm.login_verify(["abha-login", "password-verify"], auth, "password")


# ---- profile PATCH / logout / refresh ------------------------------------------------------------------------

def update_profile(data):
    """PATCH profile/account. With the ABHA user token it edits that profile (e.g. profilePhoto); the benefit
    and child variants name the account with abhaNumber instead and send no token."""
    fields = {k: data[k] for k in PROFILE_FIELDS if data.get(k) not in (None,)}
    if not fields:
        raise ValidationError(f"Send at least one of: {', '.join(PROFILE_FIELDS)}", details={"field": "profile"})
    if "abhaNumber" in fields:
        fields["abhaNumber"] = v.abha_number(fields["abhaNumber"])
    if "mobile" in fields:
        fields["mobile"] = v.mobile(fields["mobile"])
    try:
        token = v.x_token(data)
    except ValidationError:
        if "abhaNumber" not in fields:
            raise  # nothing identifies the account
        token = None
    return abdm.patch_profile(fields, x_token=token)


def logout(data):
    return abdm.logout(v.x_token(data))


def refresh_token(r_token):
    token = (r_token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    v.require(token, "refreshToken")
    return abdm.refresh_token(token)
