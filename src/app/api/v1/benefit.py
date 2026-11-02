from flask_restx import Namespace

from src.app.api.v1 import schemas as s
from src.app.common.response import from_abdm
from src.app.common.validators import body
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import benefit_service as svc

ns = Namespace("ABHA Benefit", path="/api/v1/abha/benefit", description=(
    "Benefit programme APIs: search an ABHA, link / de-link it to a benefit. The account is named by `abhaNumber` "
    "or `xmlUid`; linking can also use the ABHA user token (`X-Token`). Every call needs `benefitName` (body, or the "
    "`BENEFIT_NAME` header): the benefit programme ABDM enabled for the NHA client."))
s.register(ns)


@ns.route("/search")
class BenefitSearch(TenantResource):
    @ns.doc(description="Search a benefit by `abhaNumber` or `xmlUid`.")
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.search(body()))


@ns.route("/link")
class BenefitLink(TenantResource):
    @ns.doc(description="Link or de-link. Body: `action` (link | de-link) and `abhaNumber` or `xmlUid`; linking "
                        "may instead send the ABHA user token.",
            params={"X-Token": {"in": "header", "description": "ABHA user token (link only, optional)"}})
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    def post(self):
        return from_abdm(svc.link_or_delink(body()))


@ns.route("/abha/<string:abha_number>")
class BenefitAbha(TenantResource):
    @ns.doc(description="Look up an ABHA by number in the benefit programme.")
    @ns.response(200, "Result", s.GENERIC_RESPONSE)
    def get(self, abha_number):
        return from_abdm(svc.abha_by_number(abha_number))
