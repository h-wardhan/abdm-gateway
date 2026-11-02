"""ABHA creation via Aadhaar OTP (M1 items #1-#7)."""
from src.app.abdm import enrollment as abdm
from src.app.abdm.crypto import encrypt
from src.app.common import validators as v
from src.app.errors import ValidationError
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


# ---- Driving licence enrolment -------------------------------------------------------------------------------
# Flow: dl/otp -> dl/verify -> dl/create (a txnId from dl/verify is used for dl/create)

DL_REQUIRED = ("documentId", "firstName", "dob", "gender", "frontSidePhoto", "backSidePhoto",
               "address", "state", "district", "pinCode")
DL_OPTIONAL = ("middleName", "lastName")


def send_dl_otp(data):
    mobile = v.mobile(v.pick(data, "mobilenumber", "mobileNumber", "mobile"))
    result = abdm.request_dl_otp(encrypt(mobile))
    txn_service.record_from(result, "enrol_dl", "otp_sent")
    return {"txnId": result.get("txnId"), "message": result.get("message")}


def verify_dl_otp(data):
    from src.app.abdm.http import abdm_timestamp

    txn_id = v.txn_id(v.pick(data, "txnId"))
    otp = v.otp(v.pick(data, "otp", "otpValue", "OTP"))
    txn_service.assert_owner(txn_id)
    result = abdm.verify_dl_otp(txn_id, encrypt(otp), abdm_timestamp())
    txn_service.record_from(result, "enrol_dl", "mobile_verified")
    return result


def create_with_dl(data):
    txn_id = v.txn_id(v.pick(data, "txnId"))
    txn_service.assert_owner(txn_id)
    fields = {"txnId": txn_id, "documentType": "DRIVING_LICENCE"}
    for key in DL_REQUIRED:
        fields[key] = v.require(v.pick(data, key), key)
    for key in DL_OPTIONAL:
        fields[key] = v.pick(data, key) or ""
    result = abdm.enrol_by_document(fields)
    txn_service.record(txn_id, "enrol_dl", "enrolled", status="completed")
    return result


# ---- Biometric / demographic / child enrolment (same ABDM endpoint as Aadhaar OTP) ---------------------------
# method (ours) -> (ABDM authMethod, key that carries the device PID)
BIO_METHODS = {
    "fingerprint": ("bio", "fingerPrintAuthPid"),
    "bio": ("bio", "fingerPrintAuthPid"),
    "face": ("face", "rdPidData"),
    "iris": ("iris", "Pid"),
}


def enrol_by_biometric(method, data):
    if method not in BIO_METHODS:
        raise ValidationError("method must be fingerprint, face or iris", code="INVALID_AUTH_METHOD")
    auth_method, pid_key = BIO_METHODS[method]
    aadhaar = v.aadhaar(v.pick(data, "aadhaarNumber", "aadharNumber", "aadhaar"))
    pid = v.require(v.pick(data, "pid", "pidData"), "pid")
    mobile = v.mobile(v.pick(data, "mobilenumber", "mobileNumber", "mobile"))
    auth = {"authMethods": [auth_method],
            auth_method: {"aadhaar": encrypt(aadhaar), pid_key: pid, "mobile": mobile}}
    result = mapper.enrolment_result(abdm.enrol_by_auth_data(auth, auth_method))
    txn_service.record(result["txnId"], f"enrol_{auth_method}", "enrolled")
    return result


DEMO_FIELDS = ("districtCode", "stateCode", "dateOfBirth", "gender", "name", "address", "pincode")


def enrol_by_demographics(data):
    aadhaar = v.aadhaar(v.pick(data, "aadhaarNumber", "aadharNumber", "aadhaar"))
    demo = {"aadhaarNumber": encrypt(aadhaar), "mobile": v.mobile(v.pick(data, "mobilenumber", "mobileNumber", "mobile"))}
    for key in DEMO_FIELDS:
        demo[key] = v.require(v.pick(data, key), key)
    demo["profilePhoto"] = v.pick(data, "profilePhoto") or ""
    result = mapper.enrolment_result(abdm.enrol_by_auth_data({"authMethods": ["demo_auth"], "demo_auth": demo}, "demo_auth"))
    txn_service.record(result["txnId"], "enrol_demo_auth", "enrolled")
    return result


def create_child_abha(data):
    token = v.x_token(data)
    child = {
        "dayOfBirth": str(v.require(v.pick(data, "dayOfBirth"), "dayOfBirth")),
        "monthOfBirth": str(v.require(v.pick(data, "monthOfBirth"), "monthOfBirth")),
        "yearOfBirth": str(v.require(v.pick(data, "yearOfBirth"), "yearOfBirth")),
        "gender": v.require(v.pick(data, "gender"), "gender"),
        "name": v.require(v.pick(data, "name"), "name"),
        "password": "",
        "profilePhoto": v.pick(data, "profilePhoto") or "",
        "parentConsent": "true" if data.get("parentConsent", True) in (True, "true", "True", 1, "1") else "false",
    }
    if child["parentConsent"] != "true":
        raise ValidationError("Parent consent is required to create a child ABHA", details={"field": "parentConsent"})
    if v.pick(data, "password"):
        child["password"] = encrypt(v.pick(data, "password"))
    return abdm.enrol_child(token, child)


def list_children(data):
    return abdm.list_children(v.x_token(data))
