"""
Two things i-medicus' M2 flow needs from the gateway to work for a user without anyone fixing it by hand
(found on the sandbox with the staging i-medicus, 13-16 Nov 2026):

1. the link token: i-medicus asks for it and uses it at once (before the callback), then asks again ("Duplicate Link
   token request"); the gateway keeps the token ABDM issued, uses it, waits for one on its way and does not ask twice;
2. the consent notice and the health-information request of a consent reach i-medicus together, so the keys exist
   when it builds, encrypts and pushes the records 10 seconds after the notice.
"""
import json
from datetime import timedelta

import pytest

from src.app.models import CallbackEvent, HipLinkToken, OutboxJob
from src.app.models import outbox_job as oj
from src.app.models.base import as_utc, utcnow
from src.app.outbox import worker
from src.app.services import hip_pairing, link_token_service
from tests.conftest import CALLBACK_URL, HIP_ID, make_jwt
from tests.contract.test_m2_end_to_end import ABDM, CONSENT_ID, PATIENT, TXN, World, _consent_notification

ADDRESS = "ravi@sbx"
NUMBER = "91536782361862"
GEN_TOKEN_BODY = {"abhaNumber": NUMBER, "abhaAddress": ADDRESS, "name": "Ravi", "gender": "M", "yearOfBirth": 1989,
                  "hipid": HIP_ID}


def link_body(token=None):
    body = {"hipid": HIP_ID, "singlecarecontext": {"abhaNumber": NUMBER, "abhaAddress": ADDRESS, "patient": PATIENT}}
    if token:
        body["linkToken"] = token
    return body


def issue(w, token, request_id="r-tok"):
    """ABDM's callback with the token."""
    w.abdm_calls_gateway("v3/hip/token/on-generate-token", {"abhaAddress": ADDRESS, "linkToken": token,
                                                           "response": {"requestId": "r-9"}}, request_id)


def abdm_calls(w, name):
    return [c.request for c in w.http.calls if c.request.url == ABDM[name]]


# ---- the link token -------------------------------------------------------------------------------------------------

