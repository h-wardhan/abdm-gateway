from flask import request
from flask_restx import Namespace

from src.app.api.v1 import schemas as s
from src.app.common.response import from_abdm, success
from src.app.common.validators import body
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import account_service as svc

ns = Namespace("ABHA Account", path="/api/v1/abha/account", description=(
    "ABHA account lifecycle (M1): delete, deactivate, re-activate, re-KYC, password, profile update, logout, token "
    "refresh. All except re-activate and password login need the ABHA user token (`xToken` query/body or `X-Token` "
    "header). `method` is `aadhaar` (UIDAI OTP), `abha` (ABDM OTP to the ABHA mobile) or `password`. "
    "Wrong OTP: HTTP 200, `success:false`, `data.authResult:\"failed\"`."))
s.register(ns)

_X_HEADER = {"X-Token": {"in": "header", "description": "ABHA user token (or xToken in body/query)"}}
_METHOD = "`method`: aadhaar | abha"


def _otp_route(action, label):
    class _Otp(TenantResource):
        @ns.doc(params=_X_HEADER, description=f"Send the OTP for {label}. Body: `abhaNumber`, {_METHOD}. "
                                              "Returns `{txnId, message}`.")
        @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
        def post(self):
            return from_abdm(svc.send_action_otp(action, body()))

    _Otp.__name__ = f"{action.title().replace('-', '')}Otp"
    return _Otp


def _verify_route(action, label, methods):
    class _Verify(TenantResource):
        @ns.doc(params=_X_HEADER, description=f"Complete {label}. Body: `method` ({methods}), then `txnId` + `otp` "
                                              "for an OTP method or `password` for password"
                                              + (", plus `reasons` (list, required)." if action in svc.NEEDS_REASONS
                                                 else "."))
        @ns.response(200, "Result", s.GENERIC_RESPONSE)
        def post(self):
            return from_abdm(svc.verify_action(action, body()))

    _Verify.__name__ = f"{action.title().replace('-', '')}Verify"
    return _Verify


for _action, _label, _methods in (
        ("delete", "deleting the ABHA", "aadhaar | abha | password"),
        ("deactivate", "deactivating the ABHA", "aadhaar | abha | password"),
        ("re-kyc", "re-KYC of the ABHA", "aadhaar | abha")):
    ns.route(f"/{_action}/otp")(_otp_route(_action, _label))
    ns.route(f"/{_action}/verify")(_verify_route(_action, _label, _methods))


# ---- reactivate (login endpoints: no ABHA token) -------------------------------------------------------------

@ns.route("/reactivate/otp")
class ReactivateOtp(TenantResource):
    @ns.doc(description=f"Send the OTP to re-activate a deactivated ABHA. Body: `abhaNumber`, {_METHOD}.")
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self):
        return from_abdm(svc.send_reactivate_otp(body()))


@ns.route("/reactivate/verify")
class ReactivateVerify(TenantResource):
    @ns.doc(description="Re-activate. Body: `method` (aadhaar | abha | password); `txnId` + `otp` for an OTP, or "
                        "`abhaNumber` + `password`. Returns the login result with a fresh ABHA token.")
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.verify_reactivate(body()))


# ---- password ------------------------------------------------------------------------------------------------

@ns.route("/password")
class Password(TenantResource):
    @ns.doc(params=_X_HEADER, description="Set or change the ABHA password. Body: `newPassword`, optional "
                                          "`oldPassword`, optional `confirmPassword`.")
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.change_password(body()))


@ns.route("/password/otp")
class PasswordOtp(TenantResource):
    @ns.doc(params=_X_HEADER, description=f"Send an OTP to reset the password. Body: `password`, {_METHOD}.")
    @ns.response(200, "OTP sent", s.TXN_MESSAGE_RESPONSE)
    def post(self):
        return from_abdm(svc.password_otp(body()))


@ns.route("/password/verify")
class PasswordVerify(TenantResource):
    @ns.doc(params=_X_HEADER, description="Verify the OTP sent by `password/otp`. Body: `txnId`, `otp`.")
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.password_otp_verify(body()))


# ---- profile update / logout / refresh -----------------------------------------------------------------------

@ns.route("/profile")
class ProfileUpdate(TenantResource):
    @ns.doc(params=_X_HEADER, description="PATCH the ABHA profile. Any of `profilePhoto`, `name`, `dob`, `gender`, "
                                          "`mobile`, `accountStatus`, `abhaNumber`. Without the ABHA token the "
                                          "account is named by `abhaNumber` (benefit / child variants).")
    @ns.response(200, "Updated profile", s.GENERIC_RESPONSE)
    def patch(self):
        return from_abdm(svc.update_profile(body()))


@ns.route("/logout")
class Logout(TenantResource):
    @ns.doc(params=_X_HEADER, description="Log the ABHA user out (invalidates the token).")
    @ns.response(200, "Logged out", s.GENERIC_RESPONSE)
    def get(self):
        return from_abdm(svc.logout({}))


@ns.route("/refresh-token")
class RefreshToken(TenantResource):
    @ns.doc(description="Exchange a refresh token for a new ABHA token. Send the refresh token in the `R-Token` "
                        "header (or `rToken` query).",
            params={"R-Token": {"in": "header", "description": "refreshToken from a login/enrol verify"}})
    @ns.response(200, "New token", s.GENERIC_RESPONSE)
    def get(self):
        return success(svc.refresh_token(request.headers.get("R-Token") or request.args.get("rToken")))


# ---- password login ------------------------------------------------------------------------------------------

@ns.route("/login/password/search")
class PasswordLoginSearch(TenantResource):
    @ns.doc(description="Password login, step 1. Body: `abhaNumber`.")
    @ns.response(200, "Search result", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.login_search(body()))


@ns.route("/login/password/verify")
class PasswordLoginVerify(TenantResource):
    @ns.doc(description="Password login, step 2. Body: `abhaNumber`, `password`. Returns the ABHA user token.")
    @ns.response(200, "Verified", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.login_by_password(body()))
