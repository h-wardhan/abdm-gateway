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
