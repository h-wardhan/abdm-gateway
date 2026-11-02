from flask_restx import Namespace

from src.app.api.v1.profile import _file_response
from src.app.common.validators import body, x_token
from src.app.middleware.tenant_auth import CredentialsResource
from src.app.services import app_compat_service as compat
from src.app.services import enrolment_service, login_service
from src.app.services import profile_service

ns = Namespace("ABHA App (compat)", path="/api/v1/abha/app", description=(
    "The calls i-medicus' mobile-app service makes, with the answers it reads: ABDM's response as-is at the top "
    "level (no `data` envelope). Auth is the `Client-Id` + `Client-Secret` headers it sends (a gateway token also "
    "works). New code should use the other ABHA endpoints."))


def _created_unless_failed(result):
    """HTTP 201 when ABDM accepted the OTP, 200 when it said the OTP was wrong (the caller tells them apart)."""
    return result, (201 if compat.succeeded(result) else 200)


@ns.route("/aadhaar/otp")
class AadhaarOtp(CredentialsResource):
    @ns.doc(description="Aadhaar OTP for a new ABHA. Body: `aadharNumber`. Answer: `{txnId, message}`.")
    def post(self):
        return enrolment_service.send_aadhaar_otp(body())


@ns.route("/aadhaar/verify")
class AadhaarVerify(CredentialsResource):
    @ns.doc(description="Verify the Aadhaar OTP. Body: `txnId`, `otpValue`, `mobile`. Answer: `{txnId, isNew, "
                        "tokens, ABHAProfile}`.")
    def post(self):
        return compat.aadhaar_verify(body())


@ns.route("/mobile/otp")
class MobileLoginOtp(CredentialsResource):
    @ns.doc(description="Login by mobile, one step: finds the ABHA and sends the OTP. Body: `mobileNumber`. "
                        "Answer (HTTP 201): `{txnId, message}`.")
    def post(self):
        return compat.mobile_login_otp(body()), 201


@ns.route("/mobile/verify")
class MobileLoginVerify(CredentialsResource):
    @ns.doc(description="Verify the login OTP. Body: `txnId`, `otp`. Answer: `{accounts, token, expiresIn, "
                        "refreshToken, ...}` with HTTP 201; a wrong OTP is HTTP 200 with `authResult: failed`.")
    def post(self):
        return _created_unless_failed(login_service.verify_mobile_login_otp(body()))


@ns.route("/email/verify-link")
class EmailVerifyLink(CredentialsResource):
    @ns.doc(description="Email verification link. Body: `email`; the ABHA user token in `X-Token`.")
    def post(self):
        return enrolment_service.send_email_verification_link(body())


@ns.route("/address-suggestions")
class AddressSuggestions(CredentialsResource):
    @ns.doc(description="ABHA address suggestions. Body: `txnId`. Answer: `{txnId, abhaAddressList}`.")
    def post(self):
        return enrolment_service.address_suggestions(body().get("txnId"))


@ns.route("/address")
class Address(CredentialsResource):
    @ns.doc(description="Create the ABHA address. Body: `txnId`, `abhaAddress`, `preferred`.")
    def post(self):
        return enrolment_service.create_abha_address(body())


@ns.route("/abha-number/otp")
class AbhaNumberOtp(CredentialsResource):
    @ns.doc(description="Login by ABHA number, OTP to the linked mobile. Body: `abhaNumber`. Answer: `{txnId, message}`.")
    def post(self):
        return login_service.send_abha_number_otp(body(), "mobile")


@ns.route("/abha-number/verify")
class AbhaNumberVerify(CredentialsResource):
    @ns.doc(description="Verify that OTP. Body: `txnId`, `OTP`. Answer: `{accounts, token, refreshToken, expiresIn, "
                        "refreshExpiresIn, ...}`; a wrong OTP is `authResult: failed`.")
    def post(self):
        return login_service.verify_abha_number_otp(body(), "mobile")


@ns.route("/profile/card")
class Card(CredentialsResource):
    @ns.doc(description="ABHA card bytes. ABHA user token in `X-Token` (with or without 'Bearer ').")
    def get(self):
        content, content_type = profile_service.abha_card(x_token())
        return _file_response(content, content_type, "abha-card")


@ns.route("/profile/qr")
class Qr(CredentialsResource):
    @ns.doc(description="ABHA QR bytes. ABHA user token in `X-Token` (with or without 'Bearer ').")
    def get(self):
        content, content_type = profile_service.qr_code(x_token())
        return _file_response(content, content_type, "abha-qr")
