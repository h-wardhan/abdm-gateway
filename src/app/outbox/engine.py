"""
Outbox engine: enqueue a job, claim due jobs, run them, retry with backoff.

Claiming is a single atomic UPDATE (`... WHERE id=:id AND status='pending'`), so it works the same on SQLite
and Postgres and two workers can never run the same job. A worker that dies mid-job leaves it `processing`;
once `locked_until` passes, another worker takes it over.

A handler returns one of:
  Ok()                  job is done
  Retry(reason, status) try again later (network error, timeout, 5xx, 408, 429)
  Fail(reason, status)  give up now (the client rejected it, the HIP is gone, ...)
Retries follow OUTBOX_BACKOFF_SECONDS (with +-20% jitter) and stop after max_attempts: the job is then `dead`.
"""
import logging
import random
from dataclasses import dataclass
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, or_, update

from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models import callback_event as ce
from src.app.models import outbox_job as oj
from src.app.models.base import as_utc, utcnow

logger = logging.getLogger(__name__)

HANDLERS = {}


@dataclass
class Ok:
    pass


@dataclass
class Retry:
    reason: str
    status: int = None


@dataclass
class Fail:
    reason: str
    status: int = None


def handler(kind):
    """Register the function that runs jobs of `kind`."""
    def register(fn):
        HANDLERS[kind] = fn
        return fn
    return register


def backoff_delay(attempt, rng=random):
    """Seconds to wait after the `attempt`-th failed try (1 = first try)."""
    schedule = current_app.config["OUTBOX_BACKOFF_SECONDS"]
    base = schedule[min(attempt, len(schedule)) - 1]
    return base * rng.uniform(0.8, 1.2)


def enqueue(kind, *, callback_event_id=None, payload_enc=None, api_client_id=None, delay=0, max_attempts=None):
    job = OutboxJob(
        kind=kind,
        status=oj.PENDING,
        max_attempts=max_attempts or current_app.config["OUTBOX_MAX_ATTEMPTS"],
        next_attempt_at=utcnow() + timedelta(seconds=delay),
        callback_event_id=callback_event_id,
        payload_enc=payload_enc,
        api_client_id=api_client_id,
    )
    db.session.add(job)
    return job


def _claim(job_id, now):
    lock_until = now + timedelta(seconds=current_app.config["OUTBOX_LOCK_SECONDS"])
    result = db.session.execute(
        update(OutboxJob)
        .where(OutboxJob.id == job_id)
        .where(or_(
            (OutboxJob.status == oj.PENDING) & (OutboxJob.next_attempt_at <= now),
            (OutboxJob.status == oj.PROCESSING) & (OutboxJob.locked_until < now),
        ))
        .values(status=oj.PROCESSING, locked_until=lock_until, attempts=OutboxJob.attempts + 1)
        .execution_options(synchronize_session=False)  # the database decides the winner, not Python
    )
    db.session.commit()
    return result.rowcount == 1


def claim_due(limit):
    """Claim up to `limit` due jobs. Returns the claimed OutboxJob rows."""
    now = utcnow()
    ids = [row[0] for row in db.session.query(OutboxJob.id).filter(or_(
        (OutboxJob.status == oj.PENDING) & (OutboxJob.next_attempt_at <= now),
        (OutboxJob.status == oj.PROCESSING) & (OutboxJob.locked_until < now),
    )).order_by(OutboxJob.next_attempt_at).limit(limit).all()]
    claimed = []
    for job_id in ids:
        if _claim(job_id, now):
            db.session.expire_all()
            claimed.append(db.session.get(OutboxJob, job_id))
    return claimed


def _set_event_status(job, status, error=None):
    if job.callback_event_id is None:
        return
    event = db.session.get(CallbackEvent, job.callback_event_id)
    if event is None:
        return
    event.status = status
    event.error = error
    if status == ce.DELIVERED:
        event.delivered_at = utcnow()


def _finish(job, outcome):
    if isinstance(outcome, Ok):
        job.status = oj.DONE
        job.last_error = None
        job.last_status = None
        _set_event_status(job, ce.DELIVERED)
    elif isinstance(outcome, Retry) and job.attempts < job.max_attempts:
        job.status = oj.PENDING
        job.last_error, job.last_status = outcome.reason, outcome.status
        job.next_attempt_at = utcnow() + timedelta(seconds=backoff_delay(job.attempts))
    else:  # Fail, or Retry with no attempts left
        job.status = oj.DEAD
        job.last_error, job.last_status = outcome.reason, outcome.status
        _set_event_status(job, ce.FAILED, outcome.reason)
    job.locked_until = None
    db.session.commit()


def run_job(job):
    """Run one claimed job and record the outcome. Never raises."""
    fn = HANDLERS.get(job.kind)
    try:
        outcome = fn(job) if fn else Fail(f"no handler for job kind {job.kind!r}")
    except Exception as exc:  # noqa: BLE001  (a bad job must not stop the worker)
        db.session.rollback()
        logger.exception("Outbox job crashed", extra={"job_id": job.id, "kind": job.kind})
        outcome = Retry(f"handler error: {exc}")
    _finish(job, outcome)
    logger.info(
        "Outbox job %s %s -> %s",
        job.id, job.kind, job.status,
        extra={"job_id": job.id, "kind": job.kind, "job_status": job.status, "attempts": job.attempts},
    )
    return job


def stats():
    """How much work is waiting: {"pending": n, "processing": n, "dead": n, "oldestPendingSeconds": n or None}."""
    counts = dict(db.session.query(OutboxJob.status, func.count(OutboxJob.id)).group_by(OutboxJob.status).all())
    oldest = db.session.query(func.min(OutboxJob.next_attempt_at)).filter(OutboxJob.status == oj.PENDING).scalar()
    waiting = None
    if oldest is not None:
        waiting = max(0, int((utcnow() - as_utc(oldest)).total_seconds()))
    return {"pending": counts.get(oj.PENDING, 0), "processing": counts.get(oj.PROCESSING, 0),
            "dead": counts.get(oj.DEAD, 0), "oldestPendingSeconds": waiting}


def process_due(limit=None):
    """Claim and run due jobs. Returns how many were run."""
    jobs = claim_due(limit or current_app.config["OUTBOX_BATCH"])
    for job in jobs:
        run_job(job)
    return len(jobs)


from src.app.outbox import abdm_calls, handlers  # noqa: E402,F401  (registers the job handlers)
