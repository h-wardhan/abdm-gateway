"""
M2 (HIP) contract tests, Days 9-11: user-initiated linking answers, HIP-initiated linking, consent
acknowledgement and health-information acknowledgement with the consent check.
Each test pins the ABDM path, headers and body of ABDM's Milestone 2 Postman collection, and the shape i-medicus
sends / expects (HTTP codes and the top-level `status: success`).
"""
import json
from datetime import timedelta

import pytest
import responses

from src.app.extensions import db
from src.app.models import HipConsent, HipReply, OutboxJob
from src.app.models.base import utcnow
from tests.conftest import GATEWAY, HIP_ID, make_jwt

SESSION = f"{GATEWAY}/api/hiecm/gateway/v3/sessions"
HIECM = f"{GATEWAY}/api/hiecm"
ON_DISCOVER = f"{HIECM}/user-initiated-linking/v3/patient/care-context/on-discover"
ON_INIT = f"{HIECM}/user-initiated-linking/v3/link/care-context/on-init"
ON_CONFIRM = f"{HIECM}/user-initiated-linking/v3/link/care-context/on-confirm"
GEN_TOKEN = f"{HIECM}/v3/token/generate-token"
LINK = f"{HIECM}/hip/v3/link/carecontext"
LINK_NOTIFY = f"{HIECM}/hip/v3/link/context/notify"
SMS = f"{HIECM}/hip/v3/link/patient/links/sms/notify2"
CONSENT_ACK = f"{HIECM}/consent/v3/request/hip/on-notify"
HI_ACK = f"{HIECM}/data-flow/v3/health-information/hip/on-request"
ALL_ABDM = [ON_DISCOVER, ON_INIT, ON_CONFIRM, GEN_TOKEN, LINK, LINK_NOTIFY, SMS, CONSENT_ACK, HI_ACK]

REQ = "req-0001-aaaa"
TXN = "a1b2c3d4-0000-1111-2222-333344445555"
CONSENT_ID = "ed486d33-3efa-4b8e-9a5c-47359467c20d"
PATIENT = [{"referenceNumber": "P-1", "display": "Ravi", "hiType": "Prescription", "count": 1,
            "careContexts": [{"referenceNumber": "CC-1", "display": "Visit 1"}]}]


@pytest.fixture
def abdm_up(jwks):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    for url in ALL_ABDM:
        jwks.add(responses.POST, url, json={}, status=202)
    return jwks


def _calls(mock, url):
    return [c for c in mock.calls if c.request.url == url]


def _sent(mock, url):
    calls = _calls(mock, url)
    assert len(calls) == 1, f"expected 1 call to {url}, got {len(calls)}"
    return calls[0].request, json.loads(calls[0].request.body)


def _callback(client, path, body, request_id=REQ, hip_id=HIP_ID):
    """ABDM calls the gateway."""
    res = client.post(f"/api/{path}", data=json.dumps(body), headers={
        "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "X-HIP-ID": hip_id,
        "Content-Type": "application/json"})
    assert res.status_code == 202
    return res


def _post(client, auth, path, body, **headers):
    return client.post(f"/api/v1/hip/{path}", json=body, headers={**auth, **headers})


def _ok_i_medicus_shape(res, status=200):
    assert res.status_code == status
    body = res.get_json()
    assert body["success"] is True and body["status"] == "success"
    return body["data"]


# ---- Day 9: user-initiated linking --------------------------------------------------------------------------------

def test_on_discover_sends_the_v3_body_for_the_clinic_of_the_callback(client, abdm_up, hip, auth):
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN, "patient": {"id": "ravi@sbx"}})
    res = _post(client, auth, "care-context/on-discover", {
        "transactionId": TXN, "patient": PATIENT, "matchedBy": ["MR"], "response": {"requestId": REQ}})
    assert _ok_i_medicus_shape(res) == {"status": "sent", "hipId": HIP_ID}
    req, sent = _sent(abdm_up, ON_DISCOVER)
    assert req.headers["X-HIP-ID"] == HIP_ID and req.headers["Authorization"] == "Bearer gw-token"
    assert req.headers["X-CM-ID"] == "sbx" and req.headers["REQUEST-ID"] and req.headers["TIMESTAMP"]
    assert sent == {"transactionId": TXN, "patient": PATIENT, "matchedBy": ["MR"], "response": {"requestId": REQ}}


