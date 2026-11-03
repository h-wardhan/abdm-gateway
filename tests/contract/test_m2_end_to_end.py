"""
M2 (Days 9-11) end to end, the way it runs on the server, with mocked ABDM and an i-medicus style webhook:

  ABDM (signed callback) -> gateway receiver -> worker -> i-medicus webhook (its handler runs)
  -> i-medicus answers through /api/v1/hip/... with the bodies it always sent -> gateway -> ABDM

Nothing real is called. The i-medicus side is played by the test: it reads the forwarded callback exactly as
`ABHAController::webhook` does (url key after /api/abdm-webhook/, `request-id` header).
"""
import json

import responses

from src.app.models import CallbackEvent, HipConsent
from src.app.outbox import worker
from tests.conftest import CALLBACK_URL, GATEWAY, HIP_ID, make_jwt

HIECM = f"{GATEWAY}/api/hiecm"
SESSION = f"{GATEWAY}/api/hiecm/gateway/v3/sessions"
ABDM = {
    "on_discover": f"{HIECM}/user-initiated-linking/v3/patient/care-context/on-discover",
    "on_init": f"{HIECM}/user-initiated-linking/v3/link/care-context/on-init",
    "on_confirm": f"{HIECM}/user-initiated-linking/v3/link/care-context/on-confirm",
    "token": f"{HIECM}/v3/token/generate-token",
    "link": f"{HIECM}/hip/v3/link/carecontext",
    "notify": f"{HIECM}/hip/v3/link/context/notify",
    "consent": f"{HIECM}/consent/v3/request/hip/on-notify",
    "hi": f"{HIECM}/data-flow/v3/health-information/hip/on-request",
}
TXN = "a1b2c3d4-0000-1111-2222-333344445555"
CONSENT_ID = "ed486d33-3efa-4b8e-9a5c-47359467c20d"
PATIENT = [{"referenceNumber": "P-1", "display": "Ravi", "hiType": "Prescription", "count": 1,
            "careContexts": [{"referenceNumber": "CC-1", "display": "Visit 1"}]}]


class World:
    """ABDM, the gateway and i-medicus around one test."""

    def __init__(self, client, http, auth):
        self.client, self.http, self.auth = client, http, auth
        http.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
        for url in ABDM.values():
            http.add(responses.POST, url, json={}, status=202)

    def abdm_calls_gateway(self, path, body, request_id):
        res = self.client.post(f"/api/{path}", data=json.dumps(body), headers={
            "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "X-HIP-ID": HIP_ID,
            "Content-Type": "application/json"})
        assert res.status_code == 202
        return res

    def imedicus_receives(self, path):
        """Run the worker; return (url key, request-id header, body) of what i-medicus got for `path`."""
        self.http.add(responses.POST, f"{CALLBACK_URL}/api/{path}", json={"status": "success"})
        assert worker.run(once=True) >= 1
        call = [c for c in self.http.calls if c.request.url == f"{CALLBACK_URL}/api/{path}"][-1].request
        return call.url.split("/api/abdm-webhook/")[1], call.headers["request-id"], json.loads(call.body)

    def imedicus_answers(self, path, body):
        res = self.client.post(f"/api/v1/hip/{path}", json=body, headers=self.auth)
        return res

    def abdm_got(self, name):
        return [json.loads(c.request.body) for c in self.http.calls if c.request.url == ABDM[name]]


