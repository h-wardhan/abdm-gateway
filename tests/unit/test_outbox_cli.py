import importlib
from datetime import timedelta

import pytest

from src.app.callbacks import registry, vault
from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models.base import utcnow
from src.app.outbox import engine
from tests.conftest import CALLBACK_URL, HIP_ID


@pytest.fixture
def run(app):
    runner = app.test_cli_runner()
    return lambda *args: runner.invoke(args=list(args))


def _event(status="received", hip_id=HIP_ID, error=None, request_id="req-0001-abcdef"):
    event = CallbackEvent(request_id=request_id, path="api/v3/hip/patient/share", hip_id=hip_id, status=status,
                          body_enc=vault.seal_text("{}"), error=error)
    db.session.add(event)
    db.session.commit()
    return event


def _job(status="pending", event=None, **fields):
    job = engine.enqueue("forward_callback", callback_event_id=event.id if event else None)
    job.status = status
    for key, value in fields.items():
        setattr(job, key, value)
    db.session.commit()
    return job


# ---- engine.stats ---------------------------------------------------------------------------------------------

def test_stats_when_empty(app):
    assert engine.stats() == {"pending": 0, "processing": 0, "dead": 0, "oldestPendingSeconds": None}


def test_stats_counts_and_oldest_waiting_time(app):
    old = _job(next_attempt_at=utcnow() - timedelta(seconds=120))
    _job()
    _job(status="processing")
    _job(status="dead")
    _job(status="dead")
    _job(status="done")
    stats = engine.stats()
    assert (stats["pending"], stats["processing"], stats["dead"]) == (2, 1, 2)
    assert 115 <= stats["oldestPendingSeconds"] <= 130
    assert old.id


def test_oldest_pending_is_never_negative_for_future_jobs(app):
    _job(next_attempt_at=utcnow() + timedelta(hours=1))
    assert engine.stats()["oldestPendingSeconds"] == 0


# ---- flask outbox ---------------------------------------------------------------------------------------------

def test_outbox_stats_command(run, app):
    _job(next_attempt_at=utcnow() - timedelta(seconds=30))
    _job(status="dead")
    _event(status="unrouted", request_id="req-unrouted-001")
    res = run("outbox", "stats")
    assert res.exit_code == 0, res.output
    assert "pending   : 1 (oldest waiting" in res.output
    assert "dead      : 1" in res.output and "unrouted callbacks: 1" in res.output


def test_outbox_stats_with_nothing_pending(run, app):
    res = run("outbox", "stats")
    assert "pending   : 0\n" in res.output and "oldest" not in res.output


def test_outbox_list(run, app):
    event = _event()
    _job(status="dead", event=event, attempts=6, last_error="client app returned HTTP 500")
    _job(status="done")
    everything = run("outbox", "list")
    assert everything.exit_code == 0 and "dead" in everything.output and "done" in everything.output
    only_dead = run("outbox", "list", "--status", "dead")
    assert "client app returned HTTP 500" in only_dead.output and "done" not in only_dead.output
    assert "6/6" in only_dead.output


def test_outbox_list_limit_and_empty(run, app):
    assert "No jobs." in run("outbox", "list").output
    for _ in range(3):
        _job()
    lines = [line for line in run("outbox", "list", "--limit", "2").output.splitlines() if "forward_callback" in line]
    assert len(lines) == 2


def test_outbox_retry_revives_a_dead_job_and_its_event(run, app):
    event = _event(status="failed", error="client app returned HTTP 500")
    job = _job(status="dead", event=event, attempts=6, last_error="client app returned HTTP 500", last_status=500)
    res = run("outbox", "retry", str(job.id))
    assert res.exit_code == 0, res.output
    job = db.session.get(OutboxJob, job.id)
    assert (job.status, job.attempts, job.last_error, job.last_status) == ("pending", 0, None, None)
    event = db.session.get(CallbackEvent, event.id)
    assert (event.status, event.error) == ("received", None)
    assert engine.claim_due(5)[0].id == job.id  # due right away


