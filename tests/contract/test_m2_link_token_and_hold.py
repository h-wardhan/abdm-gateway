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


# ---- the consent acknowledgement waits for the health-information request ---------------------------------------------
# i-medicus: notice -> sleeps 5 s -> saves the consent id -> acknowledges -> sleeps 10 s -> builds the records. ABDM sends the
# request only after the acknowledgement (~14 s later). The gateway answers the acknowledgement when the request was handled.

def hi_request(consent_id=CONSENT_ID):
    return {"transactionId": TXN, "hiRequest": {
        "consent": {"id": consent_id}, "dateRange": {"from": "2026-10-01T00:00:00.000Z", "to": "2026-11-30T00:00:00.000Z"},
        "dataPushUrl": "https://hiu.example.test/push",
        "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "nonce": "n",
                        "dhPublicKey": {"expiry": "2126-01-01T00:00:00.000Z", "parameters": "p", "keyValue": "k"}}}}


def notice_ack(consent_id=CONSENT_ID):
    return {"consentId": consent_id, "requestId": "r-c", "hipId": HIP_ID}


@pytest.fixture
def ack_wait(app, monkeypatch):
    """Turn the wait on; no real sleeping."""
    app.config["HIP_CONSENT_ACK_WAIT_SECONDS"] = 25
    app.config["HIP_KEYS_SETTLE_SECONDS"] = 4
    slept = []
    monkeypatch.setattr(hip_pairing.time, "sleep", lambda s: slept.append(s))
    return slept


def test_the_ack_is_sent_to_abdm_first_and_i_medicus_is_answered_when_the_request_was_handled(
        ack_wait, client, jwks, http, hip, auth, monkeypatch):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    seen = {}

    def request_arrives(seconds):
        ack_wait.append(seconds)
        if "abdm_acked" not in seen:
            seen["abdm_acked"] = len(abdm_calls(w, "consent"))      # ABDM already has the acknowledgement
            w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
            w.imedicus_answers("health-information/on-request", {"transactionId": TXN, "response": {"requestId": "r-h"}})

    monkeypatch.setattr(hip_pairing.time, "sleep", request_arrives)
    res = w.imedicus_answers("consent/on-notify", notice_ack())
    assert res.status_code == 201 and seen["abdm_acked"] == 1
    assert 4 in ack_wait                                        # and the keys got their moment


def test_the_wait_ends_when_no_request_comes_and_i_medicus_is_answered_anyway(ack_wait, client, jwks, http, hip, auth,
                                                                              monkeypatch):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(hip_pairing.time, "monotonic", lambda: next(ticks))
    res = w.imedicus_answers("consent/on-notify", notice_ack())
    assert res.status_code == 201 and len(abdm_calls(w, "consent")) == 1


def test_a_consent_that_is_not_granted_is_answered_at_once(ack_wait, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification("REVOKED"), "r-c")
    assert w.imedicus_answers("consent/on-notify", notice_ack()).status_code == 201
    assert ack_wait == []


def test_a_repeated_ack_does_not_wait_again(ack_wait, client, jwks, http, hip, auth, monkeypatch):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    monkeypatch.setattr(hip_pairing, "wait_for_request", lambda consent_id: calls.append(consent_id))
    calls = []
    w.imedicus_answers("consent/on-notify", notice_ack())
    w.imedicus_answers("consent/on-notify", notice_ack())
    assert calls == [CONSENT_ID]                                # the second answer was "already_sent"


def test_with_the_wait_off_the_ack_is_answered_at_once(app, client, jwks, http, hip, auth, monkeypatch):
    app.config["HIP_CONSENT_ACK_WAIT_SECONDS"] = 0
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    monkeypatch.setattr(hip_pairing.time, "sleep", lambda s: pytest.fail("must not wait"))
    assert w.imedicus_answers("consent/on-notify", notice_ack()).status_code == 201


# ---- the request is not left behind the worker that is busy with its notice -------------------------------------------

@pytest.fixture
def threads(monkeypatch):
    started = []
    monkeypatch.setattr(hip_pairing, "_spawn", lambda delay, fn, *args: started.append((delay, fn, args)))
    return started


