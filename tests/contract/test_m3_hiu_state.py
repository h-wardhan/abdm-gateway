"""
M3 (HIU) consent and transfer tracking, WBS Days 16-19: the gateway keeps what ABDM's callbacks say about the consents
we asked for, stops a health-information request for a consent that cannot be used, lists the consents whose data must
be erased, and keeps the records of an ended consent shut.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import responses

from src.app.models import HiuConsent, HiuTransfer
from src.app.services import crypto_service
from tests.conftest import GATEWAY, HIP_ID, make_jwt
from tests.contract.test_m3_hiu import HI_REQUEST, REQ, SESSION, hi_body, iso

CONSENT_ID = "c0ffee00-1111-4222-8333-444455556666"
CREQ = "creq-9"


@pytest.fixture
def abdm_up(jwks):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, HI_REQUEST, json={}, status=202)
    return jwks


def stamp(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def abdm_calls(client, path, body, request_id="r-1"):
    res = client.post(f"/api/{path}", data=json.dumps(body), headers={
        "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "Content-Type": "application/json",
        "X-HIU-ID": HIP_ID})
    assert res.status_code == 202
    return res


def notify(client, status="GRANTED", request_id="r-n", artefacts=(CONSENT_ID,)):
    body = {"notification": {"status": status, "consentRequestId": CREQ,
                             "consentArtefacts": [{"id": a} for a in artefacts]}}
    abdm_calls(client, "v3/hiu/consent/request/notify", body, request_id)


def on_fetch(client, status="GRANTED", erase_at=None, date_to="2026-11-01T00:00:00.000Z", request_id="r-f"):
    body = {"consent": {"status": status, "consentDetail": {
        "consentId": CONSENT_ID, "hiTypes": ["Prescription"], "hip": {"id": "IN0000000001"},
        "permission": {"accessMode": "VIEW", "dateRange": {"from": "2026-10-01T00:00:00.000Z", "to": date_to},
                       "dataEraseAt": erase_at or stamp(30)}}}, "response": {"requestId": "x"}}
    abdm_calls(client, "v3/hiu/consent/on-fetch", body, request_id)


def hi_request(client, auth, **changes):
    return client.post("/api/v1/hiu/health-information/request", json=hi_body(consentId=CONSENT_ID, **changes),
                       headers=auth)


def hi_calls(mock):
    return [c for c in mock.calls if c.request.url == HI_REQUEST]


# ---- what the callbacks record ----------------------------------------------------------------------------------------

def test_the_notification_and_the_fetched_artefact_are_recorded(client, jwks, hip):
    notify(client)
    row = HiuConsent.query.one()
    assert (row.consent_id, row.consent_request_id, row.status, row.hiu_id) == (CONSENT_ID, CREQ, "GRANTED", HIP_ID)
    on_fetch(client)
    row = HiuConsent.query.one()  # same row, now with the artefact's terms
    assert row.hip_id == "IN0000000001" and row.hi_types == ["Prescription"]
    assert row.date_to.year == 2026 and row.erase_at is not None


def test_a_denied_request_without_artefacts_is_recorded_by_its_request_id(client, jwks, hip):
    notify(client, status="DENIED", artefacts=())
    row = HiuConsent.query.one()
    assert (row.status, row.consent_id, row.consent_request_id) == ("DENIED", None, CREQ)
    notify(client, status="DENIED", artefacts=(), request_id="r-n2")
    assert HiuConsent.query.count() == 1


def test_a_revocation_updates_the_same_consent(client, jwks, hip):
    notify(client)
    notify(client, status="REVOKED", request_id="r-n2")
    assert HiuConsent.query.one().status == "REVOKED"


def test_a_callback_that_is_not_about_our_consents_changes_nothing(client, jwks, hip):
    abdm_calls(client, "v3/hiu/consent/request/notify", {"notification": {}})
    abdm_calls(client, "v3/hiu/consent/on-fetch", {"consent": {}}, "r-2")
    assert HiuConsent.query.count() == 0


# ---- the health-information request is checked -----------------------------------------------------------------------

def test_a_granted_consent_goes_through_and_the_request_is_tracked(client, abdm_up, hip, auth):
    notify(client)
    on_fetch(client)
    res = hi_request(client, auth)
    assert res.status_code == 202 and len(hi_calls(abdm_up)) == 1
    transfer = HiuTransfer.query.one()
    assert (transfer.request_id, transfer.consent_id, transfer.status) == (REQ, CONSENT_ID, "REQUESTED")


@pytest.mark.parametrize("status", ["REVOKED", "DENIED", "EXPIRED"])
def test_a_consent_that_has_ended_is_blocked_before_abdm(client, abdm_up, hip, auth, status):
    notify(client)
    on_fetch(client, status=status)
    res = hi_request(client, auth)
    assert res.status_code == 403
    error = res.get_json()["message"]["error"]
    assert error["code"] == "CONSENT_INVALID" and error["details"]["reason"] == status
    assert hi_calls(abdm_up) == [] and HiuTransfer.query.count() == 0


def test_a_consent_past_its_erase_date_is_blocked(client, abdm_up, hip, auth):
    notify(client)
    on_fetch(client, erase_at="2020-01-01T00:00:00.000Z")
    res = hi_request(client, auth)
    assert res.status_code == 403 and res.get_json()["message"]["error"]["details"]["reason"] == "EXPIRED"
    assert hi_calls(abdm_up) == []


def test_a_date_range_outside_the_consent_is_blocked(client, abdm_up, hip, auth):
    notify(client)
    on_fetch(client)
    res = hi_request(client, auth, toDate="2026-12-01T00:00:00.000Z")
    assert res.status_code == 403 and res.get_json()["message"]["error"]["details"]["reason"] == "OUT_OF_RANGE"
    assert hi_calls(abdm_up) == []


def test_a_consent_the_gateway_never_heard_of_goes_through_unless_configured(app, client, abdm_up, hip, auth):
    assert hi_request(client, auth).status_code == 202
    app.config["HIU_REQUIRE_KNOWN_CONSENT"] = True
    res = hi_request(client, auth, requestId="another-request-1")
    assert res.status_code == 403 and res.get_json()["message"]["error"]["details"]["reason"] == "NOT_FOUND"


def test_a_failed_abdm_call_leaves_no_transfer_row(client, jwks, hip, auth):
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    jwks.add(responses.POST, HI_REQUEST, json={}, status=503)
    assert hi_request(client, auth).status_code == 502
    assert HiuTransfer.query.count() == 0


def test_the_answer_with_the_transaction_id_completes_the_transfer(client, abdm_up, hip, auth):
    notify(client)
    on_fetch(client)
    hi_request(client, auth)
    abdm_calls(client, "v3/hiu/health-information/on-request", {
        "hiRequest": {"transactionId": "txn-77", "sessionStatus": "REQUESTED"}, "response": {"requestId": REQ}}, "r-h")
    transfer = HiuTransfer.query.one()
    assert (transfer.transaction_id, transfer.status, transfer.session_status) == ("txn-77", "ACKNOWLEDGED", "REQUESTED")


# ---- status -----------------------------------------------------------------------------------------

def test_the_consent_state_route_shows_status_usability_and_transactions(client, abdm_up, hip, auth):
    notify(client)
    on_fetch(client)
    hi_request(client, auth)
    abdm_calls(client, "v3/hiu/health-information/on-request", {
        "hiRequest": {"transactionId": "txn-77", "sessionStatus": "REQUESTED"}, "response": {"requestId": REQ}}, "r-h")
    data = client.get(f"/api/v1/hiu/consent/{CONSENT_ID}", headers=auth).get_json()["data"]
    assert data["status"] == "GRANTED" and data["usable"] is True and data["reason"] is None
    assert data["dateRange"]["to"] == "2026-11-01T00:00:00.000Z" and data["hiTypes"] == ["Prescription"]
    assert data["transactions"] == [{"requestId": REQ, "transactionId": "txn-77", "status": "ACKNOWLEDGED",
                                     "sessionStatus": "REQUESTED"}]


def test_the_state_of_an_ended_consent_says_why(client, jwks, hip, auth):
    notify(client)
    notify(client, status="REVOKED", request_id="r-n2")
    data = client.get(f"/api/v1/hiu/consent/{CONSENT_ID}", headers=auth).get_json()["data"]
    assert data["usable"] is False and data["reason"] == "REVOKED"


def test_another_clients_consent_is_404(client, jwks, hip, auth, other_auth):
    notify(client)
    res = client.get(f"/api/v1/hiu/consent/{CONSENT_ID}", headers=other_auth)
    assert res.status_code == 404 and res.get_json()["message"]["error"]["code"] == "CONSENT_NOT_FOUND"
    assert client.get("/api/v1/hiu/consent/unknown-id", headers=auth).status_code == 404



def test_the_state_routes_need_login(client, hip):
    assert client.get(f"/api/v1/hiu/consent/{CONSENT_ID}").status_code == 401


# ---- access control on received data ---------------------------------------------------------------------------------

def _decrypt_body(**extra):
    hiu, hip_keys = crypto_service.new_keys(), crypto_service.new_keys()
    encrypted = crypto_service.encrypt({
        "receiverPublicKey": hiu["publicKey"], "receiverNonce": hiu["nonce"], "senderPrivateKey": hip_keys["privateKey"],
        "senderPublicKey": hip_keys["publicKey"], "senderNonce": hip_keys["nonce"], "plainTextData": '{"a": 1}'})
    return {"receiverPrivateKey": hiu["privateKey"], "receiverNonce": hiu["nonce"], "senderPublicKey": encrypted["keyToShare"],
            "senderNonce": hip_keys["nonce"], "encryptedData": encrypted["encryptedData"], **extra}


def test_records_open_while_the_consent_stands_and_stay_shut_after_it_ends(client, abdm_up, hip, auth):
    notify(client)
    on_fetch(client)
    hi_request(client, auth)
    abdm_calls(client, "v3/hiu/health-information/on-request", {
        "hiRequest": {"transactionId": "txn-77", "sessionStatus": "REQUESTED"}, "response": {"requestId": REQ}}, "r-h")
    assert client.post("/api/v1/fhir/decrypt", json=_decrypt_body(transactionId="txn-77"), headers=auth).status_code == 200
    notify(client, status="REVOKED", request_id="r-n2")
    res = client.post("/api/v1/fhir/decrypt", json=_decrypt_body(transactionId="txn-77"), headers=auth)
    assert res.status_code == 403 and res.get_json()["message"]["error"]["details"]["reason"] == "REVOKED"
    assert client.post("/api/v1/fhir/decrypt", json=_decrypt_body(consentId=CONSENT_ID), headers=auth).status_code == 403


def test_decrypt_without_a_transaction_or_consent_works_as_before(client, hip, auth):
    res = client.post("/api/v1/fhir/decrypt", json=_decrypt_body(), headers=auth)
    assert res.status_code == 200 and json.loads(res.get_json() if res.is_json and isinstance(res.get_json(), str) else res.data)
