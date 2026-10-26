"""The forward_callback job: delivering a stored callback to the client app's webhook."""
import json
from datetime import timedelta

import pytest
import requests
import responses

from src.app.callbacks import forwarder, vault
from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models.base import as_utc, utcnow
from src.app.outbox import engine
from tests.conftest import CALLBACK_URL, HIP_ID

TARGET = f"{CALLBACK_URL}/api/v3/hip/patient/share"
BODY = {"metaData": {"hipId": HIP_ID}, "profile": {"patient": {"abhaAddress": "ravi@sbx"}}}


def _queue(hip, hip_id=HIP_ID, **event_overrides):
    event = CallbackEvent(
        request_id="req-0001-abcdef", path="api/v3/hip/patient/share", hip_id=hip_id, api_client_id=hip.api_client_id,
        status="received", body_enc=vault.seal_text(json.dumps(BODY)),
        headers={"REQUEST-ID": "req-0001-abcdef", "X-CM-ID": "sbx"},
    )
    for key, value in event_overrides.items():
        setattr(event, key, value)
    db.session.add(event)
    db.session.flush()
    job = engine.enqueue("forward_callback", callback_event_id=event.id, api_client_id=hip.api_client_id)
    db.session.commit()
    return event, job


def _run_once():
    [job] = engine.claim_due(1)
    engine.run_job(job)
    return job


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_2xx_delivers(app, http, hip, status):
    http.add(responses.POST, TARGET, status=status)
    event, _ = _queue(hip)
    job = _run_once()
    assert job.status == "done"
    assert db.session.get(CallbackEvent, event.id).status == "delivered"


def test_delivered_request_is_what_the_client_expects(app, http, hip):
    http.add(responses.POST, TARGET, json={"status": "success"})
    event, _ = _queue(hip)
    _run_once()
    [call] = http.calls
    assert call.request.url == TARGET
    assert json.loads(call.request.body) == BODY
    headers = call.request.headers
    assert headers["request-id"] == "req-0001-abcdef" and headers["x-cm-id"] == "sbx"
    assert headers["X-Gateway-Event-Id"] == str(event.id)
    expected = forwarder.sign(hip.test_secret, headers["X-Gateway-Timestamp"], call.request.body)
    assert headers["X-Gateway-Signature"] == expected


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_temporary_client_errors_are_retried(app, http, hip, status):
    http.add(responses.POST, TARGET, status=status)
    event, _ = _queue(hip)
    job = _run_once()
    assert (job.status, job.last_status) == ("pending", status)
    assert as_utc(job.next_attempt_at) > utcnow()
    assert db.session.get(CallbackEvent, event.id).status == "received"


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 403, 404, 410, 422])
def test_permanent_client_errors_are_not_retried(app, http, hip, status):
    http.add(responses.POST, TARGET, status=status, headers={"Location": "https://elsewhere.example.test/"})
    event, _ = _queue(hip)
    job = _run_once()
    assert (job.status, job.last_status) == ("dead", status)
    assert db.session.get(CallbackEvent, event.id).status == "failed"
    assert len(http.calls) == 1  # redirects are never followed


def test_timeout_is_retried(app, http, hip):
    http.add(responses.POST, TARGET, body=requests.Timeout("slow"))
    _queue(hip)
    job = _run_once()
    assert job.status == "pending" and "timed out" in job.last_error


def test_connection_error_is_retried(app, http, hip):
    http.add(responses.POST, TARGET, body=requests.ConnectionError("refused"))
    _queue(hip)
    job = _run_once()
    assert job.status == "pending" and "ConnectionError" in job.last_error


def test_event_missing_is_dead(app, http, hip):
    event, _ = _queue(hip)
    OutboxJob.query.update({"callback_event_id": None})
    db.session.commit()
    job = _run_once()
    assert job.status == "dead" and job.last_error == "callback event not found"


def test_event_without_hip_id_is_dead(app, http, hip):
    _queue(hip, hip_id=None)
    assert _run_once().last_error == "hip is not registered"


def test_hip_removed_is_dead(app, http, hip):
    _queue(hip, hip_id="IN-GONE-0001")
    assert _run_once().last_error == "hip is not registered"


def test_hip_disabled_after_queueing_is_dead(app, http, hip):
    _queue(hip)
    hip.is_active = False
    db.session.commit()
    assert _run_once().last_error == "hip is disabled"
    assert len(http.calls) == 0


def test_url_that_became_blocked_is_dead_and_not_called(app, http, hip):
    _queue(hip)
    hip.callback_url = "https://app.i-medicus.in/api/abdm-webhook"
    db.session.commit()
    job = _run_once()
    assert job.status == "dead" and "blocked" in job.last_error
    assert len(http.calls) == 0


def test_url_that_now_resolves_to_a_private_address_is_dead(app, http, hip, fake_dns):
    _queue(hip)
    fake_dns["client.example.test"] = "10.9.9.9"  # DNS now points inside our network
    job = _run_once()
    assert job.status == "dead" and "private" in job.last_error
    assert len(http.calls) == 0


def test_url_that_does_not_resolve_right_now_is_retried(app, http, hip, fake_dns):
    _queue(hip)
    del fake_dns["client.example.test"]
    job = _run_once()
    assert job.status == "pending" and "does not resolve" in job.last_error


def test_fails_twice_then_succeeds_on_the_third_attempt(app, http, hip):
    http.add(responses.POST, TARGET, status=503)
    http.add(responses.POST, TARGET, status=503)
    http.add(responses.POST, TARGET, status=200)
    event, job = _queue(hip)
    for attempt in range(3):
        db.session.get(OutboxJob, job.id).next_attempt_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        _run_once()
    job = db.session.get(OutboxJob, job.id)
    assert (job.status, job.attempts) == ("done", 3)
    assert db.session.get(CallbackEvent, event.id).status == "delivered"
    assert len(http.calls) == 3


def test_gives_up_after_the_configured_attempts(app, http, hip):
    http.add(responses.POST, TARGET, status=500)
    event, job = _queue(hip)
    for _ in range(app.config["OUTBOX_MAX_ATTEMPTS"]):
        db.session.get(OutboxJob, job.id).next_attempt_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        _run_once()
    job = db.session.get(OutboxJob, job.id)
    assert (job.status, job.attempts) == ("dead", 6)
    assert db.session.get(CallbackEvent, event.id).status == "failed"
    assert engine.process_due() == 0  # a dead job is never picked up again
