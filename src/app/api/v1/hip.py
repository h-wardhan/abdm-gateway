from flask import g
from flask_restx import Namespace

from src.app.api.v1 import examples
from src.app.api.v1 import schemas as s
from src.app.common.response import current_request_id
from src.app.common.validators import body
from src.app.middleware.tenant_auth import CredentialsResource
from src.app.services import hip_service as svc
from src.app.services import hip_transfer_service as transfers

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
    @examples.body(ns, "OnDiscover")
    def post(self):
        return _envelope(svc.on_discover(g.client, body()))


@ns.route("/link/on-init")
class OnInit(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Answer a link care-context init callback: `transactionId`, `link` "
                                     "(referenceNumber, authenticationType, meta).")
    @ns.response(200, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    @examples.body(ns, "OnInit")
    def post(self):
        return _envelope(svc.on_init(g.client, body()))


@ns.route("/link/on-confirm")
class OnConfirm(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Answer a link care-context confirm callback after verifying the OTP: "
                                     "`patient[]` (or `Patient[]`).")
    @ns.response(200, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    @examples.body(ns, "OnConfirm")
    def post(self):
        return _envelope(svc.on_confirm(g.client, body()))


# ---- HIP-initiated linking (the clinic links the patient) ---------------------------------------------------------

@ns.route("/link-token")
class LinkToken(CredentialsResource):
    @ns.doc(description="Ask ABDM for a link token for a patient. Body: `hipid`, `abhaNumber`, `abhaAddress`, `name`, "
                        "`gender`, `yearOfBirth`. ABDM answers 202; the token arrives later at the webhook "
                        "(`api/v3/hip/token/on-generate-token`).")
    @ns.response(202, "Requested", s.GENERIC_RESPONSE)
    @examples.body(ns, "LinkToken")
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
    @examples.body(ns, "LinkCareContext")
    def post(self):
        return _envelope(svc.link_care_context(g.client, body()), 202)


@ns.route("/link/notify")
class LinkNotify(CredentialsResource):
    @ns.doc(description="Tell the patient a care context was linked. Header `X-Link-Token`. Body: `hipid`, "
                        "`abhaAddress`, `hiType` (list), `patientReference`, `careContextReference`.",
            params={"X-Link-Token": {"in": "header", "description": "link token"}})
    @ns.response(201, "Sent", s.GENERIC_RESPONSE)
    @examples.body(ns, "LinkNotify")
    def post(self):
        return _envelope(svc.link_notify(g.client, body()), 201)


@ns.route("/sms/notify")
class SmsNotify(CredentialsResource):
    @ns.doc(description="SMS to a patient without an ABHA, asking them to link records. Body: `hipId`, "
                        "`phoneNumber` (`name` is ignored: the clinic name is the one registered for the facility).")
    @ns.response(200, "Sent", s.GENERIC_RESPONSE)
    @examples.body(ns, "SmsNotify")
    def post(self):
        return _envelope(svc.sms_notify(g.client, body()))


# ---- consent and health-information requests ----------------------------------------------------------------------

@ns.route("/consent/on-notify")
class ConsentOnNotify(CredentialsResource):
    @ns.doc(description=_REQUEST_ID + "Acknowledge a consent notification (`consent/request/hip/notify`). Body: "
                                     "`consentId`, `requestId` (i-medicus), or ABDM's `acknowledgement` + `response`.")
    @ns.response(201, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    @examples.body(ns, "HipConsentOnNotify")
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
    @examples.body(ns, "HiOnRequest")
    def post(self):
        return _envelope(svc.hi_on_request(g.client, body()))


# ---- transfer: push the encrypted records to the HIU, tell ABDM how it went ---------------------------------------

@ns.route("/health-information/push")
class HiPush(CredentialsResource):
    @ns.doc(description="Push the encrypted records to the HIU. Body: what i-medicus sent `calling-data-push-url`: "
                        "`transactionId`, `entries[{content, media, checksum, careContextReference}]`, "
                        "`keyMaterial{cryptoAlg, curve, dhPublicKey{expiry, parameters, keyValue}, nonce}` (`pushUrl` "
                        "may be sent; it must be the one ABDM gave). The consent and the care contexts are checked "
                        "again (HTTP 403 `CONSENT_INVALID` / `CARE_CONTEXT_NOT_CONSENTED`); the HIU's URL is the one "
                        "of the acknowledged request. HIU down: HTTP 202 `queued`, the worker retries. HIU says no: "
                        "HTTP 502 `HIU_REJECTED_PUSH` and ABDM is told FAILED.")
    @ns.response(200, "Pushed / already_sent", s.GENERIC_RESPONSE)
    @ns.response(202, "Queued for retry", s.GENERIC_RESPONSE)
    @ns.response(403, "Consent not valid", s.ERROR_RESPONSE)
    @ns.response(502, "The HIU rejected the push", s.ERROR_RESPONSE)
    @examples.body(ns, "HiPush")
    def post(self):
        result = transfers.push(g.client, body())
        return _envelope(result, 202 if result["status"] == "queued" else 200)


@ns.route("/health-information/notify")
class HiNotify(CredentialsResource):
    @ns.doc(description="Tell ABDM how the transfer went (`data-flow-notification-request`). Body: `notification"
                        "{consentId, transactionId, doneAt, notifier, statusNotification{sessionStatus "
                        "(TRANSFERRED | FAILED), hipId, statusResponses[{careContextReference, hiStatus, description}]}}`. "
                        "Every care context that was pushed is reported, also those left out of the body. TRANSFERRED "
                        "while the push is still queued is held (HTTP 202) and sent when the push lands.")
    @ns.response(200, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    @ns.response(202, "Held until the push lands", s.GENERIC_RESPONSE)
    @examples.body(ns, "HiNotify")
    def post(self):
        result = transfers.notify(g.client, body())
        return _envelope(result, 202 if result["status"] == "held" else 200)


@ns.route("/transfers/<string:transaction_id>")
class Transfer(CredentialsResource):
    @ns.doc(description="State of one transfer: ACKNOWLEDGED, PUSH_QUEUED, PUSHED, NOTIFIED or FAILED, the care contexts "
                        "pushed, the attempts and the last error.")
    @ns.response(200, "The transfer", s.GENERIC_RESPONSE)
    @ns.response(404, "No such transfer for your facilities", s.ERROR_RESPONSE)
    def get(self, transaction_id):
        return _envelope(transfers.status_of(g.client, transaction_id))