def test_on_discover_defaults_matched_by_and_fills_display_and_count(client, abdm_up, hip, auth):
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    bare = [{"referenceNumber": "P-1", "hiType": "Invoice", "careContexts": [{"referenceNumber": "CC-1"}]}]
    _post(client, auth, "care-context/on-discover", {"transactionId": TXN, "patient": bare, "requestId": REQ})
    _, sent = _sent(abdm_up, ON_DISCOVER)
    assert sent["matchedBy"] == ["MR"]
    assert sent["patient"][0]["display"] == "P-1" and sent["patient"][0]["count"] == 1
    assert sent["patient"][0]["careContexts"][0]["display"] == "CC-1"


def test_on_init_sends_the_link(client, abdm_up, hip, auth):
    _callback(client, "v3/link/care-context/init", {"transactionId": TXN})
    link = {"referenceNumber": "link-ref-1", "authenticationType": "DIRECT",
            "meta": {"communicationMedium": "MOBILE", "communicationHint": "OTP",
                     "communicationExpiry": "2026-11-03T10:05:00.000Z"}}
    res = _post(client, auth, "link/on-init", {"transactionId": TXN, "link": link, "response": {"requestId": REQ}})
    assert _ok_i_medicus_shape(res)["status"] == "sent"
    req, sent = _sent(abdm_up, ON_INIT)
    assert req.headers["X-HIP-ID"] == HIP_ID
    assert sent == {"transactionId": TXN, "link": link, "response": {"requestId": REQ}}


def test_on_confirm_accepts_the_capital_patient_key_i_medicus_sends(client, abdm_up, hip, auth):
    _callback(client, "v3/link/care-context/confirm", {"confirmation": {"linkRefNumber": "x", "token": "123456"}})
    res = _post(client, auth, "link/on-confirm", {"Patient": PATIENT, "response": {"requestId": REQ}})
    assert _ok_i_medicus_shape(res)["status"] == "sent"
    _, sent = _sent(abdm_up, ON_CONFIRM)
    assert sent == {"patient": PATIENT, "response": {"requestId": REQ}}


@pytest.mark.parametrize("path,body,field", [
    ("care-context/on-discover", {"patient": PATIENT, "requestId": REQ}, "transactionId"),
    ("care-context/on-discover", {"transactionId": TXN, "patient": [], "requestId": REQ}, "patient"),
    ("care-context/on-discover", {"transactionId": TXN, "requestId": REQ}, "patient"),
    ("care-context/on-discover", {"transactionId": TXN, "requestId": REQ,
                                  "patient": [{"referenceNumber": "P", "hiType": "X", "careContexts": []}]},
     "careContexts"),
    ("care-context/on-discover", {"transactionId": TXN, "requestId": REQ,
                                  "patient": [{"hiType": "X", "careContexts": [{"referenceNumber": "c"}]}]},
     "referenceNumber"),
    ("care-context/on-discover", {"transactionId": TXN, "requestId": REQ,
                                  "patient": [{"referenceNumber": "P", "careContexts": [{"referenceNumber": "c"}]}]},
     "hiType"),
    ("care-context/on-discover", {"transactionId": TXN, "patient": PATIENT}, "requestId"),
    ("link/on-init", {"transactionId": TXN, "requestId": REQ}, "link"),
    ("link/on-init", {"transactionId": TXN, "requestId": REQ, "link": {"authenticationType": "DIRECT"}},
     "link.referenceNumber"),
    ("link/on-init", {"transactionId": TXN, "requestId": REQ, "link": {"referenceNumber": "r"}},
     "link.authenticationType"),
    ("link/on-init", {"requestId": REQ, "link": {"referenceNumber": "r", "authenticationType": "DIRECT"}},
     "transactionId"),
    ("link/on-confirm", {"requestId": REQ}, "patient"),
])
def test_invalid_answers_are_rejected_without_calling_abdm(client, abdm_up, hip, auth, path, body, field):
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    res = _post(client, auth, path, body)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"]["field"] == field
    assert all(_calls(abdm_up, u) == [] for u in ALL_ABDM)


