"""
The calls of i-medicus' older mobile-app service (MobileAbdmService), served by the same ABDM logic as /api/v1/abha/*.

That service reads ABDM's answer as-is, at the top level (`txnId`, `accounts`, `token`, `ABHAProfile`, ...), not inside
the gateway's `data` envelope, and some of its calls expect a single step where ABDM v3 needs two. This module returns
what it reads; the validation, txn ownership checks and ABDM calls are the ones the normal endpoints use.
"""
from src.app.abdm import enrollment as enrol_abdm
from src.app.abdm.crypto import encrypt
from src.app.common import validators as v
from src.app.errors import ValidationError
from src.app.services import enrolment_service, login_service, txn_service


def aadhaar_verify(data):
    """Verify the Aadhaar OTP and create (or fetch) the ABHA. ABDM's answer: txnId, tokens{}, ABHAProfile{}, isNew."""
    txn_id = v.txn_id(v.pick(data, "txnId"))
    otp = v.otp(v.pick(data, "otpValue", "otp", "OTP"))
    mobile = v.mobile(v.pick(data, "mobilenumber", "mobileNumber", "mobile"))
    txn_service.assert_owner(txn_id)

    result = enrol_abdm.enrol_by_aadhaar(txn_id, encrypt(otp), mobile)
    result["txnId"] = result.get("txnId") or txn_id
    txn_service.record(result["txnId"], enrolment_service.FLOW, "enrolled")
    return result


def mobile_login_otp(data):
    """Search the ABHA on a mobile and send the login OTP in one step (ABDM v3 needs search, then OTP).

    The first ABHA on the mobile is used, which is what the single-step call always did.
    """
    found = login_service.search_by_mobile(data)
    first = found[0] if found else {}
    txn_id = first.get("txnId")
    if not txn_id:
        raise ValidationError("No ABHA found for this mobile number", code="ABHA_NOT_FOUND")
    result = login_service.send_mobile_login_otp({"txnId": txn_id, "index": 1})
    result["txnId"] = result.get("txnId") or txn_id
    return result


def succeeded(result):
    """True unless ABDM says the OTP was wrong (HTTP 200 with authResult 'failed')."""
    return str((result or {}).get("authResult", "")).lower() != "failed"
