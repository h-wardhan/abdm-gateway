from flask import g
from flask_restx import Namespace, fields

from src.app.common.response import success
from src.app.common.validators import body
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import share_service

ns = Namespace("Scan & Share", path="/api/v1/share",
               description="Answer a patient who scanned a clinic's QR (the callback arrives at your webhook)")

on_share_request = ns.model("OnShareRequest", {
    "requestId": fields.String(required=True, description="`request-id` header of the "
                                                          "`api/v3/hip/patient/share` callback you received"),
    "abhaAddress": fields.String(required=True, example="ravi@sbx"),
    "tokenNumber": fields.String(required=True, example="5", description="Token you gave the patient"),
    "expiry": fields.String(example="1800", description="Seconds the token is valid (default 1800)"),
})


@ns.route("/on-share")
class OnShare(TenantResource):
    @ns.doc(description=(
        "Tell ABDM the token number of a scanned patient, so the patient sees it in their ABHA app. "
        "The clinic is found from `requestId`; you can only answer scans of your own clinics. "
        "`status: queued` (HTTP 202) means ABDM was not reachable and the gateway will retry."))
    @ns.expect(on_share_request, validate=False)
    @ns.response(200, "Sent to ABDM")
    @ns.response(202, "Queued, will be retried")
    @ns.response(404, "No such scan for your clinics")
    def post(self):
        result, hip_id = share_service.respond(g.client, body())
        if result["status"] == "queued":
            return success({"status": "queued", "hipId": hip_id, "jobId": result["jobId"]}, 202)
        return success({"status": "sent", "hipId": hip_id})
