"""Replies sent to ABDM that are queued for the worker when ABDM is down."""
import json
from datetime import timedelta

import pytest
import requests
import responses

from src.app.callbacks import vault
from src.app.errors import AbdmUnavailableError, AbdmUpstreamError, ValidationError
from src.app.extensions import db
from src.app.models import AbdmErrorLog, OutboxJob
from src.app.models.base import utcnow
from src.app.outbox import abdm_calls, engine, worker
from tests.conftest import GATEWAY

PATH = "/api/hiecm/test/v3/on-share"
URL = GATEWAY + PATH
BODY = {"acknowledgement": {"status": "SUCCESS", "healthId": "ravi@sbx"}, "response": {"requestId": "r-1"}}


@pytest.mark.parametrize("exc,expected", [
    (AbdmUnavailableError(), True),
    (AbdmUpstreamError("x", status=500), True),
    (AbdmUpstreamError("x", status=502), True),
    (AbdmUpstreamError("x", status=503), True),
    (AbdmUpstreamError("x", status=408), True),
    (AbdmUpstreamError("x", status=429), True),
    (AbdmUpstreamError("x", status=400), False),
    (AbdmUpstreamError("x", status=403), False),
    (AbdmUpstreamError("x", status=404), False),
    (AbdmUpstreamError("x", status=409), False),
    (ValidationError("nope"), False),
])
def test_is_retryable(exc, expected):
    assert abdm_calls.is_retryable(exc) is expected


def test_enqueue_stores_the_call_encrypted(app):
    job = abdm_calls.enqueue_abdm_call(PATH, BODY, endpoint_name="on-share", delay=30)
    db.session.commit()
    assert (job.kind, job.status) == ("abdm_call", "pending")
    assert "ravi@sbx" not in job.payload_enc
    assert json.loads(vault.open_text(job.payload_enc)) == {
        "method": "POST", "path": PATH, "target": "gateway", "body": BODY, "headers": None, "endpoint_name": "on-share"}


@pytest.mark.parametrize("func", [abdm_calls.enqueue_abdm_call, abdm_calls.send_or_queue])
def test_calls_to_the_abha_service_are_never_queued(app, abdm, func):
    with pytest.raises(ValueError, match="never queued"):
        func("/abha/api/v3/enrollment/request/otp", {"otp": "123456"}, target="abha")
    assert OutboxJob.query.count() == 0
    assert len(abdm.calls) == 0


def test_send_or_queue_sends_when_abdm_answers(app, abdm):
    abdm.add(responses.POST, URL, json={"message": "ok"}, status=202)
    out = abdm_calls.send_or_queue(PATH, BODY, endpoint_name="on-share")
    assert out == {"status": "sent", "result": {"message": "ok"}}
    assert OutboxJob.query.count() == 0
    sent = [c for c in abdm.calls if c.request.url == URL][0]
    assert json.loads(sent.request.body) == BODY


@pytest.mark.parametrize("reply", [
    {"status": 500, "json": {"message": "boom"}},
    {"status": 503, "json": {}},
    {"status": 429, "json": {"message": "slow down"}},
    {"status": 408, "json": {}},
    {"body": requests.ConnectionError("down")},
    {"body": requests.Timeout("slow")},
])
def test_send_or_queue_queues_when_abdm_has_a_temporary_problem(app, abdm, reply):
    abdm.add(responses.POST, URL, **reply)
    out = abdm_calls.send_or_queue(PATH, BODY, api_client_id=None)
    job = OutboxJob.query.one()
    assert out == {"status": "queued", "jobId": job.id}
    assert json.loads(vault.open_text(job.payload_enc))["body"] == BODY


@pytest.mark.parametrize("status", [400, 403, 404, 409, 422])
def test_send_or_queue_raises_permanent_errors_and_queues_nothing(app, abdm, status):
    abdm.add(responses.POST, URL, json={"message": "invalid"}, status=status)
    with pytest.raises(AbdmUpstreamError) as exc:
        abdm_calls.send_or_queue(PATH, BODY)
    assert exc.value.status == status
    assert OutboxJob.query.count() == 0


