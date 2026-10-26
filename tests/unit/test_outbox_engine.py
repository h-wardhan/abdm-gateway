from datetime import timedelta

import pytest

from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models.base import as_utc, utcnow
from src.app.outbox import engine
from src.app.outbox.engine import Fail, Ok, Retry


class FixedRng:
    def __init__(self, factor):
        self.factor = factor

    def uniform(self, low, high):
        return self.factor


def _job(kind="test_kind", **overrides):
    job = engine.enqueue(kind, **overrides)
    db.session.commit()
    return job


def _event():
    event = CallbackEvent(request_id="req-0001-abcdef", path="api/v3/x", status="received", body_enc="x")
    db.session.add(event)
    db.session.commit()
    return event


@pytest.fixture
def handlers(monkeypatch):
    """Replace the job handlers with controllable ones."""
    registry = {}
    monkeypatch.setattr(engine, "HANDLERS", registry)
    return registry


def test_enqueue_defaults(app):
    job = _job()
    assert (job.status, job.attempts, job.max_attempts) == ("pending", 0, 6)
    assert as_utc(job.next_attempt_at) <= utcnow()


def test_enqueue_delay_and_custom_max_attempts(app):
    job = _job(delay=60, max_attempts=2)
    assert job.max_attempts == 2
    assert as_utc(job.next_attempt_at) > utcnow() + timedelta(seconds=50)


def test_backoff_follows_the_schedule(app):
    expected = [2, 10, 60, 300, 900, 3600]
    assert [engine.backoff_delay(n, FixedRng(1.0)) for n in range(1, 7)] == expected


def test_backoff_stays_at_the_last_step_after_the_schedule_ends(app):
    assert engine.backoff_delay(50, FixedRng(1.0)) == 3600


def test_backoff_has_jitter_within_20_percent(app):
    for _ in range(50):
        assert 8 <= engine.backoff_delay(2) <= 12


def test_claim_due_picks_only_due_pending_jobs(app):
    due = _job()
    _job(delay=3600)  # not due yet
    finished = _job()
    finished.status = "done"
    dead = _job()
    dead.status = "dead"
    db.session.commit()

    claimed = engine.claim_due(10)
    assert [j.id for j in claimed] == [due.id]
    assert claimed[0].status == "processing" and claimed[0].attempts == 1
    assert as_utc(claimed[0].locked_until) > utcnow()


def test_claim_due_respects_the_limit_and_order(app):
    first, second, third = _job(), _job(), _job()
    first.next_attempt_at = utcnow() - timedelta(seconds=30)
    second.next_attempt_at = utcnow() - timedelta(seconds=20)
    third.next_attempt_at = utcnow() - timedelta(seconds=10)
    db.session.commit()
    assert [j.id for j in engine.claim_due(2)] == [first.id, second.id]


def test_a_claimed_job_is_not_claimed_again(app):
    _job()
    assert len(engine.claim_due(10)) == 1
    assert engine.claim_due(10) == []


def test_two_workers_cannot_claim_the_same_job(app):
    job = _job()
    now = utcnow()
    assert engine._claim(job.id, now) is True
    assert engine._claim(job.id, now) is False  # the second worker loses the race


def test_job_of_a_crashed_worker_is_taken_over_after_the_lock_expires(app):
    job = _job()
    engine.claim_due(10)
    assert engine.claim_due(10) == []  # lock still valid

    job = db.session.get(OutboxJob, job.id)
    job.locked_until = utcnow() - timedelta(seconds=1)
    db.session.commit()
    taken = engine.claim_due(10)
    assert [j.id for j in taken] == [job.id] and taken[0].attempts == 2


def test_run_job_ok_marks_done_and_the_event_delivered(app, handlers):
    handlers["test_kind"] = lambda job: Ok()
    event = _event()
    job = _job(callback_event_id=event.id)
    [job] = engine.claim_due(1)
    engine.run_job(job)
    assert (job.status, job.locked_until, job.last_error) == ("done", None, None)
    event = db.session.get(CallbackEvent, event.id)
    assert event.status == "delivered" and event.delivered_at is not None


