from flask import request
from flask_restx import Namespace

from src.app.api.v1 import schemas as s
from src.app.common.response import from_abdm
from src.app.common.validators import body
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import login_service as svc

ns = Namespace("ABHA Login", path="/api/v1/abha/login", description=(
    "Verify an existing ABHA with OTP (M1). Every verify returns the ABHA user token "
    "(`token`, or `tokens.token` for ABHA address); pass it as `xToken` to the profile/card/QR endpoints. "
    "Wrong OTP: HTTP 200, `success:false`, `data.authResult:\"failed\"`."))
s.register(ns)

OTP_DOC = {"description": "Returns `{txnId, message}`."}


# ---- mobile --------------------------------------------------------------------------

@ns.route("/mobile/otp")
class MobileOtp(TenantResource):
    @ns.doc(description="Send OTP for the ABHA picked from search/mobile (`txnId` + `index`).")
    @ns.expect(s.MOBILE_INDEX, validate=False)
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self):
        return from_abdm(svc.send_mobile_login_otp(body()))


@ns.route("/mobile/verify")
class MobileVerify(TenantResource):
    @ns.doc(description="Verify mobile OTP. Returns `{authResult, message, token, expiresIn, refreshToken, "
                        "refreshExpiresIn, accounts[]}`.")
    @ns.expect(s.TXN_OTP, validate=False)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.verify_mobile_login_otp(body()))


@ns.route("/mobile/select-account")
class MobileSelectAccount(TenantResource):
    @ns.doc(description="Only when verify returned several `accounts` and a transfer token: pick one. "
                        "Send the verify `token` in the `T-Token` header.",
            params={"T-Token": {"in": "header", "required": True, "description": "token from mobile/verify"}})
    @ns.expect(s.SELECT_ACCOUNT, validate=False)
    @ns.response(200, "Account token", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.select_mobile_login_account(body(), request.headers.get("T-Token")))


# ---- ABHA number ---------------------------------------------------------------------

@ns.route("/abha-number/<string:method>/otp")
@ns.doc(params={"method": {"description": "mobile | aadhaar", "enum": ["mobile", "aadhaar"]}})
class AbhaNumberOtp(TenantResource):
    @ns.doc(**OTP_DOC)
    @ns.expect(s.ABHA_NUMBER, validate=False)
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self, method):
        return from_abdm(svc.send_abha_number_otp(body(), method))


@ns.route("/abha-number/<string:method>/verify")
@ns.doc(params={"method": {"description": "mobile | aadhaar (same as the OTP call)", "enum": ["mobile", "aadhaar"]}})
class AbhaNumberVerify(TenantResource):
    @ns.doc(description="Verify OTP. Returns `{authResult, message, token, expiresIn, refreshToken, refreshExpiresIn, accounts[]}`.")
    @ns.expect(s.TXN_OTP, validate=False)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self, method):
        return from_abdm(svc.verify_abha_number_otp(body(), method))


# ---- Aadhaar -------------------------------------------------------------------------

@ns.route("/aadhaar/otp")
class AadhaarOtp(TenantResource):
    @ns.doc(**OTP_DOC)
    @ns.expect(s.AADHAAR, validate=False)
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self):
        return from_abdm(svc.send_aadhaar_login_otp(body()))


@ns.route("/aadhaar/verify")
class AadhaarVerify(TenantResource):
    @ns.doc(description="Verify Aadhaar OTP. Returns `{authResult, message, token, expiresIn, refreshToken, refreshExpiresIn, accounts[]}`.")
    @ns.expect(s.TXN_OTP, validate=False)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.verify_aadhaar_login_otp(body()))


# ---- ABHA address --------------------------------------------------------------------

@ns.route("/abha-address/otp")
class AbhaAddressOtp(TenantResource):
    @ns.doc(**OTP_DOC)
    @ns.expect(s.ABHA_ADDRESS_OTP, validate=False)
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self):
        return from_abdm(svc.send_abha_address_otp(body()))


@ns.route("/abha-address/verify")
class AbhaAddressVerify(TenantResource):
    @ns.doc(description="Verify OTP. Returns `{authResult, message, users[], tokens{token, expiresIn, refreshToken, refreshExpiresIn}}`.")
    @ns.expect(s.ABHA_ADDRESS_VERIFY, validate=False)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.verify_abha_address_otp(body()))


