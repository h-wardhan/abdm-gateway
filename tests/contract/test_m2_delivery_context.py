"""
The gateway does not depend on i-medicus' rows or timing when it encrypts, pushes and reports a transfer.

i-medicus builds the records inside its consent-notice handler and reads the transaction id and the HIU's key from its own
`hip_link_requests` / `hip_link_request_keys` rows. When ABDM's health-information request has not been handled by i-medicus
yet (or a second notice overwrote the row) it sends empty keys and a stale or empty transaction (seen on the sandbox:
`fhir/encrypt` 400 "senderPrivateKey is required", nothing pushed). The gateway has the request itself (it stores it when it
arrives), and with one notice at a time per client every call of that client belongs to the notice being handled.
"""
import json
from datetime import timedelta

import pytest
import responses

from src.app.abdm import fidelius
from src.app.extensions import db
from src.app.models import CallbackEvent, HipTransfer, OutboxJob
from src.app.models import hip_state as hs
from src.app.models import outbox_job as oj
from src.app.models.base import utcnow
from src.app.outbox import engine
from tests.contract.test_fhir_routes import hip_auth  # noqa: F401  (fixture)
from tests.contract.test_m2_all_record_types import SLUGS, TYPES, build, paged  # noqa: F401
from tests.contract.test_m2_transfer import (CONSENT_ID, HI_NOTIFY, HIU_URL, TXN, Flow, flow, hiu_dns)  # noqa: F401


@pytest.fixture
def ctx(app):
    app.config["HIP_ACK_NOTICE_AT_ONCE"] = True      # the gateway stores and acknowledges ABDM's request itself
    app.config["HIP_ONE_NOTICE_PER_CLIENT"] = True   # one notice at a time per client: the context is unambiguous
    app.config["HIP_NOTICE_HOLD_SECONDS"] = 900


def begin_notice(consent_id=CONSENT_ID):
    """i-medicus is now handling the consent notice (the worker delivers it and waits for the answer)."""
    event = CallbackEvent.query.filter(CallbackEvent.path.like("%consent/request/hip/notify")).first()
    job = engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=event.api_client_id)
    job.status = oj.PROCESSING
    job.hold_consent_id = consent_id
    db.session.commit()
    return job


def empty_encrypt(flow, plain):
    """The call i-medicus makes when its rows hold no keys yet."""
    return flow.client.post("/api/v1/fhir/encrypt", headers=flow.auth, json={
        "receiverPublicKey": "", "receiverNonce": "", "senderPrivateKey": "", "senderPublicKey": "", "senderNonce": "",
        "plainTextData": plain})