def test_user_initiated_linking_discover_init_confirm(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)

    # 1. patient searches the clinic in the ABHA app
    w.abdm_calls_gateway("v3/hip/patient/care-context/discover",
                         {"transactionId": TXN, "patient": {"id": "ravi@sbx", "name": "Ravi", "gender": "M",
                                                            "verifiedIdentifiers": [{"type": "MOBILE",
                                                                                     "value": "9876543210"}]}}, "r-1")
    key, request_id, body = w.imedicus_receives("v3/hip/patient/care-context/discover")
    assert key == "api/v3/hip/patient/care-context/discover" and request_id == "r-1"
    assert body["patient"]["verifiedIdentifiers"][0]["value"] == "9876543210"  # what onCareContextDiscover reads
    res = w.imedicus_answers("care-context/on-discover", {
        "transactionId": body["transactionId"], "patient": PATIENT, "matchedBy": ["MR"],
        "response": {"requestId": request_id}})
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent"
    assert w.abdm_got("on_discover")[0]["response"] == {"requestId": "r-1"}

    # 2. patient picks a care context: ABDM asks for the link, i-medicus sends the OTP and answers
    w.abdm_calls_gateway("v3/link/care-context/init", {"transactionId": TXN, "patient": {"id": "ravi@sbx"}}, "r-2")
    key, request_id, body = w.imedicus_receives("v3/link/care-context/init")
    res = w.imedicus_answers("link/on-init", {"transactionId": body["transactionId"], "link": {
        "referenceNumber": "link-1", "authenticationType": "DIRECT",
        "meta": {"communicationMedium": "MOBILE", "communicationHint": "OTP",
                 "communicationExpiry": "2026-11-03T10:05:00.000Z"}}, "response": {"requestId": request_id}})
    assert res.status_code == 200 and w.abdm_got("on_init")[0]["link"]["referenceNumber"] == "link-1"

    # 3. patient types the OTP: i-medicus verifies it and confirms (its confirm body uses a capital `Patient`)
    w.abdm_calls_gateway("v3/link/care-context/confirm", {"confirmation": {"linkRefNumber": "link-1", "token": "123456"}},
                         "r-3")
    key, request_id, body = w.imedicus_receives("v3/link/care-context/confirm")
    assert body["confirmation"]["token"] == "123456"
    res = w.imedicus_answers("link/on-confirm", {"Patient": PATIENT, "response": {"requestId": request_id}})
    assert res.status_code == 200 and res.get_json()["success"] is True  # i-medicus checks 200 + success
    assert w.abdm_got("on_confirm")[0] == {"patient": PATIENT, "response": {"requestId": "r-3"}}

    # a retried confirm callback from ABDM is dropped, a repeated answer is not sent again
    w.abdm_calls_gateway("v3/link/care-context/confirm", {"confirmation": {"linkRefNumber": "link-1", "token": "123456"}},
                         "r-3")
    assert CallbackEvent.query.filter_by(request_id="r-3").count() == 1
    again = w.imedicus_answers("link/on-confirm", {"Patient": PATIENT, "response": {"requestId": "r-3"}})
    assert again.get_json()["data"]["status"] == "already_sent" and len(w.abdm_got("on_confirm")) == 1