@pytest.mark.parametrize("path,body", [
    ("care-context/on-discover", {"transactionId": TXN, "patient": PATIENT}),
    ("link/on-init", {"transactionId": TXN, "link": {"referenceNumber": "r", "authenticationType": "DIRECT"}}),
    ("link/on-confirm", {"patient": PATIENT}),
    ("consent/on-notify", {"consentId": CONSENT_ID}),
    ("health-information/on-request", {"transactionId": TXN}),
])
def test_unknown_request_id_is_a_404(client, abdm_up, hip, auth, path, body):
    res = _post(client, auth, path, {**body, "requestId": "never-seen-0001"})
    assert res.status_code == 404
    assert res.get_json()["message"]["error"]["code"] == "CALLBACK_NOT_FOUND"
    assert all(_calls(abdm_up, u) == [] for u in ALL_ABDM)


def test_a_client_cannot_answer_another_clients_callback(client, abdm_up, hip, other_auth):
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    res = _post(client, other_auth, "care-context/on-discover",
                {"transactionId": TXN, "patient": PATIENT, "requestId": REQ})
    assert res.status_code == 404 and _calls(abdm_up, ON_DISCOVER) == []


def test_the_second_answer_to_the_same_callback_is_not_sent_again(client, abdm_up, hip, auth):
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    body = {"transactionId": TXN, "patient": PATIENT, "requestId": REQ}
    assert _ok_i_medicus_shape(_post(client, auth, "care-context/on-discover", body))["status"] == "sent"
    assert _ok_i_medicus_shape(_post(client, auth, "care-context/on-discover", body))["status"] == "already_sent"
    assert len(_calls(abdm_up, ON_DISCOVER)) == 1 and HipReply.query.count() == 1


def test_different_answers_to_one_callback_are_independent(client, abdm_up, hip, auth):
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    _post(client, auth, "care-context/on-discover", {"transactionId": TXN, "patient": PATIENT, "requestId": REQ})
    res = _post(client, auth, "consent/on-notify", {"consentId": CONSENT_ID, "requestId": REQ})
    assert res.get_json()["data"]["status"] == "sent"


def test_abdm_down_queues_the_answer_and_a_duplicate_is_not_queued_twice(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, ON_DISCOVER, json={"error": "down"}, status=503)
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    body = {"transactionId": TXN, "patient": PATIENT, "requestId": REQ}
    data = _ok_i_medicus_shape(_post(client, auth, "care-context/on-discover", body))
    assert data["status"] == "queued" and data["hipId"] == HIP_ID
    job = OutboxJob.query.filter_by(kind="abdm_call").one()
    assert job.id == data["jobId"] and job.status == "pending" and job.api_client_id == hip.api_client_id
    assert _post(client, auth, "care-context/on-discover", body).get_json()["data"]["status"] == "already_sent"
    assert OutboxJob.query.filter_by(kind="abdm_call").count() == 1


def test_abdm_rejecting_an_answer_is_an_error_and_can_be_tried_again(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, ON_DISCOVER, json={"error": {"code": 1401, "message": "bad"}}, status=400)
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    body = {"transactionId": TXN, "patient": PATIENT, "requestId": REQ}
    assert _post(client, auth, "care-context/on-discover", body).status_code == 400
    assert HipReply.query.count() == 0 and OutboxJob.query.filter_by(kind="abdm_call").count() == 0


def test_each_clinic_is_answered_with_its_own_id(client, abdm_up, hip, auth, api_client):
    from src.app.callbacks import registry
    tenant, _ = api_client
    registry.upsert(tenant, "IN0999999999", "https://client.example.test/hook", "Second clinic")
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN}, "req-clinic-2", "IN0999999999")
    _post(client, auth, "care-context/on-discover", {"transactionId": TXN, "patient": PATIENT,
                                                    "requestId": "req-clinic-2"})
    assert _sent(abdm_up, ON_DISCOVER)[0].headers["X-HIP-ID"] == "IN0999999999"


# ---- Day 10: HIP-initiated linking --------------------------------------------------------------------------------

def test_generate_token_sends_the_patient_to_abdm_with_the_clinic_header(client, abdm_up, hip, auth):
    res = _post(client, auth, "link-token", {
        "abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "name": "Ravi Kumar", "gender": "M",
        "yearOfBirth": 1989, "hipid": HIP_ID})
    assert _ok_i_medicus_shape(res, 202) == {"hipId": HIP_ID}
    req, sent = _sent(abdm_up, GEN_TOKEN)
    assert req.headers["X-HIP-ID"] == HIP_ID and req.headers["X-CM-ID"] == "sbx"
    assert sent == {"abhaNumber": 91536782361862, "abhaAddress": "ravi@sbx", "name": "Ravi Kumar", "gender": "M",
                    "yearOfBirth": 1989}


