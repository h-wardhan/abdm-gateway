"""ABHA creation via Aadhaar OTP (M1 items #1-#7)."""
from src.app.abdm import enrollment as abdm
from src.app.abdm.crypto import encrypt
from src.app.common import validators as v
from src.app.mappers import abha as mapper
from src.app.services import txn_service

FLOW = "enrol_aadhaar"


def send_aadhaar_otp(data):
    aadhaar = v.aadhaar(v.pick(data, "aadhaarNumber", "aadharNumber", "aadhaar"))
    result = abdm.request_aadhaar_otp(encrypt(aadhaar))
    txn_service.record_from(result, FLOW, "aadhaar_otp_sent")
    return {"txnId": result.get("txnId"), "message": result.get("message")}


def verify_aadhaar_otp(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    otp = v.otp(v.pick(data, "otpValue", "otp", "OTP"))
    mobile = v.mobile(v.pick(data, "mobilenumber", "mobileNumber", "mobile"))
    txn_service.assert_owner(txn_id)

    result = abdm.enrol_by_aadhaar(txn_id, encrypt(otp), mobile)
    mapped = mapper.enrolment_result(result)
    mapped["txnId"] = mapped["txnId"] or txn_id
    txn_service.record(mapped["txnId"], FLOW, "enrolled")
    return mapped


def send_mobile_update_otp(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    mobile = v.mobile(v.pick(data, "mobilenumber", "mobileNumber", "mobile"))
    txn_service.assert_owner(txn_id)

    result = abdm.request_mobile_update_otp(txn_id, encrypt(mobile))
    txn_service.record_from(result, FLOW, "mobile_otp_sent")
    return {"txnId": result.get("txnId") or txn_id, "message": result.get("message")}


def verify_mobile_update_otp(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    otp = v.otp(v.pick(data, "otp", "otpValue", "OTP"))
    txn_service.assert_owner(txn_id)

    result = abdm.verify_mobile_update_otp(txn_id, encrypt(otp))
    txn_service.record_from(result, FLOW, "mobile_verified")
    return result


def address_suggestions(txn_id):
    txn_id = v.txn_id(txn_id)
    txn_service.assert_owner(txn_id)
    result = abdm.address_suggestions(txn_id)
    return {
        "txnId": result.get("txnId") or txn_id,
        "abhaAddressList": result.get("abhaAddressList") or [],
    }


def create_abha_address(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    address = v.abha_address(v.pick(data, "abhaAddress"))
    preferred = v.pick(data, "preferred")
    txn_service.assert_owner(txn_id)

    result = abdm.create_abha_address(txn_id, address, int(preferred) if preferred not in (None, "") else 1)
    txn_service.record(txn_id, FLOW, "address_created", status="completed")
    return result


def send_email_verification_link(data):
    token = v.x_token(data)
    email = v.email(v.pick(data, "email"))
    result = abdm.request_email_verification_link(token, encrypt(email))
    return {"txnId": result.get("txnId"), "message": result.get("message")}