def test_hip_initiated_linking_token_link_result_and_notify(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)

    # i-medicus asks for a link token (ABDM answers 202; the token itself comes as a callback)
    res = w.imedicus_answers("link-token", {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "name": "Ravi",
                                            "gender": "M", "yearOfBirth": 1989, "hipid": HIP_ID})
    assert res.status_code == 202
    w.abdm_calls_gateway("v3/hip/token/on-generate-token", {"abhaAddress": "ravi@sbx", "linkToken": "tok-xyz",
                                                           "response": {"requestId": "r-9"}}, "r-t")
    key, _, body = w.imedicus_receives("v3/hip/token/on-generate-token")
    assert key == "api/v3/hip/token/on-generate-token" and body["linkToken"] == "tok-xyz"  # onGenerateToken saves it

    # links the care context with that token, ABDM reports the result as a callback
    res = w.imedicus_answers("link/care-context", {"hipid": HIP_ID, "linkToken": body["linkToken"], "singlecarecontext": {
        "abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "patient": PATIENT}})
    assert res.status_code == 202
    assert [c.request.headers["X-LINK-TOKEN"] for c in w.http.calls if c.request.url == ABDM["link"]] == ["tok-xyz"]
    w.abdm_calls_gateway("v3/link/on_carecontext", {"abhaAddress": "ravi@sbx", "status": "Successfully Linked care "
                                                    "context", "response": {"requestId": "r-link"}}, "r-l")
    key, _, body = w.imedicus_receives("v3/link/on_carecontext")
    assert key == "api/v3/link/on_carecontext" and body["abhaAddress"] == "ravi@sbx"  # onCareContext marks LINKED

    # and notifies the patient
    res = w.imedicus_answers("link/notify", {"hipid": HIP_ID, "linkToken": "tok-xyz", "abhaAddress": "ravi@sbx",
                                             "hiType": ["Prescription"], "patientReference": "P-1",
                                             "careContextReference": "CC-1"})
    assert res.status_code == 201 and w.abdm_got("notify")[0]["notification"]["hip"] == {"id": HIP_ID}


def _consent_notification(status="GRANTED", erase_at="2126-11-30T00:00:00.000Z"):
    return {"notification": {"status": status, "consentId": CONSENT_ID, "consentDetail": {
        "consentId": CONSENT_ID, "patient": {"id": "ravi@sbx"}, "hip": {"id": HIP_ID},
        "careContexts": [{"patientReference": "P-1", "careContextReference": "CC-1"}], "hiTypes": ["Prescription"],
        "permission": {"accessMode": "VIEW", "dateRange": {"from": "2026-10-01T00:00:00.000Z",
                                                           "to": "2026-11-30T00:00:00.000Z"},
                       "dataEraseAt": erase_at}}, "signature": "sig", "grantAcknowledgement": False}}


def test_consent_notify_then_health_information_request(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)

    # consent granted: i-medicus gets it, acknowledges it (its body: consentId + requestId + hipId)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    key, request_id, body = w.imedicus_receives("v3/consent/request/hip/notify")
    assert key == "api/v3/consent/request/hip/notify" and body["notification"]["status"] == "GRANTED"
    res = w.imedicus_answers("consent/on-notify", {"consentId": body["notification"]["consentId"],
                                                    "requestId": request_id, "hipId": HIP_ID})
    assert res.status_code == 201 and res.get_json()["status"] == "success"  # consentHipOnNotify reads `status`
    assert w.abdm_got("consent")[0]["acknowledgement"] == {"status": "OK", "consentId": CONSENT_ID}
    assert HipConsent.query.one().status == "GRANTED"

    # HIU asks for data: valid consent, so it is acknowledged
    request = {"transactionId": TXN, "hiRequest": {"consent": {"id": CONSENT_ID}, "dateRange": {
        "from": "2026-10-10T00:00:00.000Z", "to": "2026-11-01T00:00:00.000Z"}, "dataPushUrl": "https://hiu.test/push",
        "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "nonce": "n"}}}
    w.abdm_calls_gateway("v3/hip/health-information/request", request, "r-h1")
    key, request_id, body = w.imedicus_receives("v3/hip/health-information/request")
    assert body["hiRequest"]["consent"]["id"] == CONSENT_ID  # what onHealthInformationRequest reads
    res = w.imedicus_answers("health-information/on-request", {"transactionId": body["transactionId"],
                                                               "requestId": request_id})
    assert res.status_code == 200 and res.get_json()["status"] == "success"  # consentAcknowledge reads `status`
    assert w.abdm_got("hi")[0]["hiRequest"] == {"transactionId": TXN, "sessionStatus": "ACKNOWLEDGED"}

    # the patient revokes; the next request is refused and nothing more goes to ABDM
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification("REVOKED"), "r-c2")
    w.abdm_calls_gateway("v3/hip/health-information/request", {**request, "transactionId": "txn-2"}, "r-h2")
    res = w.imedicus_answers("health-information/on-request", {"transactionId": "txn-2", "requestId": "r-h2"})
    assert res.status_code == 403
    assert res.get_json()["message"]["error"]["details"]["reason"] == "REVOKED"
    assert len(w.abdm_got("hi")) == 1


def test_an_expired_consent_is_refused_end_to_end(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(erase_at="2020-01-01T00:00:00.000Z"),
                         "r-c")
    w.abdm_calls_gateway("v3/hip/health-information/request", {"transactionId": TXN, "hiRequest": {
        "consent": {"id": CONSENT_ID}}}, "r-h")
    res = w.imedicus_answers("health-information/on-request", {"transactionId": TXN, "requestId": "r-h"})
    assert res.status_code == 403 and res.get_json()["message"]["error"]["details"]["reason"] == "EXPIRED"
    assert w.abdm_got("hi") == []
