from flask import request
from flask_restx import Namespace

from src.app.api.v1 import schemas as s
from src.app.common.response import from_abdm, success
from src.app.common.validators import body
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import enrolment_service as svc

ns = Namespace("ABHA Enrolment", path="/api/v1/abha/enrol",
               description="Create a new ABHA with Aadhaar OTP (M1). Flow: aadhaar/otp → aadhaar/verify → "
                           "[mobile/otp → mobile/verify] → address-suggestions → address")
s.register(ns)


def _doc(fn):
    fn = ns.response(400, "Validation error", s.ERROR_RESPONSE)(fn)
    fn = ns.response(401, "Missing/invalid access token", s.ERROR_RESPONSE)(fn)
    return fn


@ns.route("/aadhaar/otp")
class AadhaarOtp(TenantResource):
    @ns.doc(description="Send an OTP to the mobile linked with this Aadhaar. Returns `txnId`. "
                        "ABDM rate limits arrive as HTTP 429 with ABDM's message.")
    @ns.expect(s.AADHAAR, validate=False)
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.send_aadhaar_otp(body()))


@ns.route("/aadhaar/verify")
class AadhaarVerify(TenantResource):
    @ns.doc(description=(
        "Verify the Aadhaar OTP and create (or fetch the existing) ABHA. Returns "
        "`{txnId, message, isNew, tokens{token, expiresIn, refreshToken, refreshExpiresIn}, abhaProfile{abhaNumber, "
        "firstName, ..., mobile, phrAddress[]}}`. If `abhaProfile.mobile` differs from the mobile you sent, "
        "verify the new mobile with mobile/otp + mobile/verify."))
    @ns.expect(s.ENROL_VERIFY, validate=False)
    @ns.response(200, "ABHA created", s.GENERIC_RESPONSE)
    @_doc
    def post(self):
        return success(svc.verify_aadhaar_otp(body()))


@ns.route("/mobile/otp")
class MobileOtp(TenantResource):
    @ns.doc(description="Send an OTP to a new communication mobile during enrolment.")
    @ns.expect(s.TXN_MOBILE, validate=False)
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.send_mobile_update_otp(body()))


@ns.route("/mobile/verify")
class MobileVerify(TenantResource):
    @ns.doc(description="Verify the communication-mobile OTP. Wrong OTP: HTTP 200, `success:false`, "
                        "`data.authResult:\"failed\"`.")
    @ns.expect(s.TXN_OTP, validate=False)
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.verify_mobile_update_otp(body()))


@ns.route("/address-suggestions")
class AddressSuggestions(TenantResource):
    @ns.doc(description="Suggested ABHA addresses. Returns `{txnId, abhaAddressList[]}`.",
            params={"txnId": {"description": "txnId from aadhaar/verify", "in": "query", "required": True}})
    @ns.response(200, "Suggestions", s.GENERIC_RESPONSE)
    @_doc
    def get(self):
        return success(svc.address_suggestions(request.args.get("txnId")))


@ns.route("/address")
class AbhaAddress(TenantResource):
    @ns.doc(description="Create the chosen ABHA address (without the @sbx / @abdm suffix).")
    @ns.expect(s.ABHA_ADDRESS_CREATE, validate=False)
    @ns.response(200, "Created", s.GENERIC_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.create_abha_address(body()))


# ---- Driving licence -----------------------------------------------------------------------------------------

@ns.route("/dl/otp")
class DlOtp(TenantResource):
    @ns.doc(description="Driving-licence enrolment, step 1: OTP to the mobile. Body: `mobile`.")
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.send_dl_otp(body()))


@ns.route("/dl/verify")
class DlVerify(TenantResource):
    @ns.doc(description="Step 2: verify the mobile OTP. Body: `txnId`, `otp`. Returns a txnId for `dl/create`.")
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.verify_dl_otp(body()))


@ns.route("/dl/create")
class DlCreate(TenantResource):
    @ns.doc(description="Step 3: create the ABHA from the licence. Body: `txnId`, `documentId`, `firstName`, "
                        "`middleName`, `lastName`, `dob`, `gender`, `frontSidePhoto`, `backSidePhoto` (base64), "
                        "`address`, `state`, `district`, `pinCode`.")
    @ns.response(200, "Enrolment number / ABHA", s.GENERIC_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.create_with_dl(body()))


# ---- Biometric / demographic / child -------------------------------------------------------------------------

@ns.route("/biometric/<string:method>")
@ns.doc(params={"method": {"description": "fingerprint | face | iris", "enum": ["fingerprint", "face", "iris"]}})
class BiometricEnrol(TenantResource):
    @ns.doc(description="Create an ABHA with a biometric Aadhaar auth. Body: `aadhaarNumber`, `pid` (device PID "
                        "data, passed on unchanged), `mobile`. Same result as `aadhaar/verify`.")
    @ns.response(200, "ABHA created", s.GENERIC_RESPONSE)
    @_doc
    def post(self, method):
        return success(svc.enrol_by_biometric(method, body()))


@ns.route("/demo-auth")
class DemoAuth(TenantResource):
    @ns.doc(description="Create an ABHA with demographic Aadhaar auth. Body: `aadhaarNumber`, `mobile`, `name`, "
                        "`dateOfBirth`, `gender`, `stateCode`, `districtCode`, `address`, `pincode`, optional "
                        "`profilePhoto` (plain base64).")
    @ns.response(200, "ABHA created", s.GENERIC_RESPONSE)
    @_doc
    def post(self):
        return success(svc.enrol_by_demographics(body()))


@ns.route("/child")
class ChildAbha(TenantResource):
    @ns.doc(description="Create a child ABHA under the parent's ABHA user token. Body: `name`, `gender`, "
                        "`dayOfBirth`, `monthOfBirth`, `yearOfBirth`, optional `profilePhoto`, `password`.")
    @ns.response(200, "Child ABHA", s.GENERIC_RESPONSE)
    @_doc
    def post(self):
        return from_abdm(svc.create_child_abha(body()))

    @ns.doc(description="List the child ABHAs of the parent's ABHA user token.",
            params={"xToken": {"in": "query", "description": "parent's ABHA user token (or X-Token header)"}})
    @ns.response(200, "Children", s.GENERIC_RESPONSE)
    @_doc
    def get(self):
        return from_abdm(svc.list_children({}))
