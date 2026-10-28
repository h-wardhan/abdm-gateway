from flask import g
from flask_restx import Namespace, fields

from src.app.common.response import current_request_id, success
from src.app.common.validators import body, pick, require
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


qr_request = ns.model("FacilityQrRequest", {
    "hipId": fields.String(required=True, example="IN0810041408", description="Your clinic's facility id"),
    "callbackUrl": fields.String(description="Accepted for i-medicus compatibility; callbacks go to the webhook "
                                              "registered for the clinic, not to this URL"),
    "counterId": fields.String(description="Optional desk / counter id"),
})


@ns.route("/qr")
class FacilityQr(TenantResource):
    @ns.doc(description=(
        "The QR code of one of your clinics, for the clinic desk: the patient scans it in the ABHA app "
        "(Scan & Share). Same body i-medicus sends to generate it (`hipId`, `callbackUrl`). The answer keeps the "
        "shape its dashboard reads: `type: Success` and `data`, a PNG data URI usable as `<img src>`; `payload` "
        "is what the QR holds. Only your own registered clinics."))
    @ns.expect(qr_request, validate=False)
    @ns.response(200, "QR created")
    @ns.response(404, "No such clinic for your client")
    def post(self):
        data = body()
        hip_id = require(pick(data, "hipId", "hip_id"), "hipId")
        payload, image = share_service.facility_qr(g.client, hip_id, pick(data, "counterId", "counter_id"))
        return {"success": True, "type": "Success", "data": image, "code": payload["hipId"], "payload": payload,
                "requestId": current_request_id()}
