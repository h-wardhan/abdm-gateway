"""Swagger models shared by the ABHA namespaces. Docs only: validation happens in the services."""
from flask_restx import Model, fields

ERROR_RESPONSE = Model("ErrorResponse", {
    "success": fields.Boolean(example=False),
    "code": fields.String(example="error"),
    "message": fields.Raw(example={"error": {"code": "ABDM-1204", "message": "UIDAI Error code : 111 : ..."}}),
    "requestId": fields.String,
})

GENERIC_RESPONSE = Model("AbdmResponse", {
    "success": fields.Boolean(example=True),
    "data": fields.Raw(description="ABDM result (see each endpoint)"),
    "message": fields.String(description="ABDM message, if any"),
    "requestId": fields.String,
})

TXN_MESSAGE_RESPONSE = Model("TxnMessageResponse", {
    "success": fields.Boolean(example=True),
    "data": fields.Raw(example={"txnId": "a6f2...-uuid", "message": "OTP sent to Aadhaar registered mobile number ending with ******1234"}),
    "message": fields.String,
    "requestId": fields.String,
})

AADHAAR = Model("AadhaarRequest", {
    "aadhaarNumber": fields.String(required=True, example="123412341234", description="12 digits, Verhoeff-checked"),
})

ENROL_VERIFY = Model("EnrolVerifyRequest", {
    "txnId": fields.String(required=True),
    "otpValue": fields.String(required=True, example="123456", description="alias: otp"),
    "mobilenumber": fields.String(required=True, example="9876543210",
                                  description="Mobile to link to the ABHA (alias: mobileNumber)"),
})

TXN_MOBILE = Model("TxnMobileRequest", {
    "txnId": fields.String(required=True),
    "mobilenumber": fields.String(required=True, example="9876543210", description="alias: mobileNumber"),
})

TXN_OTP = Model("TxnOtpRequest", {
    "txnId": fields.String(required=True),
    "otp": fields.String(required=True, example="123456", description="aliases: otpValue, OTP"),
})

ABHA_ADDRESS_CREATE = Model("AbhaAddressCreateRequest", {
    "txnId": fields.String(required=True),
    "abhaAddress": fields.String(required=True, example="ramesh.kumar1990"),
    "preferred": fields.Integer(example=1),
})

EMAIL = Model("EmailVerificationRequest", {
    "email": fields.String(required=True, example="patient@example.com"),
    "xToken": fields.String(description="ABHA user token; or send the X-Token header"),
})

PROFILE_MOBILE_OTP = Model("ProfileMobileOtpRequest", {
    "mobile": fields.String(required=True, example="9876543210", description="New mobile (aliases: mobileNumber, mobilenumber)"),
    "xToken": fields.String(description="ABHA user token; or send the X-Token header"),
})

PROFILE_VERIFY = Model("ProfileVerifyRequest", {
    "txnId": fields.String(required=True, description="txnId from the matching .../otp call"),
    "otp": fields.String(required=True, example="123456", description="aliases: otpValue, OTP"),
    "xToken": fields.String(description="ABHA user token; or send the X-Token header"),
})

MOBILE = Model("MobileRequest", {
    "mobileNumber": fields.String(required=True, example="9876543210"),
})

MOBILE_INDEX = Model("MobileIndexRequest", {
    "txnId": fields.String(required=True, description="txnId from search/mobile"),
    "index": fields.Integer(required=True, example=1, description="ABHA[].index from search/mobile"),
})

SELECT_ACCOUNT = Model("SelectAccountRequest", {
    "txnId": fields.String(required=True),
    "abhaNumber": fields.String(required=True, example="91-1234-5678-9012"),
})

ABHA_NUMBER = Model("AbhaNumberRequest", {
    "abhaNumber": fields.String(required=True, example="91-1234-5678-9012", description="14 digits, hyphens optional"),
})

ABHA_ADDRESS = Model("AbhaAddressRequest", {
    "abhaAddress": fields.String(required=True, example="ramesh.kumar1990@sbx"),
})

ABHA_ADDRESS_OTP = Model("AbhaAddressOtpRequest", {
    "abhaAddress": fields.String(required=True, example="ramesh.kumar1990@sbx"),
    "scope": fields.String(required=True, example="mobile-verify",
                           description="mobile-verify | aadhaar-verify (aliases: authMethod, authType)"),
})

ABHA_ADDRESS_VERIFY = Model("AbhaAddressVerifyRequest", {
    "txnId": fields.String(required=True),
    "otp": fields.String(required=True, example="123456"),
    "scope": fields.String(required=True, example="mobile-verify", description="same value used for the OTP request"),
})

ALL = [
    ERROR_RESPONSE, GENERIC_RESPONSE, TXN_MESSAGE_RESPONSE, AADHAAR, ENROL_VERIFY, TXN_MOBILE, TXN_OTP,
    ABHA_ADDRESS_CREATE, EMAIL, PROFILE_MOBILE_OTP, PROFILE_VERIFY, MOBILE, MOBILE_INDEX, SELECT_ACCOUNT, ABHA_NUMBER, ABHA_ADDRESS,
    ABHA_ADDRESS_OTP, ABHA_ADDRESS_VERIFY,
]


def register(ns):
    for model in ALL:
        ns.add_model(model.name, model)
