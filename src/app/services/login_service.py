"""ABHA search + login (M1 items #8-#20)."""
from src.app.abdm import login as abdm
from src.app.abdm.crypto import encrypt
from src.app.common import validators as v
from src.app.errors import ValidationError
from src.app.services import txn_service

MOBILE_SCOPE = ["abha-login", "mobile-verify"]
AADHAAR_SCOPE = ["abha-login", "aadhaar-verify"]
# authMethod in the URL → (verify scope, otpSystem)
ABHA_NUMBER_METHODS = {
    "mobile": (MOBILE_SCOPE, "abdm"),
    "aadhaar": (AADHAAR_SCOPE, "aadhaar"),
}
# ABHA address login: "mobile-verify" / "aadhaar-verify" (i-medicus) or "mobile" / "aadhaar"
ADDRESS_METHODS = {
    "mobile-verify": ("mobile-verify", "abdm"),
    "mobile": ("mobile-verify", "abdm"),
    "mobile_otp": ("mobile-verify", "abdm"),
    "aadhaar-verify": ("aadhaar-verify", "aadhaar"),
    "aadhaar": ("aadhaar-verify", "aadhaar"),
    "aadhaar_otp": ("aadhaar-verify", "aadhaar"),
}


def _otp_and_txn(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    otp = v.otp(v.pick(data, "otp", "otpValue", "OTP"))
    txn_service.assert_owner(txn_id)
    return txn_id, otp


# ---- by mobile -----------------------------------------------------------------------

def search_by_mobile(data):
    mobile = v.mobile(v.pick(data, "mobileNumber", "mobilenumber", "mobile"))
    result = abdm.search_by_mobile(encrypt(mobile))
    txn_service.record_from(result, "login_mobile", "searched")
    return result if isinstance(result, list) else [result]


def send_mobile_login_otp(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    index = v.require(v.pick(data, "index"), "index")
    txn_service.assert_owner(txn_id)
    result = abdm.request_login_otp(
        ["abha-login", "search-abha", "mobile-verify"], "index", encrypt(str(index)), "abdm", txn_id=txn_id
    )
    txn_service.record_from(result, "login_mobile", "otp_sent")
    return result


def verify_mobile_login_otp(data):
    txn_id, otp = _otp_and_txn(data)
    result = abdm.verify_login_otp(MOBILE_SCOPE, txn_id, encrypt(otp))
    txn_service.record_from(result, "login_mobile", "verified")
    return result


def select_mobile_login_account(data, t_token):
    """Several ABHAs on one mobile: choose one after OTP verify (T-token from the verify response)."""
    txn_id = v.txn_id(v.pick(data, "txnId"))
    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    txn_service.assert_owner(txn_id)
    return abdm.verify_login_user(v.require(t_token, "T-token"), abha_number, txn_id)


# ---- by ABHA number ------------------------------------------------------------------

def search_by_abha_number(data):
    """Validate an ABHA number and return the OTP methods it supports.

    ABDM v3 has no plain lookup by ABHA number; an unknown number is reported by ABDM
    when the OTP is requested.
    """
    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    return {"abhaNumber": abha_number, "authMethods": ["MOBILE_OTP", "AADHAAR_OTP"]}


def _abha_number_method(method):
    if method not in ABHA_NUMBER_METHODS:
        raise ValidationError("authMethod must be 'mobile' or 'aadhaar'", code="INVALID_AUTH_METHOD")
    return ABHA_NUMBER_METHODS[method]


def send_abha_number_otp(data, method):
    scope, otp_system = _abha_number_method(method)
    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    result = abdm.request_login_otp(scope, "abha-number", encrypt(abha_number), otp_system)
    txn_service.record_from(result, f"login_abha_number_{method}", "otp_sent")
    return result


def verify_abha_number_otp(data, method):
    scope, _ = _abha_number_method(method)
    txn_id, otp = _otp_and_txn(data)
    result = abdm.verify_login_otp(scope, txn_id, encrypt(otp))
    txn_service.record_from(result, f"login_abha_number_{method}", "verified")
    return result


# ---- by Aadhaar ----------------------------------------------------------------------

def send_aadhaar_login_otp(data):
    aadhaar = v.aadhaar(v.pick(data, "aadhaarNumber", "aadharNumber", "aadhaar"))
    result = abdm.request_login_otp(AADHAAR_SCOPE, "aadhaar", encrypt(aadhaar), "aadhaar")
    txn_service.record_from(result, "login_aadhaar", "otp_sent")
    return result


def verify_aadhaar_login_otp(data):
    txn_id, otp = _otp_and_txn(data)
    result = abdm.verify_login_otp(AADHAAR_SCOPE, txn_id, encrypt(otp))
    txn_service.record_from(result, "login_aadhaar", "verified")
    return result


# ---- by ABHA address -----------------------------------------------------------------

def search_by_abha_address(data):
    address = v.abha_address(v.pick(data, "abhaAddress", "abha_address"))
    return abdm.search_abha_address(address)


def _address_method(data):
    raw = v.pick(data, "authMethod", "scope", "scop", "authType")
    if isinstance(raw, list):
        raw = next((s for s in raw if s != "abha-address-login"), None)
    key = str(raw or "").strip().lower()
    if key not in ADDRESS_METHODS:
        raise ValidationError("authMethod/scope must be 'mobile-verify' or 'aadhaar-verify'",
                              code="INVALID_AUTH_METHOD")
    return ADDRESS_METHODS[key]


def send_abha_address_otp(data):
    address = v.abha_address(v.pick(data, "abhaAddress", "abha_address"))
    verify_scope, otp_system = _address_method(data)
    result = abdm.request_abha_address_otp(["abha-address-login", verify_scope], encrypt(address), otp_system)
    txn_service.record_from(result, "login_abha_address", "otp_sent")
    return result


def verify_abha_address_otp(data):
    verify_scope, _ = _address_method(data)
    txn_id, otp = _otp_and_txn(data)
    result = abdm.verify_abha_address_otp(["abha-address-login", verify_scope], txn_id, encrypt(otp))
    txn_service.record_from(result, "login_abha_address", "verified")
    return result


# ---- forgot ABHA / retrieval of the enrolment number ---------------------------------------------------------
# kind -> (login hint, scope, otpSystem); the verify call uses the same scope
FORGOT = {
    "mobile": ("mobile", ["abha-login", "mobile-verify"], "abdm"),
    "aadhaar": ("aadhaar", ["abha-login", "aadhaar-verify"], "aadhaar"),
}
RETRIEVAL = ("mobile", ["abha-login", "mobile-verify", "retrieval"], "abdm")


def _forgot_kind(method):
    if method not in FORGOT:
        raise ValidationError("method must be 'mobile' or 'aadhaar'", code="INVALID_AUTH_METHOD")
    return FORGOT[method]


def _identifier(hint, data):
    if hint == "aadhaar":
        return v.aadhaar(v.pick(data, "aadhaarNumber", "aadharNumber", "aadhaar"))
    return v.mobile(v.pick(data, "mobileNumber", "mobilenumber", "mobile"))


def _send_hint_otp(flow, spec, data):
    hint, scope, otp_system = spec
    result = abdm.request_login_otp(scope, hint, encrypt(_identifier(hint, data)), otp_system)
    txn_service.record_from(result, flow, "otp_sent")
    return result


def _verify_hint_otp(flow, scope, data):
    txn_id, otp = _otp_and_txn(data)
    result = abdm.verify_login_otp(scope, txn_id, encrypt(otp))
    txn_service.record_from(result, flow, "verified")
    return result


def send_forgot_otp(data, method):
    return _send_hint_otp(f"forgot_{method}", _forgot_kind(method), data)


def verify_forgot_otp(data, method):
    return _verify_hint_otp(f"forgot_{method}", _forgot_kind(method)[1], data)


def send_retrieval_otp(data):
    return _send_hint_otp("retrieval", RETRIEVAL, data)


def verify_retrieval_otp(data):
    return _verify_hint_otp("retrieval", RETRIEVAL[1], data)


# ---- biometric login (fingerprint / face / iris) -------------------------------------------------------------
# method -> (verify scope token, ABDM authMethod, key carrying the PID)
BIO_LOGIN = {
    "fingerprint": ("aadhaar-bio-verify", "bio", "fingerPrintAuthPid"),
    "face": ("aadhaar-face-verify", "face", "faceAuthPid"),
    "iris": ("aadhaar-iris-verify", "iris", "irisAuthPid"),
}
PROFILE_LOGIN = "/abha/api/v3/profile/login"


def _bio(method):
    if method not in BIO_LOGIN:
        raise ValidationError("method must be fingerprint, face or iris", code="INVALID_AUTH_METHOD")
    return BIO_LOGIN[method]


def send_biometric_otp(data, method):
    """Start a biometric login for an ABHA number: ABDM returns a txnId to attach to the device PID."""
    token, _, _ = _bio(method)
    abha_number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    result = abdm.request_login_otp_for(PROFILE_LOGIN, ["abha-login", token], "abha-number", encrypt(abha_number),
                                        "aadhaar", f"profile/login/request/otp[{method}]")
    txn_service.record_from(result, f"login_{method}", "challenge_sent")
    return result


def send_mobile_biometric_otp(data, method):
    """Biometric login for an ABHA picked from search/mobile (txnId + index)."""
    token, _, _ = _bio(method)
    txn_id = v.txn_id(v.pick(data, "txnId"))
    index = v.require(v.pick(data, "index"), "index")
    txn_service.assert_owner(txn_id)
    result = abdm.request_login_otp_for(PROFILE_LOGIN, ["abha-login", "search-abha", token], "index",
                                        encrypt(str(index)), "aadhaar", f"profile/login/request/otp[{method}-index]",
                                        txn_id=txn_id)
    txn_service.record_from(result, f"login_{method}", "challenge_sent")
    return result


def send_address_biometric_otp(data, method):
    token, _, _ = _bio(method)
    address = v.abha_address(v.pick(data, "abhaAddress", "abha_address"))
    result = abdm.request_login_otp_for(abdm.PHR, ["abha-login", token], "abha-address", encrypt(address),
                                        "aadhaar", f"phr/web/login/abha/request/otp[{method}]")
    txn_service.record_from(result, f"login_{method}_address", "challenge_sent")
    return result


def _biometric_auth(data, method):
    token, auth_method, pid_key = _bio(method)
    txn_id = v.txn_id(v.pick(data, "txnId"))
    pid = v.require(v.pick(data, "pid", "pidData"), "pid")
    txn_service.assert_owner(txn_id)
    return ["abha-login", token], {"authMethods": [auth_method], auth_method: {"txnId": txn_id, pid_key: pid}}


def verify_biometric(data, method):
    scope, auth = _biometric_auth(data, method)
    result = abdm.verify_login_with(PROFILE_LOGIN, scope, auth, f"profile/login/verify[{method}]")
    txn_service.record_from(result, f"login_{method}", "verified")
    return result


def verify_address_biometric(data, method):
    scope, auth = _biometric_auth(data, method)
    result = abdm.verify_login_with(abdm.PHR, scope, auth, f"phr/web/login/abha/verify[{method}]")
    txn_service.record_from(result, f"login_{method}_address", "verified")
    return result
