"""
M3 (HIU) contract tests: consent request, consent notification acknowledgement, consent fetch, health-information
request. Each pins ABDM's path, headers and body of the Milestone 3 Postman collection and the bodies i-medicus sends.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import responses

from src.app.models import CallbackEvent, HipReply, OutboxJob
from src.app.services import crypto_service
from tests.conftest import GATEWAY, HIP_ID, make_jwt

HIECM = f"{GATEWAY}/api/hiecm"
SESSION = f"{HIECM}/gateway/v3/sessions"
CONSENT_INIT = f"{HIECM}/consent/v3/request/init"
CONSENT_ON_NOTIFY = f"{HIECM}/consent/v3/request/hiu/on-notify"
CONSENT_FETCH = f"{HIECM}/consent/v3/fetch"
HI_REQUEST = f"{HIECM}/data-flow/v3/health-information/request"
REQ = "6f1c2a9e-1111-4222-8333-444455556666"
CONSENT_ID = "ed486d33-3efa-4b8e-9a5c-47359467c20d"


@pytest.fixture
def abdm_up(jwks):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    for url in (CONSENT_INIT, CONSENT_ON_NOTIFY, CONSENT_FETCH, HI_REQUEST):
        jwks.add(responses.POST, url, json={}, status=202)
    return jwks


def sent(mock, url):
    calls = [c for c in mock.calls if c.request.url == url]
    assert len(calls) == 1, f"expected 1 call to {url}, got {len(calls)}"
    return calls[0].request, json.loads(calls[0].request.body)


def post(client, auth, path, body):
    return client.post(f"/api/v1/hiu/{path}", json=body, headers=auth)


def iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def init_body(**changes):
    """What ABDMService::consentInit sends."""
    body = {"refUri": "www.abdm.gov.in", "patientId": "ravi@sbx", "requesterName": "Dr. Asha Verma",
            "requesterType": "REGN", "requesterValue": "MH101", "fromDate": "2026-10-01T00:00:00.000Z",
            "toDate": "2026-11-01T00:00:00.000Z", "dataEraseAt": iso(30), "hiType": ["Prescription", "OPConsultation"],
            "purposeOfRequest": "CAREMGT", "userProfileId": 1, "hipId": HIP_ID, "requestId": REQ}
    body.update(changes)
    return body


# ---- consent init ---------------------------------------------------------------------------------------------------

def test_consent_init_sends_abdms_v3_body_with_the_request_id_i_medicus_keeps(client, abdm_up, hip, auth):
    res = post(client, auth, "consent/init", init_body())
    assert res.status_code == 202
    assert res.get_json()["data"] == {"hipId": HIP_ID, "requestId": REQ, "status": "requested"}

    request, body = sent(abdm_up, CONSENT_INIT)
    # REQUEST-ID is the id i-medicus stored, so the on-init callback finds its consent request row
    assert request.headers["REQUEST-ID"] == REQ and request.headers["X-HIU-ID"] == HIP_ID
    assert request.headers["X-CM-ID"] == "sbx" and request.headers["Authorization"] == "Bearer gw-token"
    consent = body["consent"]
    assert consent["purpose"] == {"text": "Care Management", "code": "CAREMGT", "refUri": "www.abdm.gov.in"}
    assert consent["patient"] == {"id": "ravi@sbx"} and consent["hiu"] == {"id": HIP_ID}
    assert consent["hip"] is None and consent["careContexts"] is None
    assert consent["requester"] == {"name": "Dr. Asha Verma", "identifier": {
        "type": "REGN", "value": "MH101", "system": "https://www.mciindia.org"}}
    assert consent["hiTypes"] == ["Prescription", "OPConsultation"]
    permission = consent["permission"]
    assert permission["accessMode"] == "VIEW"
    assert permission["dateRange"] == {"from": "2026-10-01T00:00:00.000Z", "to": "2026-11-01T00:00:00.000Z"}
    assert permission["dataEraseAt"].endswith(".000Z")  # i-medicus' microseconds are normalised to ABDM's format
    assert permission["frequency"] == {"unit": "HOUR", "value": 0, "repeats": 0}


def test_the_purpose_can_be_its_code_or_its_text(client, abdm_up, hip, auth):
    post(client, auth, "consent/init", init_body(purposeOfRequest="Break the Glass"))
    assert sent(abdm_up, CONSENT_INIT)[1]["consent"]["purpose"]["code"] == "BTG"


def test_a_request_id_is_made_when_the_client_sends_none(client, abdm_up, hip, auth):
    body = init_body()
    del body["requestId"]
    res = post(client, auth, "consent/init", body)
    request, _ = sent(abdm_up, CONSENT_INIT)
    assert res.get_json()["data"]["requestId"] == request.headers["REQUEST-ID"]


@pytest.mark.parametrize("changes,field", [
    ({"patientId": "not an address"}, "abhaAddress"), ({"purposeOfRequest": "FUN"}, "purposeOfRequest"),
    ({"purposeOfRequest": None}, "purposeOfRequest"), ({"hiType": []}, "hiType"), ({"hiType": "x"}, None),
    ({"fromDate": "yesterday"}, "fromDate"), ({"toDate": None}, "toDate"),
    ({"fromDate": "2026-12-01T00:00:00Z"}, "fromDate"),
    ({"dataEraseAt": "2020-01-01T00:00:00Z"}, "dataEraseAt"), ({"requesterName": None}, "requesterName"),
    ({"requesterValue": None}, "requesterValue"), ({"requestId": "x y"}, "requestId"),
])
def test_consent_init_is_validated_before_anything_goes_to_abdm(client, abdm_up, hip, auth, changes, field):
    res = post(client, auth, "consent/init", init_body(**changes))
    if field is None:                       # "x" is a valid one-element list of types
        assert res.status_code == 202
        return
    assert res.status_code == 400 and res.get_json()["message"]["error"]["details"]["field"] == field
    assert [c for c in abdm_up.calls if c.request.url == CONSENT_INIT] == []


def test_consent_init_is_never_queued_when_abdm_is_down(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, CONSENT_INIT, json={}, status=503)
    res = post(client, auth, "consent/init", init_body())
    assert res.status_code == 502 and OutboxJob.query.filter_by(kind="abdm_call").count() == 0


def test_a_facility_that_is_not_the_clients_is_404(client, abdm_up, hip, auth):
    res = post(client, auth, "consent/init", init_body(hipId="IN9999999999"))
    assert res.status_code == 404 and res.get_json()["message"]["error"]["code"] == "HIP_NOT_FOUND"


# ---- consent notification: the callback, then the answer ------------------------------------------------------------

NOTIFY = {"notification": {"status": "GRANTED", "consentRequestId": "creq-1", "consentArtefacts": [{"id": CONSENT_ID}]}}


def abdm_calls_gateway(client, path, body, request_id=REQ, headers=None):
    res = client.post(f"/api/{path}", data=json.dumps(body), headers={
        "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "Content-Type": "application/json",
        **(headers or {"X-HIU-ID": HIP_ID})})
    assert res.status_code == 202
    return res


def test_a_hiu_callback_is_routed_by_x_hiu_id(client, jwks, hip):
    abdm_calls_gateway(client, "v3/hiu/consent/request/notify", NOTIFY)
    event = CallbackEvent.query.one()
    assert event.hip_id == HIP_ID and event.api_client_id == hip.api_client_id and event.status == "received"


def test_a_hiu_callback_without_a_header_is_routed_by_the_hiu_in_its_body(client, jwks, hip):
    body = {"notification": {**NOTIFY["notification"], "consentDetail": {"hiu": {"id": HIP_ID}}}}
    abdm_calls_gateway(client, "v3/hiu/consent/request/notify", body, headers={})
    assert CallbackEvent.query.one().hip_id == HIP_ID


def test_consent_on_notify_answers_the_callback_with_abdms_body(client, abdm_up, hip, auth):
    abdm_calls_gateway(client, "v3/hiu/consent/request/notify", NOTIFY)
    res = post(client, auth, "consent/on-notify", {
        "acknowledgement": [{"status": "ok", "consentId": CONSENT_ID}], "response": {"requestId": REQ}, "hipId": HIP_ID})
    assert res.status_code == 200 and res.get_json()["data"] == {"status": "sent", "hipId": HIP_ID}
    request, body = sent(abdm_up, CONSENT_ON_NOTIFY)
    assert request.headers["X-HIU-ID"] == HIP_ID and "X-HIP-ID" not in request.headers
    assert body == {"acknowledgement": [{"status": "OK", "consentId": CONSENT_ID}], "response": {"requestId": REQ}}


def test_a_second_consent_answer_is_not_sent_again(client, abdm_up, hip, auth):
    abdm_calls_gateway(client, "v3/hiu/consent/request/notify", NOTIFY)
    body = {"acknowledgement": [{"status": "OK", "consentId": CONSENT_ID}], "response": {"requestId": REQ}}
    post(client, auth, "consent/on-notify", body)
    again = post(client, auth, "consent/on-notify", body)
    assert again.get_json()["data"]["status"] == "already_sent"
    assert len([c for c in abdm_up.calls if c.request.url == CONSENT_ON_NOTIFY]) == 1
    assert HipReply.query.filter_by(kind="hiu_consent_on_notify").count() == 1


def test_consent_on_notify_for_an_unknown_callback_is_404(client, abdm_up, hip, auth):
    res = post(client, auth, "consent/on-notify", {"acknowledgement": [{"status": "OK", "consentId": CONSENT_ID}],
                                                   "response": {"requestId": "never-seen"}})
    assert res.status_code == 404 and res.get_json()["message"]["error"]["code"] == "CALLBACK_NOT_FOUND"


def test_consent_on_notify_needs_the_acknowledged_consents(client, abdm_up, hip, auth):
    abdm_calls_gateway(client, "v3/hiu/consent/request/notify", NOTIFY)
    res = post(client, auth, "consent/on-notify", {"response": {"requestId": REQ}})
    assert res.status_code == 400 and res.get_json()["message"]["error"]["details"]["field"] == "acknowledgement"


def test_consent_on_notify_is_queued_when_abdm_is_down(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, CONSENT_ON_NOTIFY, json={}, status=503)
    abdm_calls_gateway(client, "v3/hiu/consent/request/notify", NOTIFY)
    res = post(client, auth, "consent/on-notify", {"acknowledgement": [{"status": "OK", "consentId": CONSENT_ID}],
                                                   "response": {"requestId": REQ}})
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "queued"
    assert OutboxJob.query.filter_by(kind="abdm_call").count() == 1


# ---- consent fetch --------------------------------------------------------------------------------------------------

def test_consent_fetch_sends_the_consent_id_for_the_hiu(client, abdm_up, hip, auth):
    res = post(client, auth, "consent/fetch", {"consentId": CONSENT_ID, "hipId": HIP_ID})
    assert res.status_code == 200 and res.get_json()["data"] == {"hipId": HIP_ID, "status": "sent"}
    request, body = sent(abdm_up, CONSENT_FETCH)
    assert body == {"consentId": CONSENT_ID} and request.headers["X-HIU-ID"] == HIP_ID


def test_consent_fetch_needs_a_consent_id_and_is_queued_when_abdm_is_down(client, jwks, hip, auth):
    assert post(client, auth, "consent/fetch", {"hipId": HIP_ID}).status_code == 400
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, CONSENT_FETCH, json={}, status=503)
    res = post(client, auth, "consent/fetch", {"consentId": CONSENT_ID, "hipId": HIP_ID})
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "queued"


# ---- consent request status -----------------------------------------------------------------------------------------

CONSENT_STATUS = f"{HIECM}/consent/v3/request/status"


def test_consent_status_sends_the_consent_request_id_for_the_hiu(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, CONSENT_STATUS, json={}, status=202)
    res = post(client, auth, "consent/status", {"consentRequestId": REQ, "hipId": HIP_ID})
    assert res.status_code == 200 and res.get_json()["data"] == {"hipId": HIP_ID, "status": "sent"}
    request, body = sent(jwks, CONSENT_STATUS)
    assert body == {"consentRequestId": REQ} and request.headers["X-HIU-ID"] == HIP_ID
    assert request.headers["X-CM-ID"] == "sbx" and request.headers["Authorization"] == "Bearer gw-token"


def test_consent_status_needs_the_request_id_and_is_queued_when_abdm_is_down(client, jwks, hip, auth):
    assert post(client, auth, "consent/status", {"hipId": HIP_ID}).status_code == 400
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, CONSENT_STATUS, json={}, status=503)
    res = post(client, auth, "consent/status", {"consentRequestId": REQ, "hipId": HIP_ID})
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "queued"


# ---- health-information request -------------------------------------------------------------------------------------

def hi_body(**changes):
    """What onConsentFetch sends (SendHealthInformationRequest)."""
    keys = crypto_service.new_keys()
    body = {"consentId": CONSENT_ID, "fromDate": "2026-10-01T00:00:00.000Z", "toDate": "2026-11-01T00:00:00.000Z",
            "dataPushUrl": "http://13.201.109.37:8081/api/abdm-webhook", "expiryDate": iso(1),
            "publicKey": keys["publicKey"], "nonce": keys["nonce"], "hipId": HIP_ID, "requestId": REQ}
    body.update(changes)
    return body


def test_the_health_information_request_carries_our_key_and_push_url(client, abdm_up, hip, auth):
    body = hi_body()
    res = post(client, auth, "health-information/request", body)
    assert res.status_code == 202 and res.get_json()["data"]["requestId"] == REQ
    request, sent_body = sent(abdm_up, HI_REQUEST)
    assert request.headers["REQUEST-ID"] == REQ and request.headers["X-HIU-ID"] == HIP_ID
    hi = sent_body["hiRequest"]
    assert hi["consent"] == {"id": CONSENT_ID}
    assert hi["dateRange"] == {"from": "2026-10-01T00:00:00.000Z", "to": "2026-11-01T00:00:00.000Z"}
    assert hi["dataPushUrl"] == "http://13.201.109.37:8081/api/abdm-webhook"
    assert hi["keyMaterial"] == {"cryptoAlg": "ECDH", "curve": "Curve25519", "nonce": body["nonce"],
                                 "dhPublicKey": {"expiry": hi["keyMaterial"]["dhPublicKey"]["expiry"],
                                                 "parameters": "Curve25519/32byte random key",
                                                 "keyValue": body["publicKey"]}}
    assert hi["keyMaterial"]["dhPublicKey"]["expiry"].endswith(".000Z")


@pytest.mark.parametrize("changes,field", [
    ({"consentId": None}, "consentId"), ({"fromDate": None}, "fromDate"), ({"dataPushUrl": None}, "dataPushUrl"),
    ({"dataPushUrl": "ftp://x.test/y"}, "dataPushUrl"), ({"dataPushUrl": "https://u:p@x.test/y"}, "dataPushUrl"),
    ({"expiryDate": "2020-01-01T00:00:00Z"}, "expiryDate"), ({"expiryDate": None}, "expiryDate"),
    ({"publicKey": None}, "publicKey"), ({"nonce": None}, "nonce"),
])
def test_the_health_information_request_is_validated(client, abdm_up, hip, auth, changes, field):
    res = post(client, auth, "health-information/request", hi_body(**changes))
    assert res.status_code == 400 and res.get_json()["message"]["error"]["details"]["field"] == field
    assert [c for c in abdm_up.calls if c.request.url == HI_REQUEST] == []


@pytest.mark.parametrize("changes", [{"publicKey": "bm90IGEga2V5"}, {"nonce": "AAAA"}])
def test_a_bad_key_or_nonce_is_refused(client, abdm_up, hip, auth, changes):
    res = post(client, auth, "health-information/request", hi_body(**changes))
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "INVALID_KEY_MATERIAL"


def test_the_health_information_request_is_never_queued(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, HI_REQUEST, json={}, status=503)
    res = post(client, auth, "health-information/request", hi_body())
    assert res.status_code == 502 and OutboxJob.query.filter_by(kind="abdm_call").count() == 0


@pytest.mark.parametrize("path", ["consent/init", "consent/on-notify", "consent/fetch", "health-information/request"])
def test_every_hiu_route_needs_authentication(client, path):
    assert client.post(f"/api/v1/hiu/{path}", json={}).status_code == 401