def test_run_job_retry_reschedules_with_backoff(app, handlers):
    handlers["test_kind"] = lambda job: Retry("client app returned HTTP 503", 503)
    event = _event()
    _job(callback_event_id=event.id)
    [job] = engine.claim_due(1)
    engine.run_job(job)
    assert (job.status, job.attempts, job.last_error, job.last_status) == ("pending", 1, "client app returned HTTP 503", 503)
    assert as_utc(job.next_attempt_at) > utcnow()
    assert db.session.get(CallbackEvent, event.id).status == "received"  # not given up yet


def test_run_job_gives_up_after_max_attempts(app, handlers):
    handlers["test_kind"] = lambda job: Retry("still down")
    event = _event()
    job = _job(callback_event_id=event.id, max_attempts=2)
    for _ in range(2):
        job = db.session.get(OutboxJob, job.id)
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        [claimed] = engine.claim_due(1)
        engine.run_job(claimed)
    job = db.session.get(OutboxJob, job.id)
    assert (job.status, job.attempts, job.last_error) == ("dead", 2, "still down")
    event = db.session.get(CallbackEvent, event.id)
    assert (event.status, event.error) == ("failed", "still down")


def test_run_job_fail_is_dead_immediately(app, handlers):
    handlers["test_kind"] = lambda job: Fail("client app returned HTTP 400", 400)
    job = _job()
    [job] = engine.claim_due(1)
    engine.run_job(job)
    assert (job.status, job.attempts, job.last_status) == ("dead", 1, 400)


def test_unknown_job_kind_is_dead(app, handlers):
    job = _job(kind="never_registered")
    [job] = engine.claim_due(1)
    engine.run_job(job)
    assert job.status == "dead" and "no handler" in job.last_error


def test_a_crashing_handler_is_retried_not_raised(app, handlers):
    def boom(job):
        raise RuntimeError("kaboom")

    handlers["test_kind"] = boom
    _job()
    [job] = engine.claim_due(1)
    engine.run_job(job)
    assert job.status == "pending" and "kaboom" in job.last_error


def test_job_without_an_event_or_with_a_deleted_event(app, handlers):
    handlers["test_kind"] = lambda job: Ok()
    no_event = _job()
    event = _event()
    orphan = _job(callback_event_id=event.id)
    db.session.delete(db.session.get(CallbackEvent, event.id))
    db.session.commit()
    for job in engine.claim_due(10):
        engine.run_job(job)
    assert db.session.get(OutboxJob, no_event.id).status == "done"
    assert db.session.get(OutboxJob, orphan.id) is None or db.session.get(OutboxJob, orphan.id).status == "done"


def test_event_deleted_after_claim_is_ignored(app, handlers):
    handlers["test_kind"] = lambda job: Ok()
    event = _event()
    job = _job(callback_event_id=event.id)
    [job] = engine.claim_due(1)
    db.session.query(CallbackEvent).delete()
    db.session.commit()
    engine._set_event_status(db.session.get(OutboxJob, job.id) or job, "delivered")  # must not raise


def test_process_due_runs_everything_due(app, handlers):
    done = []
    handlers["test_kind"] = lambda job: done.append(job.id) or Ok()
    ids = [_job().id for _ in range(3)]
    _job(delay=3600)
    assert engine.process_due() == 3
    assert sorted(done) == sorted(ids)
    assert engine.process_due() == 0


def test_process_due_honours_the_batch_size(app, handlers):
    handlers["test_kind"] = lambda job: Ok()
    for _ in range(5):
        _job()
    assert engine.process_due(limit=2) == 2
    app.config["OUTBOX_BATCH"] = 1
    assert engine.process_due() == 1


def test_handler_decorator_registers(app, handlers):
    @engine.handler("decorated")
    def fn(job):
        return Ok()

    assert handlers["decorated"] is fn


def test_job_claimed_by_someone_else_in_between_is_skipped(app, monkeypatch):
    mine, _ = _job(), _job()
    monkeypatch.setattr(engine, "_claim", lambda job_id, now: job_id == mine.id)  # another worker won the other job
    assert [j.id for j in engine.claim_due(10)] == [mine.id]
