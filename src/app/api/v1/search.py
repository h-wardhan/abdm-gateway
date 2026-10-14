from flask_restx import Namespace

from src.app.api.v1 import schemas as s
from src.app.common.response import from_abdm, success
from src.app.common.validators import body
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import login_service as svc

ns = Namespace("ABHA Search", path="/api/v1/abha/search", description="Find ABHA accounts before login (M1)")
s.register(ns)


@ns.route("/mobile")
class SearchMobile(TenantResource):
    @ns.doc(description="ABHAs linked to a mobile. Returns a list: `[{txnId, ABHA:[{index, ABHANumber, name, gender}]}]`. "
                        "Use `txnId` + `index` with login/mobile/otp.")
    @ns.expect(s.MOBILE, validate=False)
    @ns.response(200, "Accounts", s.GENERIC_RESPONSE)
    @ns.response(400, "Validation error / no ABHA", s.ERROR_RESPONSE)
    def post(self):
        return success(svc.search_by_mobile(body()))


@ns.route("/abha-number")
class SearchAbhaNumber(TenantResource):
    @ns.doc(description="Validate an ABHA number and list the OTP methods (`MOBILE_OTP`, `AADHAAR_OTP`). "
                        "ABDM confirms the number exists when the OTP is requested.")
    @ns.expect(s.ABHA_NUMBER, validate=False)
    @ns.response(200, "Auth methods", s.GENERIC_RESPONSE)
    def post(self):
        return success(svc.search_by_abha_number(body()))


@ns.route("/abha-address")
class SearchAbhaAddress(TenantResource):
    @ns.doc(description="Look up an ABHA address. Returns ABDM's result incl. `authMethods`.")
    @ns.expect(s.ABHA_ADDRESS, validate=False)
    @ns.response(200, "Found", s.GENERIC_RESPONSE)
    @ns.response(404, "Not found", s.ERROR_RESPONSE)
    def post(self):
        return from_abdm(svc.search_by_abha_address(body()))