def test_generate_token_accepts_a_dashed_abha_number_and_string_year(client, abdm_up, hip, auth):
    _post(client, auth, "link-token", {"abhaNumber": "91-5367-8236-1862", "abhaAddress": "ravi@sbx", "name": "R",
                                       "gender": "M", "yearOfBirth": "1989", "hipid": HIP_ID})
    sent = _sent(abdm_up, GEN_TOKEN)[1]
    assert sent["abhaNumber"] == 91536782361862 and sent["yearOfBirth"] == 1989


LINK_BODY = {"hipid": HIP_ID, "linkToken": "tok-1", "singlecarecontext": {
    "abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "patient": PATIENT}}


def test_link_care_context_sends_the_link_token_and_patient_list(client, abdm_up, hip, auth):
    res = _post(client, auth, "link/care-context", LINK_BODY, **{"X-Link-Token": "tok-header"})
    assert _ok_i_medicus_shape(res, 202) == {"hipId": HIP_ID}
    req, sent = _sent(abdm_up, LINK)
    assert req.headers["X-LINK-TOKEN"] == "tok-header" and req.headers["X-HIP-ID"] == HIP_ID
    assert sent == {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "patient": PATIENT}


def test_link_care_context_multiple_patients_and_body_token(client, abdm_up, hip, auth):
    second = [{**PATIENT[0], "referenceNumber": "P-2"}]
    body = {"hipid": HIP_ID, "linkToken": "tok-body", "abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx",
            "patient": PATIENT + second}
    _post(client, auth, "link/care-context", body)
    req, sent = _sent(abdm_up, LINK)
    assert req.headers["X-LINK-TOKEN"] == "tok-body"
    assert [p["referenceNumber"] for p in sent["patient"]] == ["P-1", "P-2"]


def test_link_notify_builds_the_notification(client, abdm_up, hip, auth):
    res = _post(client, auth, "link/notify", {
        "hipid": HIP_ID, "linkToken": "tok-1", "abhaAddress": "ravi@sbx", "hiType": ["Prescription"],
        "patientReference": "P-1", "careContextReference": "CC-1"})
    assert _ok_i_medicus_shape(res, 201) == {"hipId": HIP_ID}
    req, sent = _sent(abdm_up, LINK_NOTIFY)
    assert req.headers["X-LINK-TOKEN"] == "tok-1" and req.headers["X-HIP-ID"] == HIP_ID
    n = sent["notification"]
    assert n["patient"] == {"id": "ravi@sbx"} and n["hip"] == {"id": HIP_ID} and n["hiTypes"] == ["Prescription"]
    assert n["careContext"] == {"patientReference": "P-1", "careContextReference": "CC-1"}
    assert n["date"].endswith("Z")


def test_link_notify_accepts_a_single_hi_type_string(client, abdm_up, hip, auth):
    _post(client, auth, "link/notify", {"hipid": HIP_ID, "linkToken": "t", "abhaAddress": "ravi@sbx",
                                        "hiType": "Invoice", "patientReference": "P", "careContextReference": "C"})
    assert _sent(abdm_up, LINK_NOTIFY)[1]["notification"]["hiTypes"] == ["Invoice"]


def test_sms_notify_sends_the_phone_and_the_clinic(client, abdm_up, hip, auth):
    res = _post(client, auth, "sms/notify", {"phoneNumber": "+919876543210", "name": "Ravi", "hipId": HIP_ID})
    assert _ok_i_medicus_shape(res) == {"hipId": HIP_ID}
    req, sent = _sent(abdm_up, SMS)
    assert "X-HIP-ID" not in req.headers
    assert sent["notification"] == {"phoneNo": "9876543210", "hip": {"name": "Test clinic", "id": HIP_ID}}
    assert sent["requestId"] and sent["timestamp"].endswith("Z")


@pytest.mark.parametrize("path,body,field", [
    ("link-token", {"abhaAddress": "ravi@sbx", "name": "R", "gender": "M", "yearOfBirth": 1989, "hipid": HIP_ID,
                    "abhaNumber": "123"}, "abhaNumber"),
    ("link-token", {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "gender": "M", "yearOfBirth": 1989,
                    "hipid": HIP_ID}, "name"),
    ("link-token", {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "name": "R", "yearOfBirth": 1989,
                    "hipid": HIP_ID}, "gender"),
    ("link-token", {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "name": "R", "gender": "M",
                    "hipid": HIP_ID}, "yearOfBirth"),
    ("link-token", {"abhaNumber": "91536782361862", "name": "R", "gender": "M", "yearOfBirth": 1989,
                    "hipid": HIP_ID}, "abhaAddress"),
    ("link/care-context", {**LINK_BODY, "singlecarecontext": {"abhaNumber": "91536782361862",
                                                              "abhaAddress": "ravi@sbx", "patient": []}}, "patient"),
    ("link/notify", {"hipid": HIP_ID, "linkToken": "t", "abhaAddress": "ravi@sbx", "patientReference": "P",
                     "careContextReference": "C"}, "hiType"),
    ("link/notify", {"hipid": HIP_ID, "linkToken": "t", "abhaAddress": "ravi@sbx", "hiType": ["X"],
                     "careContextReference": "C"}, "patientReference"),
    ("link/notify", {"hipid": HIP_ID, "linkToken": "t", "abhaAddress": "ravi@sbx", "hiType": ["X"],
                     "patientReference": "P"}, "careContextReference"),
    ("sms/notify", {"hipId": HIP_ID, "phoneNumber": "123"}, "mobileNumber"),
    ("sms/notify", {"hipId": HIP_ID}, "mobileNumber"),
])
def test_invalid_calls_we_start_are_rejected_without_calling_abdm(client, abdm_up, hip, auth, path, body, field):
    res = _post(client, auth, path, body)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"]["field"] == field
    assert all(_calls(abdm_up, u) == [] for u in ALL_ABDM)


@pytest.mark.parametrize("path,body", [
    ("link/care-context", {k: v for k, v in LINK_BODY.items() if k != "linkToken"}),
    ("link/notify", {"hipid": HIP_ID, "abhaAddress": "ravi@sbx", "hiType": ["X"], "patientReference": "P",
                     "careContextReference": "C"}),
])
def test_link_token_is_required_for_linking_and_notifying(client, abdm_up, hip, auth, path, body):
    res = _post(client, auth, path, body)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "MISSING_LINK_TOKEN"


@pytest.mark.parametrize("path,body", [
    ("link-token", {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "name": "R", "gender": "M",
                    "yearOfBirth": 1989}),
    ("link/care-context", LINK_BODY),
    ("sms/notify", {"phoneNumber": "9876543210"}),
])
def test_calls_we_start_need_a_facility_of_the_client(client, abdm_up, hip, auth, other_auth, path, body):
    no_hip = {k: v for k, v in body.items() if k not in ("hipid", "hipId")}
    assert _post(client, auth, path, no_hip).status_code == 400  # no facility named
    assert _post(client, other_auth, path, {**no_hip, "hipid": HIP_ID}).status_code == 404  # not their facility
    assert _post(client, auth, path, {**no_hip, "hipid": "IN-UNKNOWN-1", "linkToken": "t"}).status_code == 404
    assert all(_calls(abdm_up, u) == [] for u in ALL_ABDM)


def test_facility_can_come_from_the_x_hip_id_header(client, abdm_up, hip, auth):
    body = {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "name": "R", "gender": "M", "yearOfBirth": 1989}
    assert _post(client, auth, "link-token", body, **{"X-HIP-ID": HIP_ID}).status_code == 202


def test_abdm_errors_of_calls_we_start_reach_the_caller_and_are_never_queued(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, GEN_TOKEN, json={"message": "Duplicate Link token request"}, status=409)
    jwks.add(responses.POST, LINK, json={"error": "down"}, status=503)
    res = _post(client, auth, "link-token", {"abhaNumber": "91536782361862", "abhaAddress": "ravi@sbx", "name": "R",
                                             "gender": "M", "yearOfBirth": 1989, "hipid": HIP_ID})
    assert res.status_code == 409
    assert "Duplicate Link token request" in res.get_json()["message"]["error"]["message"]
    assert _post(client, auth, "link/care-context", LINK_BODY).status_code == 502
    assert OutboxJob.query.filter_by(kind="abdm_call").count() == 0


# ---- Day 10: consent notification ----------------------------------------------------------------------------------

def test_consent_on_notify_for_the_i_medicus_body(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", {"notification": {"consentId": CONSENT_ID, "status": "GRANTED"}})
    res = _post(client, auth, "consent/on-notify", {"consentId": CONSENT_ID, "requestId": REQ, "hipId": HIP_ID})
    assert _ok_i_medicus_shape(res, 201)["status"] == "sent"
    req, sent = _sent(abdm_up, CONSENT_ACK)
    assert req.headers["X-HIP-ID"] == HIP_ID
    assert sent == {"acknowledgement": {"status": "OK", "consentId": CONSENT_ID}, "response": {"requestId": REQ}}


def test_consent_on_notify_for_abdm_shaped_body(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", {"notification": {"consentId": CONSENT_ID, "status": "GRANTED"}})
    _post(client, auth, "consent/on-notify", {"acknowledgement": {"status": "ok", "consentId": CONSENT_ID},
                                              "response": {"requestId": REQ}})
    assert _sent(abdm_up, CONSENT_ACK)[1]["acknowledgement"] == {"status": "OK", "consentId": CONSENT_ID}


def test_consent_on_notify_needs_a_consent_id(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", {"notification": {"consentId": CONSENT_ID, "status": "GRANTED"}})
    res = _post(client, auth, "consent/on-notify", {"requestId": REQ})
    assert res.status_code == 400 and res.get_json()["message"]["error"]["details"]["field"] == "consentId"


def _notify_body(consent_id=CONSENT_ID, status="GRANTED", date_from="2026-10-01T00:00:00.000Z",
                 date_to="2026-11-30T00:00:00.000Z", erase_at="2126-11-30T00:00:00.000Z", detail=True):
    notification = {"status": status, "consentId": consent_id}
    if detail:
        notification["consentDetail"] = {
            "consentId": consent_id, "patient": {"id": "ravi@sbx"}, "hip": {"id": HIP_ID},
            "careContexts": [{"patientReference": "P-1", "careContextReference": "CC-1"}],
            "hiTypes": ["Prescription"],
            "permission": {"accessMode": "VIEW", "dateRange": {"from": date_from, "to": date_to},
                           "dataEraseAt": erase_at}}
    return {"notification": notification}


def test_a_consent_notification_is_stored_when_it_arrives(client, jwks, hip):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    row = HipConsent.query.one()
    assert (row.consent_id, row.status, row.hip_id, row.hi_types) == (CONSENT_ID, "GRANTED", HIP_ID, ["Prescription"])
    assert row.care_contexts == [{"patientReference": "P-1", "careContextReference": "CC-1"}]
    assert row.api_client_id == hip.api_client_id and row.date_from and row.date_to and row.erase_at


def test_a_revoke_notification_updates_the_stored_consent_and_keeps_its_detail(client, jwks, hip):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    _callback(client, "v3/consent/request/hip/notify", _notify_body(status="REVOKED", detail=False), "req-revoke")
    row = HipConsent.query.one()
    assert row.status == "REVOKED" and row.hi_types == ["Prescription"] and row.date_to is not None


def test_a_malformed_consent_notification_is_still_accepted(client, jwks, hip):
    _callback(client, "v3/consent/request/hip/notify", {"notification": "oops"})
    assert HipConsent.query.count() == 0


# ---- Day 11: health-information request and the consent check -----------------------------------------------------

def _hi_request(client, consent_id=CONSENT_ID, date_from="2026-10-10T00:00:00.000Z", date_to="2026-11-01T00:00:00.000Z"):
    body = {"transactionId": TXN, "hiRequest": {"consent": {"id": consent_id},
                                                "dateRange": {"from": date_from, "to": date_to},
                                                "dataPushUrl": "https://hiu.example.test/push"}}
    _callback(client, "v3/hip/health-information/request", body, "req-hi-1")
    return body


def _ack(client, auth, request_id="req-hi-1"):
    return _post(client, auth, "health-information/on-request", {"transactionId": TXN, "requestId": request_id})


def test_a_valid_request_is_acknowledged(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    _hi_request(client)
    res = _ack(client, auth)
    assert _ok_i_medicus_shape(res)["status"] == "sent"
    req, sent = _sent(abdm_up, HI_ACK)
    assert req.headers["X-HIP-ID"] == HIP_ID
    assert sent == {"hiRequest": {"transactionId": TXN, "sessionStatus": "ACKNOWLEDGED"},
                    "response": {"requestId": "req-hi-1"}}


def test_the_transaction_id_may_come_from_the_callback(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    _hi_request(client)
    res = _post(client, auth, "health-information/on-request", {"requestId": "req-hi-1"})
    assert res.status_code == 200 and _sent(abdm_up, HI_ACK)[1]["hiRequest"]["transactionId"] == TXN


def test_a_duplicate_acknowledgement_is_not_sent_twice(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    _hi_request(client)
    _ack(client, auth)
    assert _ack(client, auth).get_json()["data"]["status"] == "already_sent"
    assert len(_calls(abdm_up, HI_ACK)) == 1


@pytest.mark.parametrize("notify,reason", [
    (_notify_body(status="REVOKED", detail=False), "REVOKED"),
    (_notify_body(status="DENIED"), "DENIED"),
    (_notify_body(erase_at="2020-01-01T00:00:00.000Z"), "EXPIRED"),
    (_notify_body(date_from="2026-10-20T00:00:00.000Z"), "OUT_OF_RANGE"),
    (_notify_body(date_to="2026-10-25T00:00:00.000Z"), "OUT_OF_RANGE"),
])
def test_a_consent_that_is_not_usable_is_refused_and_abdm_is_not_called(client, abdm_up, hip, auth, notify, reason):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())  # first granted ...
    _callback(client, "v3/consent/request/hip/notify", notify, "req-consent-2")  # ... then changed
    _hi_request(client)
    res = _ack(client, auth)
    assert res.status_code == 403
    error = res.get_json()["message"]["error"]
    assert error["code"] == "CONSENT_INVALID" and error["details"] == {"reason": reason, "consentId": CONSENT_ID}
    assert _calls(abdm_up, HI_ACK) == [] and HipReply.query.count() == 0


def test_an_unknown_consent_is_refused(client, abdm_up, hip, auth):
    _hi_request(client, consent_id="never-notified")
    res = _ack(client, auth)
    assert res.status_code == 403 and res.get_json()["message"]["error"]["details"]["reason"] == "NOT_FOUND"


def test_a_request_without_a_consent_id_is_refused(client, abdm_up, hip, auth):
    _callback(client, "v3/hip/health-information/request", {"transactionId": TXN, "hiRequest": {}}, "req-hi-1")
    assert _ack(client, auth).status_code == 403


def test_a_consent_stored_for_another_client_is_not_usable(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    row = HipConsent.query.one()
    row.api_client_id = hip.api_client_id + 1000
    db.session.commit()
    _hi_request(client)
    assert _ack(client, auth).status_code == 403


def test_a_consent_that_expires_after_it_was_stored_is_refused(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    _hi_request(client)
    row = HipConsent.query.one()
    row.erase_at = utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert _ack(client, auth).get_json()["message"]["error"]["details"]["reason"] == "EXPIRED"


def test_a_request_inside_the_range_with_no_dates_is_allowed(client, abdm_up, hip, auth):
    _callback(client, "v3/consent/request/hip/notify", _notify_body())
    body = {"transactionId": TXN, "hiRequest": {"consent": {"id": CONSENT_ID}}}
    _callback(client, "v3/hip/health-information/request", body, "req-hi-1")
    assert _ack(client, auth).status_code == 200


# ---- auth --------------------------------------------------------------------------------------------------------

ALL_ROUTES = ["care-context/on-discover", "link/on-init", "link/on-confirm", "link-token", "link/care-context",
              "link/notify", "sms/notify", "consent/on-notify", "health-information/on-request"]


@pytest.mark.parametrize("path", ALL_ROUTES)
def test_every_route_needs_a_client(client, path):
    assert client.post(f"/api/v1/hip/{path}", json={}).status_code == 401


def test_client_id_and_secret_headers_work_like_i_medicus_sends_them(client, abdm_up, hip, api_client):
    tenant, secret = api_client
    _callback(client, "v3/hip/patient/care-context/discover", {"transactionId": TXN})
    res = client.post("/api/v1/hip/care-context/on-discover",
                      json={"transactionId": TXN, "patient": PATIENT, "requestId": REQ},
                      headers={"Client-Id": tenant.client_id, "Client-Secret": secret,
                               "Authorization": "an-old-vcdoctor-token"})
    assert res.status_code == 200 and len(_calls(abdm_up, ON_DISCOVER)) == 1


def test_wrong_client_secret_is_rejected(client, hip, api_client):
    tenant, _ = api_client
    res = client.post("/api/v1/hip/care-context/on-discover", json={},
                      headers={"Client-Id": tenant.client_id, "Client-Secret": "wrong"})
    assert res.status_code == 401
