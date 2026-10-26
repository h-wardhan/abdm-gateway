from flask import Response
from flask_restx import Namespace

from src.app.api.v1 import schemas as s
from src.app.common.response import from_abdm, success
from src.app.common.validators import body, x_token
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import enrolment_service
from src.app.services import profile_service as svc

ns = Namespace("ABHA Profile", path="/api/v1/abha", description=(
    "Profile, ABHA card and QR (M1). All need the ABHA user token from a login/enrol verify: "
    "`xToken` query param or `X-Token` header (with or without 'Bearer '). "
    "Card/QR endpoints return the file bytes with its Content-Type; errors are JSON."))
s.register(ns)

X_TOKEN_PARAM = {"xToken": {"in": "query", "description": "ABHA user token (or X-Token header)"}}


def _file_response(content, content_type, filename):
    return Response(content, mimetype=content_type,
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@ns.route("/profile")
class Profile(TenantResource):
    @ns.doc(params=X_TOKEN_PARAM, description="ABHA account profile (ABDM keys as-is: ABHANumber, firstName, "
                                              "yearOfBirth, monthOfBirth, dayOfBirth, mobile, preferredAbhaAddress, ...).")
    @ns.response(200, "Profile", s.GENERIC_RESPONSE)
    @ns.response(401, "ABHA token invalid/expired", s.ERROR_RESPONSE)
    def get(self):
        return success(svc.account_profile(x_token()))


@ns.route("/profile/card")
class Card(TenantResource):
    @ns.doc(params=X_TOKEN_PARAM, description="ABHA card (PNG/PDF bytes).")
    @ns.produces(["image/png", "application/pdf"])
    def get(self):
        content, content_type = svc.abha_card(x_token())
        return _file_response(content, content_type, "abha-card")


@ns.route("/profile/qr")
class Qr(TenantResource):
    @ns.doc(params=X_TOKEN_PARAM, description="ABHA QR code (PNG bytes).")
    @ns.produces(["image/png"])
    def get(self):
        content, content_type = svc.qr_code(x_token())
        return _file_response(content, content_type, "abha-qr")


@ns.route("/profile/email/verify-link")
class EmailVerifyLink(TenantResource):
    @ns.doc(description="Send an email verification link to the ABHA holder. Returns `{txnId, message}`.",
            params={"X-Token": {"in": "header", "description": "ABHA user token (or xToken in body/query)"}})
    @ns.expect(s.EMAIL, validate=False)
    @ns.response(200, "Link sent", s.TXN_MESSAGE_RESPONSE)
    def post(self):
        return from_abdm(enrolment_service.send_email_verification_link(body()))


@ns.route("/phr/profile")
class PhrProfile(TenantResource):
    @ns.doc(params=X_TOKEN_PARAM, description="ABHA address (PHR) profile, after an ABHA address login "
                                              "(`kycStatus`, `abhaAddress`, `fullName`, `dateOfBirth`, ...).")
    @ns.response(200, "Profile", s.GENERIC_RESPONSE)
    def get(self):
        return success(svc.phr_profile(x_token()))


@ns.route("/phr/card")
class PhrCard(TenantResource):
    @ns.doc(params=X_TOKEN_PARAM, description="ABHA address (PHR) card bytes, after an ABHA address login.")
    @ns.produces(["image/png", "application/pdf"])
    def get(self):
        content, content_type = svc.phr_card(x_token())
        return _file_response(content, content_type, "abha-phr-card")