def test_the_token_abdm_issued_is_kept_sealed(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    issue(w, "tok-abdm")
    row = HipLinkToken.query.one()
    assert (row.hip_id, row.abha_address) == (HIP_ID, ADDRESS) and row.issued_at is not None
    assert "tok-abdm" not in row.token_enc  # sealed like the callback bodies


def test_a_link_call_uses_the_token_abdm_issued_not_the_one_the_client_sent(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    issue(w, "tok-abdm")
    res = w.imedicus_answers("link/care-context", link_body("what-i-medicus-had"))
    assert res.status_code == 202
    assert [r.headers["X-LINK-TOKEN"] for r in abdm_calls(w, "link")] == ["tok-abdm"]


def test_a_link_call_needs_no_token_from_the_client_when_abdm_issued_one(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    issue(w, "tok-abdm")
    assert w.imedicus_answers("link/care-context", link_body()).status_code == 202
    res = w.imedicus_answers("link/notify", {"hipid": HIP_ID, "abhaAddress": ADDRESS, "hiType": ["Prescription"],
                                             "patientReference": "P-1", "careContextReference": "CC-1"})
    assert res.status_code == 201
    assert abdm_calls(w, "notify")[0].headers["X-LINK-TOKEN"] == "tok-abdm"


def test_without_an_issued_token_the_clients_token_is_used_as_before(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    assert w.imedicus_answers("link/care-context", link_body("from-client")).status_code == 202
    assert abdm_calls(w, "link")[0].headers["X-LINK-TOKEN"] == "from-client"
    assert w.imedicus_answers("link/care-context", link_body()).status_code == 400  # and none at all is still refused


def test_a_link_call_waits_for_the_token_that_is_on_its_way(app, client, jwks, http, hip, auth, monkeypatch):
    """i-medicus links straight after asking: the token arrives a moment later."""
    app.config["HIP_LINK_TOKEN_WAIT_SECONDS"] = 5
    w = World(client, jwks, auth)
    assert w.imedicus_answers("link-token", GEN_TOKEN_BODY).status_code == 202

    def arrives(_seconds):
        monkeypatch.undo()
        issue(w, "tok-late")

    monkeypatch.setattr(link_token_service.time, "sleep", arrives)
    res = w.imedicus_answers("link/care-context", link_body("not-yet-valid"))
    assert res.status_code == 202
    assert [r.headers["X-LINK-TOKEN"] for r in abdm_calls(w, "link")] == ["tok-late"]


def test_a_token_that_never_arrives_does_not_block_for_long(app, client, jwks, http, hip, auth, monkeypatch):
    app.config["HIP_LINK_TOKEN_WAIT_SECONDS"] = 1
    w = World(client, jwks, auth)
    w.imedicus_answers("link-token", GEN_TOKEN_BODY)
    monkeypatch.setattr(link_token_service.time, "sleep", lambda _s: None)
    ticks = iter(range(100))
    monkeypatch.setattr(link_token_service.time, "monotonic", lambda: next(ticks))
    assert w.imedicus_answers("link/care-context", link_body("from-client")).status_code == 202
    assert abdm_calls(w, "link")[0].headers["X-LINK-TOKEN"] == "from-client"


def test_asking_again_for_an_issued_token_does_not_reach_abdm_and_hands_the_callback_over_again(client, jwks, http, hip,
                                                                                               auth):
    w = World(client, jwks, auth)
    assert w.imedicus_answers("link-token", GEN_TOKEN_BODY).status_code == 202
    issue(w, "tok-abdm")
    jobs_before = OutboxJob.query.count()
    res = w.imedicus_answers("link-token", GEN_TOKEN_BODY)
    assert res.status_code == 202 and res.get_json()["data"]["linkToken"] == "already issued"
    assert len(abdm_calls(w, "token")) == 1                      # ABDM was asked once
    assert OutboxJob.query.count() == jobs_before + 1            # the stored callback goes to i-medicus again
    job = OutboxJob.query.order_by(OutboxJob.id.desc()).first()
    event = CallbackEvent.query.get(job.callback_event_id)
    assert event.path.endswith("hip/token/on-generate-token")


def test_asking_while_a_token_is_on_its_way_is_not_sent_twice(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.imedicus_answers("link-token", GEN_TOKEN_BODY)
    res = w.imedicus_answers("link-token", GEN_TOKEN_BODY)
    assert res.status_code == 202 and res.get_json()["data"]["linkToken"] == "requested"
    assert len(abdm_calls(w, "token")) == 1


def test_a_refused_request_is_not_treated_as_on_its_way(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    http.replace("POST", ABDM["token"], json={"error": {"code": "ABDM-1207", "message": "no match"}}, status=400)
    assert w.imedicus_answers("link-token", GEN_TOKEN_BODY).status_code == 400
    http.replace("POST", ABDM["token"], json={}, status=202)
    assert w.imedicus_answers("link-token", GEN_TOKEN_BODY).get_json()["data"] == {"hipId": HIP_ID}
    assert len(abdm_calls(w, "token")) == 2


def test_an_expired_token_is_not_used_and_a_new_one_is_asked_for(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    issue(w, make_jwt(expires_in=-100))
    row = HipLinkToken.query.one()
    assert as_utc(row.expires_at) < utcnow()
    assert w.imedicus_answers("link-token", GEN_TOKEN_BODY).get_json()["data"] == {"hipId": HIP_ID}
    assert len(abdm_calls(w, "token")) == 1


def test_tokens_of_two_patients_stay_apart(client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    issue(w, "tok-ravi")
    w.abdm_calls_gateway("v3/hip/token/on-generate-token", {"abhaAddress": "asha@sbx", "linkToken": "tok-asha"}, "r-2")
    assert link_token_service.token_for_call(HIP_ID, ADDRESS, None) == "tok-ravi"
    assert link_token_service.token_for_call(HIP_ID, "asha@sbx", None) == "tok-asha"
    assert link_token_service.token_for_call(HIP_ID, "other@sbx", "client") == "client"


# ---- the hold: consent notice and health-information request come together ------------------------------------------

def hi_request(consent_id=CONSENT_ID):
    return {"transactionId": TXN, "hiRequest": {
        "consent": {"id": consent_id}, "dateRange": {"from": "2026-10-01T00:00:00.000Z", "to": "2026-11-30T00:00:00.000Z"},
        "dataPushUrl": "https://hiu.example.test/push",
        "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "nonce": "n",
                        "dhPublicKey": {"expiry": "2126-01-01T00:00:00.000Z", "parameters": "p", "keyValue": "k"}}}}


@pytest.fixture
def held(app):
    app.config["HIP_CONSENT_HOLD_SECONDS"] = 60
    app.config["HIP_PAIR_DELAY_SECONDS"] = 3


@pytest.fixture
def timers(monkeypatch):
    """The timer thread of the pairing, collected so the test runs it when it wants."""
    started = []
    monkeypatch.setattr(hip_pairing, "_spawn", lambda delay, fn, *args: started.append((delay, fn, args)))
    return started


def notice_job():
    return next(j for j in OutboxJob.query.all()
                if CallbackEvent.query.get(j.callback_event_id).path.endswith("consent/request/hip/notify"))


def request_job():
    return next(j for j in OutboxJob.query.all()
                if CallbackEvent.query.get(j.callback_event_id).path.endswith("hip/health-information/request"))


def test_a_granted_notice_waits_for_the_request_of_its_consent(held, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    job = notice_job()
    assert job.hold_consent_id == CONSENT_ID
    assert as_utc(job.next_attempt_at) > utcnow() + timedelta(seconds=30)
    assert worker.run(once=True) == 0                         # nothing is due: nothing reaches i-medicus yet
    assert CallbackEvent.query.one().status == "received"


def test_the_request_releases_the_notice_and_waits_to_follow_it(held, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
    assert as_utc(notice_job().next_attempt_at) <= utcnow()  # due now
    request = request_job()
    assert request.hold_consent_id == CONSENT_ID and as_utc(request.next_attempt_at) > utcnow() + timedelta(seconds=30)


def test_the_notice_is_delivered_first_and_the_request_follows_from_a_timer(held, timers, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
    http.add("POST", f"{CALLBACK_URL}/api/v3/consent/request/hip/notify", json={"status": "success"})
    http.add("POST", f"{CALLBACK_URL}/api/v3/hip/health-information/request", json={"status": "success"})

    assert worker.run(once=True) == 1                         # the worker delivers the notice (i-medicus sleeps 10 s in it)
    urls = [c.request.url for c in http.calls if c.request.url.startswith(CALLBACK_URL)]
    assert urls == [f"{CALLBACK_URL}/api/v3/consent/request/hip/notify"]
    (delay, fn, args), = timers                               # the request was taken for the timer, 3 seconds later
    assert delay == 3 and request_job().status == oj.PROCESSING

    fn(*args)                                                 # the timer fires inside i-medicus' sleep
    urls = [c.request.url for c in http.calls if c.request.url.startswith(CALLBACK_URL)]
    assert urls[-1] == f"{CALLBACK_URL}/api/v3/hip/health-information/request" and len(urls) == 2
    assert request_job().status == oj.DONE
    assert {e.status for e in CallbackEvent.query.all()} == {"delivered"}


def test_a_notice_without_a_request_is_delivered_when_the_hold_ends(held, timers, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    job = notice_job()
    job.next_attempt_at = utcnow() - timedelta(seconds=1)     # the hold has run out
    from src.app.extensions import db
    db.session.commit()
    http.add("POST", f"{CALLBACK_URL}/api/v3/consent/request/hip/notify", json={"status": "success"})
    assert worker.run(once=True) == 1 and timers == []
    assert CallbackEvent.query.one().status == "delivered"


def test_a_notice_that_is_not_a_grant_is_not_held(held, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification("REVOKED"), "r-c")
    job = notice_job()
    assert job.hold_consent_id is None and as_utc(job.next_attempt_at) <= utcnow()


def test_a_request_with_no_notice_waiting_is_not_held(held, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
    job = request_job()
    assert job.hold_consent_id is None and as_utc(job.next_attempt_at) <= utcnow()


def test_a_request_for_another_consent_does_not_release_the_notice(held, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request("another-consent"), "r-h")
    assert as_utc(notice_job().next_attempt_at) > utcnow() + timedelta(seconds=30)


def test_with_the_hold_off_everything_is_delivered_at_once(app, client, jwks, http, hip, auth):
    app.config["HIP_CONSENT_HOLD_SECONDS"] = 0
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    job = notice_job()
    assert job.hold_consent_id is None and as_utc(job.next_attempt_at) <= utcnow()