def test_outbox_retry_job_without_an_event(run, app):
    job = _job(status="dead")
    assert run("outbox", "retry", str(job.id)).exit_code == 0


def test_outbox_retry_only_for_dead_jobs(run, app):
    job = _job(status="pending")
    res = run("outbox", "retry", str(job.id))
    assert res.exit_code != 0 and "only dead jobs" in res.output


def test_outbox_retry_unknown_job(run, app):
    res = run("outbox", "retry", "999")
    assert res.exit_code != 0 and "No job 999" in res.output


# ---- flask callbacks ------------------------------------------------------------------------------------------

def test_callbacks_list(run, app):
    _event(status="delivered", request_id="req-delivered-01")
    _event(status="unrouted", hip_id="IN-UNKNOWN-1", error="hip is not registered", request_id="req-unrouted-01")
    everything = run("callbacks", "list")
    assert everything.exit_code == 0 and "delivered" in everything.output and "IN-UNKNOWN-1" in everything.output
    unrouted = run("callbacks", "list", "--status", "unrouted")
    assert "hip is not registered" in unrouted.output and "delivered" not in unrouted.output


def test_callbacks_list_event_without_hip_and_empty(run, app):
    assert "No callbacks." in run("callbacks", "list").output
    _event(status="unrouted", hip_id=None)
    assert " - " in run("callbacks", "list").output


def test_reroute_queues_an_unrouted_callback(run, hip):
    event = _event(status="unrouted", hip_id="IN-NOT-REGISTERED-YET", error="hip is not registered")
    res = run("callbacks", "reroute", str(event.id), "--hip-id", HIP_ID)
    assert res.exit_code == 0, res.output
    event = db.session.get(CallbackEvent, event.id)
    assert (event.status, event.hip_id, event.api_client_id, event.error) == ("received", HIP_ID, hip.api_client_id, None)
    job = OutboxJob.query.one()
    assert (job.kind, job.status, job.callback_event_id) == ("forward_callback", "pending", event.id)


def test_reroute_unknown_callback(run, hip):
    res = run("callbacks", "reroute", "999", "--hip-id", HIP_ID)
    assert res.exit_code != 0 and "No callback 999" in res.output


def test_reroute_only_unrouted(run, hip):
    event = _event(status="failed")
    res = run("callbacks", "reroute", str(event.id), "--hip-id", HIP_ID)
    assert res.exit_code != 0 and "only unrouted" in res.output and OutboxJob.query.count() == 0


def test_reroute_to_unknown_or_disabled_hip(run, hip):
    event = _event(status="unrouted")
    assert "No HIP" in run("callbacks", "reroute", str(event.id), "--hip-id", "IN-NOPE-0001").output
    registry.disable(hip)
    res = run("callbacks", "reroute", str(event.id), "--hip-id", HIP_ID)
    assert res.exit_code != 0 and "disabled" in res.output
    assert OutboxJob.query.count() == 0 and CALLBACK_URL


# ---- /ready ---------------------------------------------------------------------------------------------------

def test_ready_reports_the_backlog(client, app):
    _job()
    _job(status="dead")
    data = client.get("/ready").get_json()["data"]
    assert data["outbox"]["pending"] == 1 and data["outbox"]["dead"] == 1
    assert data["database"] == "ok"


def test_a_backlog_problem_does_not_fail_readiness(client, app, monkeypatch):
    def broken():
        raise RuntimeError("no such table")

    # `src.app.api` is shadowed by the restx Api object on the package, so fetch the module explicitly
    monkeypatch.setattr(importlib.import_module("src.app.api.health"), "outbox_stats", broken)
    res = client.get("/ready")
    assert res.status_code == 200 and res.get_json()["data"]["outbox"] == "unknown"