# ---- forgot ABHA / retrieval of the enrolment number ---------------------------------------------------------

@ns.route("/forgot/<string:method>/otp")
@ns.doc(params={"method": {"description": "mobile | aadhaar", "enum": ["mobile", "aadhaar"]}})
class ForgotOtp(TenantResource):
    @ns.doc(description="Forgot ABHA: send an OTP to find the ABHA. Body: `mobile` or `aadhaarNumber`.")
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self, method):
        return from_abdm(svc.send_forgot_otp(body(), method))


@ns.route("/forgot/<string:method>/verify")
@ns.doc(params={"method": {"description": "mobile | aadhaar (same as the OTP call)", "enum": ["mobile", "aadhaar"]}})
class ForgotVerify(TenantResource):
    @ns.doc(description="Verify the OTP. Returns the login result with the ABHA accounts found.")
    @ns.expect(s.TXN_OTP, validate=False)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self, method):
        return from_abdm(svc.verify_forgot_otp(body(), method))


@ns.route("/retrieval/otp")
class RetrievalOtp(TenantResource):
    @ns.doc(description="Retrieve a lost enrolment number: OTP to the mobile. Body: `mobile`.")
    @ns.expect(s.MOBILE, validate=False)
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self):
        return from_abdm(svc.send_retrieval_otp(body()))


@ns.route("/retrieval/verify")
class RetrievalVerify(TenantResource):
    @ns.doc(description="Verify the OTP. Returns the enrolment number(s) ABDM holds for this mobile.")
    @ns.expect(s.TXN_OTP, validate=False)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.verify_retrieval_otp(body()))


# ---- biometric login (fingerprint / face / iris) -------------------------------------------------------------
_BIO = {"method": {"description": "fingerprint | face | iris", "enum": ["fingerprint", "face", "iris"]}}
_PID_DOC = "`pid` is the device PID data, passed on unchanged."


@ns.route("/abha-number/biometric/<string:method>/otp")
@ns.doc(params=_BIO)
class BiometricOtp(TenantResource):
    @ns.doc(description="Start a biometric login for an ABHA number. Body: `abhaNumber`. Returns `txnId`.")
    @ns.response(200, "Challenge", s.TXN_MESSAGE_RESPONSE)
    def post(self, method):
        return from_abdm(svc.send_biometric_otp(body(), method))


@ns.route("/mobile/biometric/<string:method>/otp")
@ns.doc(params=_BIO)
class MobileBiometricOtp(TenantResource):
    @ns.doc(description="Start a biometric login for the ABHA picked from search/mobile. Body: `txnId`, `index`.")
    @ns.response(200, "Challenge", s.TXN_MESSAGE_RESPONSE)
    def post(self, method):
        return from_abdm(svc.send_mobile_biometric_otp(body(), method))


@ns.route("/abha-number/biometric/<string:method>/verify", "/mobile/biometric/<string:method>/verify")
@ns.doc(params=_BIO)
class BiometricVerify(TenantResource):
    @ns.doc(description="Finish the biometric login (ABHA number, or the mobile-search flow). Body: `txnId`, `pid`. "
                        + _PID_DOC)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self, method):
        return from_abdm(svc.verify_biometric(body(), method))


@ns.route("/abha-address/biometric/<string:method>/otp")
@ns.doc(params=_BIO)
class AddressBiometricOtp(TenantResource):
    @ns.doc(description="Start a biometric login for an ABHA address. Body: `abhaAddress`.")
    @ns.response(200, "Challenge", s.TXN_MESSAGE_RESPONSE)
    def post(self, method):
        return from_abdm(svc.send_address_biometric_otp(body(), method))


@ns.route("/abha-address/biometric/<string:method>/verify")
@ns.doc(params=_BIO)
class AddressBiometricVerify(TenantResource):
    @ns.doc(description="Finish the biometric ABHA address login. Body: `txnId`, `pid`. " + _PID_DOC)
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self, method):
        return from_abdm(svc.verify_address_biometric(body(), method))
