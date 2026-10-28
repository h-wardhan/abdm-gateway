"""Scan & Share answer (on-share): the clinic comes from the scan callback, never from the caller."""
import json

import pytest
import responses

from src.app.callbacks import registry
from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from tests.conftest import CALLBACK_URL, GATEWAY, HIP_ID, make_jwt

URL = "/api/v1/share/on-share"
ABDM_ON_SHARE = f"{GATEWAY}/api/hiecm/patient-share/v3/on-share"
SESSION = f"{GATEWAY}/api/hiecm/gateway/v3/sessions"
REQUEST_ID = "5f7a535d-a3fd-416b-b069-e6a0fc9b6f9d"


def _scan(client, hip_id=HIP_ID, request_id=REQUEST_ID, context="hip"):
    """ABDM calls the gateway: a patient scanned the QR of `hip_id`."""
    meta = {"hipId": hip_id}
    if context is not None:
        meta["context"] = context
    body = {"metaData": meta, "profile": {"patient": {"abhaAddress": "ravi@sbx", "name": "Ravi Kumar"}}}
    res = client.post("/api/v3/hip/patient/share", data=json.dumps(body), headers={
        "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "Content-Type": "application/json"})
    assert res.status_code == 202
    return res


def _answer(client, auth, **fields):
    data = {"requestId": REQUEST_ID, "abhaAddress": "ravi@sbx", "tokenNumber": "5", **fields}
    return client.post(URL, json={k: v for k, v in data.items() if v is not None}, headers=auth)


@pytest.fixture
def abdm_up(jwks):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, ABDM_ON_SHARE, json={}, status=202)
    return jwks


def _on_share_calls(http):
    return [c for c in http.calls if c.request.url == ABDM_ON_SHARE]


def test_requires_a_token(client):
    assert client.post(URL, json={}).status_code == 401


def test_sends_on_share_for_the_clinic_of_the_scan(client, abdm_up, hip, auth):
    _scan(client)
    res = _answer(client, auth)
    assert res.status_code == 200
    assert res.get_json()["data"] == {"status": "sent", "hipId": HIP_ID}

    [call] = _on_share_calls(abdm_up)
    assert call.request.headers["X-HIP-ID"] == HIP_ID
    assert call.request.headers["REQUEST-ID"] and call.request.headers["Authorization"] == "Bearer gw-token"
    assert json.loads(call.request.body) == {
        "acknowledgement": {"status": "SUCCESS", "abhaAddress": "ravi@sbx",
                            "profile": {"tokenNumber": "5", "expiry": "1800", "context": "hip"}},
        "response": {"requestId": REQUEST_ID},
    }


def test_each_clinic_is_answered_with_its_own_id(client, abdm_up, hip, auth, api_client):
    tenant, _ = api_client
    registry.upsert(tenant, "IN-CLINIC-B-01", CALLBACK_URL)
    _scan(client, HIP_ID, "req-clinic-a-0001")
    _scan(client, "IN-CLINIC-B-01", "req-clinic-b-0001")

    assert _answer(client, auth, requestId="req-clinic-b-0001", tokenNumber=2).status_code == 200
    assert _answer(client, auth, requestId="req-clinic-a-0001", tokenNumber=1).status_code == 200
    sent = [(c.request.headers["X-HIP-ID"], json.loads(c.request.body)["response"]["requestId"])
            for c in _on_share_calls(abdm_up)]
    assert sent == [("IN-CLINIC-B-01", "req-clinic-b-0001"), (HIP_ID, "req-clinic-a-0001")]


def test_token_number_may_be_a_number_and_expiry_can_be_set(client, abdm_up, hip, auth):
    _scan(client)
    assert _answer(client, auth, tokenNumber=12, expiry=30).status_code == 200
    profile = json.loads(_on_share_calls(abdm_up)[0].request.body)["acknowledgement"]["profile"]
    assert (profile["tokenNumber"], profile["expiry"]) == ("12", "30")


def test_snake_case_aliases(client, abdm_up, hip, auth):
    _scan(client)
    res = client.post(URL, json={"request_id": REQUEST_ID, "abha_address": "ravi@sbx", "token_number": "3"}, headers=auth)
    assert res.status_code == 200


def test_context_is_left_out_when_the_scan_had_none(client, abdm_up, hip, auth):
    _scan(client, context=None)
    _answer(client, auth)
    profile = json.loads(_on_share_calls(abdm_up)[0].request.body)["acknowledgement"]["profile"]
    assert "context" not in profile


def test_unreadable_scan_body_means_no_context(client, abdm_up, hip, auth):
    _scan(client)
    event = CallbackEvent.query.one()
    from src.app.callbacks import vault
    event.body_enc = vault.seal_text("[1, 2]")  # no metaData
    db.session.commit()
    assert _answer(client, auth).status_code == 200
    assert "context" not in json.loads(_on_share_calls(abdm_up)[0].request.body)["acknowledgement"]["profile"]


def test_unknown_request_id_is_a_404(client, abdm_up, hip, auth):
    res = _answer(client, auth, requestId="never-seen-0001")
    assert res.status_code == 404
    assert res.get_json()["message"]["error"]["code"] == "SHARE_REQUEST_NOT_FOUND"
    assert _on_share_calls(abdm_up) == []


def test_a_client_cannot_answer_another_clients_scan(client, abdm_up, hip, other_auth):
    _scan(client)
    assert _answer(client, other_auth).status_code == 404
    assert _on_share_calls(abdm_up) == []


def test_only_share_callbacks_can_be_answered(client, abdm_up, hip, auth):
    client.post("/api/v3/hip/token/on-generate-token", data=json.dumps({"hipId": HIP_ID}), headers={
        "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": "req-other-callback", "Content-Type": "application/json"})
    assert _answer(client, auth, requestId="req-other-callback").status_code == 404


@pytest.mark.parametrize("fields,field", [
    ({"requestId": None}, "requestId"),
    ({"abhaAddress": None}, "abhaAddress"),
    ({"tokenNumber": None}, "tokenNumber"),
    ({"abhaAddress": "not valid!"}, "abhaAddress"),
    ({"tokenNumber": "abc"}, "tokenNumber"),
    ({"tokenNumber": "12345678901"}, "tokenNumber"),
    ({"expiry": "soon"}, "expiry"),
    ({"expiry": "0"}, "expiry"),
    ({"expiry": "86401"}, "expiry"),
])
def test_invalid_input_is_a_400(client, abdm_up, hip, auth, fields, field):
    _scan(client)
    res = _answer(client, auth, **fields)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"] == {"field": field}
    assert _on_share_calls(abdm_up) == []


def test_abdm_down_queues_the_answer_for_a_retry(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, ABDM_ON_SHARE, json={"error": "down"}, status=503)
    _scan(client)
    res = _answer(client, auth)
    assert res.status_code == 202
    data = res.get_json()["data"]
    assert data["status"] == "queued" and data["hipId"] == HIP_ID

    job = OutboxJob.query.filter_by(kind="abdm_call").one()
    assert job.id == data["jobId"] and job.status == "pending" and job.api_client_id == hip.api_client_id


def test_abdm_rejecting_the_answer_is_returned_to_the_caller(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, ABDM_ON_SHARE, json={"error": {"code": 1401, "message": "bad request"}}, status=400)
    _scan(client)
    assert _answer(client, auth).status_code >= 400
    assert OutboxJob.query.filter_by(kind="abdm_call").count() == 0


def test_clinic_registered_from_the_default_webhook_can_be_answered(client, abdm_up, api_client, auth):
    tenant, _ = api_client
    registry.set_client_webhook(tenant, CALLBACK_URL)
    _scan(client, "IN-NEW-CLINIC-9")
    assert _answer(client, auth).get_json()["data"]["hipId"] == "IN-NEW-CLINIC-9"
    assert _on_share_calls(abdm_up)[0].request.headers["X-HIP-ID"] == "IN-NEW-CLINIC-9"