def _in_flight_notice():
    """The worker is delivering the notice: its job is `processing`."""
    from src.app.extensions import db
    job = next(j for j in OutboxJob.query.all()
               if CallbackEvent.query.get(j.callback_event_id).path.endswith("consent/request/hip/notify"))
    job.status = oj.PROCESSING
    db.session.commit()
    return job


def test_a_request_is_delivered_by_a_thread_while_its_notice_is_being_delivered(threads, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    assert _in_flight_notice().hold_consent_id == CONSENT_ID
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
    (delay, fn, args), = threads
    assert delay > 0
    http.add("POST", f"{CALLBACK_URL}/api/v3/hip/health-information/request", json={"status": "success"})
    fn(*args)                                                  # the thread, after the receiver committed
    urls = [c.request.url for c in http.calls if c.request.url.startswith(CALLBACK_URL)]
    assert urls == [f"{CALLBACK_URL}/api/v3/hip/health-information/request"]
    event = CallbackEvent.query.filter_by(request_id="r-h").one()
    assert event.status == "delivered"


def test_a_request_without_a_notice_in_flight_goes_through_the_worker(threads, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
    assert threads == []
    assert OutboxJob.query.one().status == oj.PENDING


def test_a_request_of_another_consent_is_not_taken_by_a_thread(threads, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    _in_flight_notice()
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request("another-consent"), "r-h")
    assert threads == []


def test_the_worker_and_the_thread_never_deliver_the_same_request_twice(threads, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    _in_flight_notice()
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
    (delay, fn, args), = threads
    http.add("POST", f"{CALLBACK_URL}/api/v3/hip/health-information/request", json={"status": "success"})
    assert worker.run(once=True) == 1                           # the worker was free first
    fn(*args)                                                   # the thread finds the job already taken
    urls = [c.request.url for c in http.calls if c.request.url == f"{CALLBACK_URL}/api/v3/hip/health-information/request"]
    assert len(urls) == 1


def test_rubbish_bodies_are_stored_and_queued_without_a_crash(threads, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", {"notification": "oops"}, "r-1")
    w.abdm_calls_gateway("v3/hip/health-information/request", {"hiRequest": ["x"]}, "r-2")
    w.abdm_calls_gateway("v3/hip/health-information/request", {"hiRequest": {"consent": "x"}}, "r-3")
    assert OutboxJob.query.count() == 3 and threads == []


# ---- the worker runs jobs side by side, HIP consent notices one at a time ---------------------------------------------

class FakePool:
    """Records what the worker hands over; futures that are never done unless the test says so."""
    class Future:
        def __init__(self):
            self.finished = False

        def done(self):
            return self.finished

    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        future = FakePool.Future()
        self.submitted.append((future, args))
        return future


def _statuses():
    from src.app.extensions import db
    db.session.expire_all()
    return {j.id: j.status for j in OutboxJob.query.all()}


def _job_ids(world, kind_path):
    return [j.id for j in OutboxJob.query.order_by(OutboxJob.id).all()
            if CallbackEvent.query.get(j.callback_event_id).path.endswith(kind_path)]


def test_a_hiu_callback_is_not_stuck_behind_a_consent_notice_that_is_waiting(app, client, jwks, http, hip, auth):
    """The deadlock of 16 Nov 2026: the notice waits for the request, the request needs the HIU callback queued behind it."""
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-n1")
    w.abdm_calls_gateway("v3/hiu/consent/request/notify", {"notification": {"status": "GRANTED"}}, "r-h1")
    pool, inflight = FakePool(), {}
    assert worker.dispatch(app, pool, inflight, 4) == 2        # both at once
    assert set(_statuses().values()) == {"processing"}


def test_two_consent_notices_never_run_together(app, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    other = _consent_notification()
    other["notification"]["consentId"] = other["notification"]["consentDetail"]["consentId"] = "second-consent"
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-n1")
    w.abdm_calls_gateway("v3/consent/request/hip/notify", other, "r-n2")
    first, second = _job_ids(w, "consent/request/hip/notify")
    pool, inflight = FakePool(), {}
    assert worker.dispatch(app, pool, inflight, 4) == 1
    assert _statuses()[first] == "processing" and _statuses()[second] == "pending"
    assert worker.dispatch(app, pool, inflight, 4) == 0         # still busy with the first
    (future, _args), = pool.submitted
    future.finished = True
    assert worker.dispatch(app, pool, inflight, 4) == 1         # the first is done: the second may start
    assert _statuses()[second] == "processing"


def test_the_request_runs_while_its_notice_is_in_flight(app, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-n1")
    pool, inflight = FakePool(), {}
    worker.dispatch(app, pool, inflight, 4)
    w.abdm_calls_gateway("v3/hip/health-information/request", hi_request(), "r-h")
    assert worker.dispatch(app, pool, inflight, 4) == 1
    assert len(pool.submitted) == 2


def test_the_worker_never_runs_more_jobs_than_it_has_slots(app, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    for n in range(5):
        w.abdm_calls_gateway("v3/hiu/consent/request/notify", {"notification": {"status": "GRANTED"}}, f"r-{n}")
    pool, inflight = FakePool(), {}
    assert worker.dispatch(app, pool, inflight, 3) == 3
    assert worker.dispatch(app, pool, inflight, 3) == 0
    assert list(_statuses().values()).count("pending") == 2


def test_a_job_still_running_is_not_claimed_again(app, client, jwks, http, hip, auth):
    from src.app.extensions import db
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/hiu/consent/request/notify", {"notification": {"status": "GRANTED"}}, "r-1")
    pool, inflight = FakePool(), {}
    worker.dispatch(app, pool, inflight, 4)
    job = OutboxJob.query.one()
    job.locked_until = utcnow() - timedelta(seconds=5)          # its lock ran out while the thread still runs
    db.session.commit()
    assert worker.dispatch(app, pool, inflight, 4) == 0 and len(pool.submitted) == 1


def test_notices_of_different_patients_run_together_and_of_one_patient_one_at_a_time(app, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)

    def notice(consent, patient):
        body = _consent_notification()
        body["notification"]["consentId"] = body["notification"]["consentDetail"]["consentId"] = consent
        body["notification"]["consentDetail"]["patient"] = {"id": patient}
        return body

    w.abdm_calls_gateway("v3/consent/request/hip/notify", notice("c-1", "ravi@sbx"), "r-1")
    w.abdm_calls_gateway("v3/consent/request/hip/notify", notice("c-2", "asha@sbx"), "r-2")
    w.abdm_calls_gateway("v3/consent/request/hip/notify", notice("c-3", "ravi@sbx"), "r-3")
    first, second, third = _job_ids(w, "consent/request/hip/notify")
    pool, inflight = FakePool(), {}
    assert worker.dispatch(app, pool, inflight, 8) == 2         # ravi's first and asha's: side by side
    status = _statuses()
    assert (status[first], status[second], status[third]) == ("processing", "processing", "pending")
    pool.submitted[0][0].finished = True                         # ravi's first is done
    assert worker.dispatch(app, pool, inflight, 8) == 1
    assert _statuses()[third] == "processing"


def test_too_many_waiting_acknowledgements_are_answered_at_once(app, ack_wait, client, jwks, http, hip, auth):
    app.config["HIP_MAX_ACK_WAITS"] = 1
    hip_pairing._slots.clear()
    w = World(client, jwks, auth)
    w.abdm_calls_gateway("v3/consent/request/hip/notify", _consent_notification(), "r-c")
    assert hip_pairing._wait_slots(1).acquire(blocking=False)    # one acknowledgement already waits
    try:
        assert hip_pairing.wait_for_request(CONSENT_ID) is False and ack_wait == []
    finally:
        hip_pairing._wait_slots(1).release()
        hip_pairing._slots.clear()


def test_a_long_queue_of_one_patients_notices_does_not_starve_the_other_jobs(app, client, jwks, http, hip, auth):
    w = World(client, jwks, auth)
    for n in range(120):                                          # more than the dispatcher's look-ahead window
        body = _consent_notification()
        body["notification"]["consentId"] = body["notification"]["consentDetail"]["consentId"] = f"c-{n}"
        w.abdm_calls_gateway("v3/consent/request/hip/notify", body, f"r-n{n}")
    w.abdm_calls_gateway("v3/hiu/consent/request/notify", {"notification": {"status": "GRANTED"}}, "r-late")
    pool, inflight = FakePool(), {}
    assert worker.dispatch(app, pool, inflight, 4) == 2           # one of the patient's notices + the job behind them
    statuses = list(_statuses().values())
    assert statuses.count("processing") == 2 and statuses.count("pending") == 119