def _run_worker_once():
    return worker.run(once=True)


def test_worker_sends_a_queued_call(app, abdm):
    abdm.add(responses.POST, URL, json={}, status=200)
    abdm_calls.enqueue_abdm_call(PATH, BODY, headers={"X-HIP-ID": "IN0110005723"})
    db.session.commit()
    assert _run_worker_once() == 1
    assert OutboxJob.query.one().status == "done"
    sent = [c for c in abdm.calls if c.request.url == URL][0]
    assert json.loads(sent.request.body) == BODY
    assert sent.request.headers["X-HIP-ID"] == "IN0110005723"
    assert sent.request.headers["Authorization"] == "Bearer gw-token"


def test_abdm_down_then_up_the_queued_reply_gets_through(app, abdm):
    abdm.add(responses.POST, URL, status=503, json={})
    abdm.add(responses.POST, URL, status=503, json={})
    abdm.add(responses.POST, URL, status=200, json={})
    assert abdm_calls.send_or_queue(PATH, BODY)["status"] == "queued"  # 1st failure, queued

    for _ in range(2):
        OutboxJob.query.update({"next_attempt_at": utcnow() - timedelta(seconds=1)})
        db.session.commit()
        _run_worker_once()
    job = OutboxJob.query.one()
    assert (job.status, job.attempts) == ("done", 2)


def test_worker_retries_temporary_errors_and_keeps_the_reason(app, abdm):
    abdm.add(responses.POST, URL, status=503, json={"message": "maintenance"})
    abdm_calls.enqueue_abdm_call(PATH, BODY)
    db.session.commit()
    _run_worker_once()
    job = OutboxJob.query.one()
    assert job.status == "pending" and "maintenance" in job.last_error and job.last_status == 502
    assert AbdmErrorLog.query.count() == 1  # the usual ABDM error bookkeeping still happens


def test_worker_gives_up_on_permanent_errors(app, abdm):
    abdm.add(responses.POST, URL, status=400, json={"message": "invalid payload"})
    abdm_calls.enqueue_abdm_call(PATH, BODY)
    db.session.commit()
    _run_worker_once()
    job = OutboxJob.query.one()
    assert (job.status, job.last_status) == ("dead", 400)


def test_worker_gives_up_after_all_attempts(app, abdm):
    abdm.add(responses.POST, URL, status=500, json={})
    job = abdm_calls.enqueue_abdm_call(PATH, BODY)
    db.session.commit()
    for _ in range(app.config["OUTBOX_MAX_ATTEMPTS"]):
        OutboxJob.query.update({"next_attempt_at": utcnow() - timedelta(seconds=1)})
        db.session.commit()
        _run_worker_once()
    assert db.session.get(OutboxJob, job.id).status == "dead"
    assert engine.process_due() == 0


@pytest.mark.parametrize("kind", ["missing", "not-encrypted", "not-json", "missing-keys", "wrong-key"])
def test_job_without_a_readable_payload_is_dead(app, abdm, monkeypatch, kind):
    payload_enc = {"missing": None, "not-encrypted": "not-encrypted-at-all", "not-json": vault.seal_text("not json"),
                   "missing-keys": vault.seal_text(json.dumps({"method": "POST"})),
                   "wrong-key": vault.seal_text(json.dumps({"method": "POST"}))}[kind]
    if kind == "wrong-key":  # the key changed since the job was queued
        import base64
        monkeypatch.setenv("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"z" * 32).decode())
    job = engine.enqueue("abdm_call", payload_enc=payload_enc)
    db.session.commit()
    _run_worker_once()
    assert db.session.get(OutboxJob, job.id).status == "dead"
    assert "no readable payload" in db.session.get(OutboxJob, job.id).last_error
    assert len(abdm.calls) == 0
