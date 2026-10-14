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