def read(flow, encrypted, nonce):
    return fidelius.decrypt(encrypted["encryptedData"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                            encrypted["keyToShare"], nonce)


def stale_push(flow, encrypted, nonce, references, transaction="old-txn-of-the-previous-consent"):
    """The push i-medicus sends: the transaction of its row (a previous consent's) and its own key material."""
    body = flow.push_body({"nonce": nonce}, encrypted, references=references)
    body["transactionId"] = transaction
    body["keyMaterial"]["nonce"] = "the-nonce-of-another-round"
    body["pushUrl"] = "https://old-hiu.example.test/push"
    return body


def open_transfer(flow, contexts=("CC-1", "CC-2")):
    """ABDM's notice and request reach the gateway; i-medicus has not handled either."""
    flow.consent(contexts=contexts)
    flow.hi_request()
    return HipTransfer.query.filter_by(transaction_id=TXN).one()


# ---- the gateway keeps the request itself --------------------------------------------------------------------------------

def test_the_request_is_stored_and_acknowledged_when_it_arrives_before_i_medicus_has_seen_it(ctx, flow):
    transfer = open_transfer(flow)
    assert transfer.consent_id == CONSENT_ID and transfer.hiu_public_key and transfer.hiu_nonce == flow.key_block["nonce"]
    assert len(flow.calls("https://healthidsbx.abdm.gov.in/api/hiecm/data-flow/v3/health-information/hip/on-request")) <= 1


def test_i_medicus_own_acknowledgement_of_the_request_is_a_duplicate(ctx, flow):
    open_transfer(flow)
    res = flow.client.post("/api/v1/hip/health-information/on-request", headers=flow.auth,
                           json={"transactionId": TXN, "requestId": "req-hi-0001"})
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "already_sent"


def test_a_request_of_a_consent_that_is_not_usable_is_not_stored_and_does_not_break_the_callback(ctx, flow):
    flow.consent(status="REVOKED")
    flow.hi_request()                                   # 202 for ABDM; the consent is revoked: refused quietly
    assert HipTransfer.query.count() == 0


# ---- encrypt without keys ------------------------------------------------------------------------------------------------

def test_encrypt_with_empty_keys_uses_the_hiu_key_of_the_notice_being_handled(ctx, flow):
    transfer = open_transfer(flow)
    begin_notice()
    res = empty_encrypt(flow, '{"resourceType":"Bundle"}')
    assert res.status_code == 200, res.get_json()
    db.session.expire_all()
    transfer = db.session.get(HipTransfer, transfer.id)
    assert transfer.sender_nonce and transfer.sender_private_key_enc and transfer.sender_public_key
    assert read(flow, res.get_json(), transfer.sender_nonce) == '{"resourceType":"Bundle"}'   # the HIU reads it


def test_all_records_of_the_transfer_share_one_sender_key(ctx, flow):
    transfer = open_transfer(flow)
    begin_notice()
    first, second = (empty_encrypt(flow, p).get_json() for p in ("one", "two"))
    assert first["keyToShare"] == second["keyToShare"]
    nonce = db.session.get(HipTransfer, transfer.id).sender_nonce
    assert (read(flow, first, nonce), read(flow, second, nonce)) == ("one", "two")


def test_a_usable_sender_key_of_i_medicus_is_kept(ctx, flow):
    open_transfer(flow)
    begin_notice()
    sender = flow.client.get("/api/v1/fhir/keys", headers=flow.auth).get_json()
    res = flow.client.post("/api/v1/fhir/encrypt", headers=flow.auth, json={
        "receiverPublicKey": "stale", "receiverNonce": "stale", "senderPrivateKey": sender["privateKey"],
        "senderPublicKey": sender["publicKey"], "senderNonce": sender["nonce"], "plainTextData": "x"})
    assert res.status_code == 200 and read(flow, res.get_json(), sender["nonce"]) == "x"


def test_without_a_notice_being_handled_encrypt_behaves_as_before(ctx, flow):
    open_transfer(flow)
    assert empty_encrypt(flow, "x").status_code == 400


def test_with_two_notices_being_handled_nothing_is_guessed(ctx, flow):
    open_transfer(flow)
    begin_notice()
    begin_notice("another-consent")
    assert empty_encrypt(flow, "x").status_code == 400


def test_the_transfer_waits_for_a_request_that_is_on_its_way(ctx, flow, app, monkeypatch):
    from src.app.services import hip_transfer_service as svc
    app.config["HIP_TRANSFER_WAIT_SECONDS"] = 5
    flow.consent()
    begin_notice()
    calls = []

    def sleeping(seconds):
        calls.append(seconds)
        flow.hi_request()                               # the request arrives while the call waits

    monkeypatch.setattr(svc.time, "sleep", sleeping)
    res = empty_encrypt(flow, "x")
    assert res.status_code == 200 and calls == [0.5]


# ---- push and notify with the transaction of another round ------------------------------------------------------------------

def test_push_with_a_stale_transaction_goes_out_under_the_transaction_of_the_consent_being_handled(ctx, flow):
    transfer = open_transfer(flow)
    begin_notice()
    encrypted = empty_encrypt(flow, "record").get_json()
    nonce = db.session.get(HipTransfer, transfer.id).sender_nonce
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    res = flow.push(stale_push(flow, encrypted, nonce, ("CC-1",)))
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent", res.get_json()
    (page,) = flow.received_by_hiu()
    assert page["transactionId"] == TXN                               # the HIU's transaction, at the HIU's URL
    assert page["keyMaterial"]["nonce"] == nonce and page["keyMaterial"]["dhPublicKey"]["keyValue"]
    entry = page["entries"][0]
    assert fidelius.decrypt(entry["content"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                            page["keyMaterial"]["dhPublicKey"]["keyValue"], page["keyMaterial"]["nonce"]) == "record"
    assert db.session.get(HipTransfer, transfer.id).sender_private_key_enc is None   # the secret is dropped


def test_push_with_the_row_of_a_finished_consent_is_not_sent_under_that_consent(ctx, flow):
    open_transfer(flow)
    old = HipTransfer(api_client_id=HipTransfer.query.first().api_client_id, hip_id="IN0810041408",
                      transaction_id="old-txn-of-the-previous-consent", consent_id="old-consent",
                      status=hs.TRANSFER_NOTIFIED, attempts=1)
    db.session.add(old)
    db.session.commit()
    begin_notice()
    encrypted = empty_encrypt(flow, "record").get_json()
    nonce = HipTransfer.query.filter_by(transaction_id=TXN).one().sender_nonce
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    assert flow.push(stale_push(flow, encrypted, nonce, ("CC-1",))).status_code == 200
    assert flow.received_by_hiu()[0]["transactionId"] == TXN
    assert db.session.get(HipTransfer, old.id).status == hs.TRANSFER_NOTIFIED


def test_push_of_care_contexts_the_consent_does_not_cover_is_not_redirected(ctx, flow):
    open_transfer(flow)
    begin_notice()
    encrypted = empty_encrypt(flow, "record").get_json()
    res = flow.push(stale_push(flow, encrypted, "n", ("SOMEONE-ELSES-CONTEXT",)))
    assert res.status_code == 404 and flow.received_by_hiu() == []


def test_notify_with_a_stale_transaction_tells_abdm_about_the_consent_being_handled(ctx, flow):
    transfer = open_transfer(flow)
    begin_notice()
    encrypted = empty_encrypt(flow, "record").get_json()
    nonce = db.session.get(HipTransfer, transfer.id).sender_nonce
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    flow.http.add(responses.POST, HI_NOTIFY, json={}, status=202)
    assert flow.push(stale_push(flow, encrypted, nonce, ("CC-1", "CC-2"))).status_code == 200
    body = flow.notify_body()
    body["notification"]["transactionId"] = "old-txn-of-the-previous-consent"
    body["notification"]["consentId"] = "old-consent"
    res = flow.client.post("/api/v1/hip/health-information/notify", headers=flow.auth, json=body)
    assert res.status_code == 200, res.get_json()
    (n,) = flow.abdm_notifications()
    assert n["transactionId"] == TXN and n["consentId"] == CONSENT_ID
    assert {r["careContextReference"] for r in n["statusNotification"]["statusResponses"]} == {"CC-1", "CC-2"}


# ---- two record types and eight, the minimum and the maximum of one consent -------------------------------------------------

def run_consent(flow, contexts, texts):
    """i-medicus' whole handler for a consent, with its rows empty: every record encrypted, one push, one notify."""
    transfer = open_transfer(flow, contexts=contexts)
    begin_notice()
    entries, nonce = [], None
    for context, text in zip(contexts, texts):
        res = empty_encrypt(flow, text)
        assert res.status_code == 200, (context, res.get_json())
        encrypted = res.get_json()
        nonce = db.session.get(HipTransfer, transfer.id).sender_nonce
        entries.append({"content": encrypted["encryptedData"], "checksum": "x", "media": "application/fhir+json",
                        "careContextReference": context})
    body = stale_push(flow, encrypted, nonce, (contexts[0],))
    body["entries"] = entries
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    flow.http.add(responses.POST, HI_NOTIFY, json={}, status=202)
    res = flow.push(body)
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent", res.get_json()
    seen = {}
    for page in flow.received_by_hiu():
        (entry,) = page["entries"]
        assert page["transactionId"] == TXN
        seen[entry["careContextReference"]] = fidelius.decrypt(
            entry["content"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
            page["keyMaterial"]["dhPublicKey"]["keyValue"], page["keyMaterial"]["nonce"])
    assert seen == dict(zip(contexts, texts))
    notice = flow.notify_body(responses_=[{"careContextReference": contexts[0], "hiStatus": "DELIVERED",
                                           "description": "Data Pushed"}])
    notice["notification"]["transactionId"] = ""
    assert flow.client.post("/api/v1/hip/health-information/notify", headers=flow.auth, json=notice).status_code == 200
    (n,) = flow.abdm_notifications()
    assert n["statusNotification"]["sessionStatus"] == "TRANSFERRED"
    assert {r["careContextReference"] for r in n["statusNotification"]["statusResponses"]} == set(contexts)
    return flow.received_by_hiu()


def test_a_consent_for_two_record_types_with_empty_i_medicus_keys(ctx, paged, flow, client, hip_auth):
    contexts = ["CC-Prescription", "CC-OPConsultation"]
    texts = [json.dumps(build(client, hip_auth, "Prescription")), json.dumps(build(client, hip_auth, "OPConsultation"))]
    assert len(run_consent(flow, contexts, texts)) == 2


def test_a_consent_for_all_eight_record_types_with_empty_i_medicus_keys(ctx, paged, flow, client, hip_auth):
    contexts = [f"CC-{hi_type}" for hi_type in TYPES]
    texts = [json.dumps(build(client, hip_auth, hi_type), ensure_ascii=False) for hi_type in TYPES]
    assert len(run_consent(flow, contexts, texts)) == 8


def test_a_consent_with_a_very_large_record(ctx, paged, flow, client, hip_auth):
    big = json.dumps({"resourceType": "Bundle", "note": "x" * (3 * 1024 * 1024)})   # a scanned report as base64: megabytes
    pages = run_consent(flow, ["CC-1", "CC-2"], [big, "small"])
    assert len(pages) == 2 and len(pages[0]["entries"][0]["content"]) > 3 * 1024 * 1024


def test_a_big_consent_of_many_care_contexts(ctx, paged, flow):
    contexts = [f"CC-{n}" for n in range(60)]                      # a year of visits
    texts = [f"record {n} " * 200 for n in range(60)]
    assert len(run_consent(flow, contexts, texts)) == 60


# ---- the worker: one notice at a time per client -------------------------------------------------------------------------------

def test_notices_of_different_patients_of_one_client_go_one_at_a_time(ctx, app, flow):
    from src.app.outbox import worker
    from tests.contract.test_m2_link_token_and_hold import FakePool
    flow.consent()
    event = CallbackEvent.query.filter(CallbackEvent.path.like("%consent/request/hip/notify")).first()
    jobs = []
    for n in range(2):
        job = engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=event.api_client_id)
        job.hold_consent_id = f"consent-{n}"
        jobs.append(job)
    db.session.commit()
    worker._LANES.clear()
    OutboxJob.query.filter(OutboxJob.id.notin_([j.id for j in jobs])).delete(synchronize_session=False)  # queued ABDM calls
    db.session.commit()
    pool, inflight = FakePool(), {}
    assert worker.dispatch(app, pool, inflight, 4) == 1               # the second waits for the first
    assert worker.dispatch(app, pool, inflight, 4) == 0
    assert sorted(OutboxJob.query.get(j.id).status for j in jobs) == ["pending", "processing"]
