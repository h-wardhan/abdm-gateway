from flask import g
from flask_restx import Namespace

from src.app.api.v1 import schemas as s
from src.app.common.response import current_request_id
from src.app.common.validators import body
from src.app.middleware.tenant_auth import CredentialsResource
from src.app.services import hip_service as svc

ns = Namespace("HIP (M2)", path="/api/v1/hip", description=(
    "Care-context linking, consent and health-information acknowledgements for a facility (HIP). Auth: the access "
    "token, or `Client-Id` + `Client-Secret` headers (what i-medicus sends). The facility is the one a callback "
    "arrived for (answers) or the client's own `hipid` (calls we start). Answers are matched to ABDM's callback by "
    "`response.requestId` and are sent once; if ABDM is down they are queued and retried "
    "(`status: queued`, HTTP unchanged). A second answer to the same callback returns `status: already_sent`."))
s.register(ns)

_REQUEST_ID = "`response.requestId` (or `requestId`) is the REQUEST-ID of the callback being answered. "


def _envelope(data, status=200):
    """The i-medicus envelope, plus the top-level `status: success` its handlers read."""
    return {"success": True, "status": "success", "data": data, "requestId": current_request_id()}, status


# ---- user-initiated linking (patient scans in the ABHA app) -------------------------------------------------------

@ns.route("/care-context/on-discover")
class OnDiscover(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Answer a patient care-context discover callback: `transactionId`, `patient[]` "
                                     "(referenceNumber, display, careContexts[], hiType, count), `matchedBy`.")
    @ns.response(200, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    @ns.response(404, "No such callback for your facilities", s.ERROR_RESPONSE)
    def post(self):
        return _envelope(svc.on_discover(g.client, body()))


@ns.route("/link/on-init")
class OnInit(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Answer a link care-context init callback: `transactionId`, `link` "
                                     "(referenceNumber, authenticationType, meta).")
    @ns.response(200, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    def post(self):
        return _envelope(svc.on_init(g.client, body()))


@ns.route("/link/on-confirm")
class OnConfirm(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Answer a link care-context confirm callback after verifying the OTP: "
                                     "`patient[]` (or `Patient[]`).")
    @ns.response(200, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    def post(self):
        return _envelope(svc.on_confirm(g.client, body()))


# ---- HIP-initiated linking (the clinic links the patient) ---------------------------------------------------------

@ns.route("/link-token")
class LinkToken(CredentialsResource):
    @ns.doc(description="Ask ABDM for a link token for a patient. Body: `hipid`, `abhaNumber`, `abhaAddress`, `name`, "
                        "`gender`, `yearOfBirth`. ABDM answers 202; the token arrives later at the webhook "
                        "(`api/v3/hip/token/on-generate-token`).")
    @ns.response(202, "Requested", s.GENERIC_RESPONSE)
    def post(self):
        return _envelope(svc.generate_token(g.client, body()), 202)


@ns.route("/link/care-context")
class LinkCareContext(CredentialsResource):
    @ns.doc(description="Link care contexts to a patient (one or several `patient[]` entries). Header `X-Link-Token` "
                        "(or `linkToken`). Body: `hipid`, and `singlecarecontext` {abhaNumber, abhaAddress, "
                        "patient[]} or the same keys at the top level. ABDM answers 202; the result arrives at the "
                        "webhook (`api/v3/link/on_carecontext`).",
            params={"X-Link-Token": {"in": "header", "description": "link token from on-generate-token"}})
    @ns.response(202, "Requested", s.GENERIC_RESPONSE)
    def post(self):
        return _envelope(svc.link_care_context(g.client, body()), 202)


@ns.route("/link/notify")
class LinkNotify(CredentialsResource):
    @ns.doc(description="Tell the patient a care context was linked. Header `X-Link-Token`. Body: `hipid`, "
                        "`abhaAddress`, `hiType` (list), `patientReference`, `careContextReference`.",
            params={"X-Link-Token": {"in": "header", "description": "link token"}})
    @ns.response(201, "Sent", s.GENERIC_RESPONSE)
    def post(self):
        return _envelope(svc.link_notify(g.client, body()), 201)


@ns.route("/sms/notify")
class SmsNotify(CredentialsResource):
    @ns.doc(description="SMS to a patient without an ABHA, asking them to link records. Body: `hipId`, "
                        "`phoneNumber` (`name` is ignored: the clinic name is the one registered for the facility).")
    @ns.response(200, "Sent", s.GENERIC_RESPONSE)
    def post(self):
        return _envelope(svc.sms_notify(g.client, body()))


# ---- consent and health-information requests ----------------------------------------------------------------------

@ns.route("/consent/on-notify")
class ConsentOnNotify(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Acknowledge a consent notification (`consent/request/hip/notify`). Body: "
                                     "`consentId`, `requestId` (i-medicus), or ABDM's `acknowledgement` + `response`.")
    @ns.response(201, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    def post(self):
        return _envelope(svc.consent_on_notify(g.client, body()), 201)


@ns.route("/health-information/on-request")
class HiOnRequest(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Acknowledge a health-information request. The gateway first checks the "
                                     "consent it received for the request: unknown, revoked, expired or a date range "
                                     "outside the consent is refused with HTTP 403 `CONSENT_INVALID` "
                                     "(`details.reason`) and nothing is sent to ABDM.")
    @ns.response(200, "Acknowledged", s.GENERIC_RESPONSE)
    @ns.response(403, "Consent not valid", s.ERROR_RESPONSE)
    def post(self):
        return _envelope(svc.hi_on_request(g.client, body()))
