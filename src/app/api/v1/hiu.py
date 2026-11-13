from flask import g
from flask_restx import Namespace

from src.app.api.v1 import examples
from src.app.api.v1 import schemas as s
from src.app.common.response import current_request_id
from src.app.common.validators import body
from src.app.middleware.tenant_auth import CredentialsResource
from src.app.services import hiu_consent_service as consent_service
from src.app.services import hiu_service as svc

ns = Namespace("HIU (M3)", path="/api/v1/hiu", description=(
    "A facility asking other facilities for a patient's records (i-medicus is both HIP and HIU): consent request, "
    "consent notification acknowledgement, consent fetch and the health-information request. Same bodies i-medicus "
    "sent VCDoctor. Auth: the access token, or `Client-Id` + `Client-Secret` headers. The facility is the client's own "
    "`hipId` (the id is the same whichever role it plays); it goes to ABDM as `X-HIU-ID`."))
s.register(ns)


def _envelope(data, status=200):
    return {"success": True, "status": "success", "data": data, "requestId": current_request_id()}, status


@ns.route("/consent/init")
class ConsentInit(CredentialsResource):
    @ns.doc(description="Request a patient's consent. Body: `requestId` (kept by i-medicus: ABDM's `on-init` callback "
                        "carries it back), `patientId` (ABHA address), `requesterName`, `requesterType`, "
                        "`requesterValue`, `fromDate`, `toDate`, `dataEraseAt`, `hiType[]`, `purposeOfRequest` "
                        "(CAREMGT, BTG, PUBHLTH, HPAYMT, DSRCH, PATRQT or its text), `hipId`. Sent once, never queued.")
    @ns.response(202, "Requested; the outcome arrives as a callback", s.GENERIC_RESPONSE)
    @examples.body(ns, "HiuConsentInit")
    def post(self):
        return _envelope(svc.consent_init(g.client, body()), 202)


@ns.route("/consent/on-notify")
class ConsentOnNotify(CredentialsResource):
    @ns.doc(description="Acknowledge a consent notification (`hiu/consent/request/notify`). Body: `acknowledgement` "
                        "(`[{status, consentId}]`), `response.requestId` (the callback's REQUEST-ID), `hipId`. Sent "
                        "once; queued and retried if ABDM is down; a second answer returns `status: already_sent`.")
    @ns.response(200, "Sent / queued / already_sent", s.GENERIC_RESPONSE)
    @examples.body(ns, "HiuConsentOnNotify")
    def post(self):
        return _envelope(svc.consent_on_notify(g.client, body()))


@ns.route("/consent/fetch")
class ConsentFetch(CredentialsResource):
    @ns.doc(description="Fetch a granted consent artefact (answer arrives as `hiu/consent/on-fetch`). Body: "
                        "`consentId`, `hipId`. Queued and retried if ABDM is down.")
    @ns.response(200, "Sent / queued", s.GENERIC_RESPONSE)
    @examples.body(ns, "HiuConsentFetch")
    def post(self):
        return _envelope(svc.consent_fetch(g.client, body()))


@ns.route("/consent/status")
class ConsentStatus(CredentialsResource):
    @ns.doc(description="Ask ABDM where a consent request stands (answer arrives as the "
                        "`hiu/consent/request/on-status` callback). Body: `consentRequestId` (the id ABDM's `on-init` "
                        "callback returned), `hipId`. Queued and retried if ABDM is down.")
    @ns.response(200, "Sent / queued", s.GENERIC_RESPONSE)
    @examples.body(ns, "HiuConsentStatus")
    def post(self):
        return _envelope(svc.consent_status(g.client, body()))


@ns.route("/consent/<string:consent_id>")
class ConsentState(CredentialsResource):
    @ns.doc(description="A consent as the gateway knows it from ABDM's callbacks: `status`, whether it is `usable` "
                        "now (and the `reason` if not: DENIED, REVOKED, EXPIRED), granted date range, erase date and "
                        "the health-information requests made under it (`transactionId`, status). Only the client's "
                        "own consents: another client's is 404 `CONSENT_NOT_FOUND`.")
    @ns.response(200, "The consent", s.GENERIC_RESPONSE)
    def get(self, consent_id):
        return _envelope(consent_service.describe(g.client.id, consent_id))


@ns.route("/health-information/request")
class HealthInformationRequest(CredentialsResource):
    @ns.doc(description="Ask ABDM to have the HIPs of a consent send the records, encrypted for our key. Body: "
                        "`consentId`, `fromDate`, `toDate`, `expiryDate` (of our key), `dataPushUrl` (where the HIP "
                        "pushes), `publicKey`, `nonce` (from `GET /api/v1/fhir/keys`), `hipId`, `requestId`. Sent once, "
                        "never queued (a repeat would open a second transaction). A consent the gateway knows to be denied, revoked, "
                        "expired or not covering the date range is refused: 403 `CONSENT_INVALID`, nothing is sent.")
    @ns.response(202, "Requested; ABDM calls back with the transactionId", s.GENERIC_RESPONSE)
    @examples.body(ns, "HiuHiRequest")
    def post(self):
        return _envelope(svc.health_information_request(g.client, body()), 202)
