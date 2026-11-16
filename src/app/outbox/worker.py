"""
The outbox worker: runs due jobs in a loop. Start it next to the web app:

    flask worker run          # loops until stopped (SIGTERM / Ctrl+C finish the current batch first)
    flask worker run --once   # one pass, for cron and tests

Several workers can run at once; each job is claimed atomically (see engine.py).
"""
import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor

from flask import current_app

from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models.base import utcnow
from src.app.outbox import engine

logger = logging.getLogger("abdm_gateway.worker")


class Stopper:
    """Flips to stopped on SIGTERM / SIGINT so the loop ends after the current batch."""

    def __init__(self):
        self.stopped = False

    def stop(self, *_args):
        self.stopped = True

    def install(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)


def tick():
    """One pass. Returns the number of jobs run; a failing pass is logged, never raised."""
    try:
        return engine.process_due()
    except Exception:  # noqa: BLE001  (database hiccup: keep the worker alive)
        db.session.rollback()
        logger.exception("Worker pass failed")
        return 0


NOTICE_LANE = "notice"
OTHER_LANE = "other"


def lane_of(job_id):
    """(lane, key). HIP consent notices of ONE patient go one at a time: i-medicus keeps the consent id and transaction of a
    care context on one row, so two notices of the same patient handled together overwrite each other. Notices of different
    patients, ABDM's other callbacks and the health-information request that must be handled while a notice waits for it
    all run alongside."""
    import json
    from src.app.callbacks import vault
    from src.app.services import hip_pairing  # late: the service imports the engine
    row = (db.session.query(OutboxJob.hold_consent_id, CallbackEvent.path, CallbackEvent.body_enc)
           .outerjoin(CallbackEvent, OutboxJob.callback_event_id == CallbackEvent.id)
           .filter(OutboxJob.id == job_id).first())
    if not (row and row[0] and row[1] and row[1].endswith(hip_pairing.NOTICE)):
        return OTHER_LANE, None
    try:
        patient = json.loads(vault.open_text(row[2]))["notification"]["consentDetail"]["patient"]["id"]
    except (ValueError, KeyError, TypeError):
        patient = None
    return NOTICE_LANE, str(patient or row[0])


def _run_in_thread(app, job_id):
    with app.app_context():
        job = db.session.get(OutboxJob, job_id)
        if job is not None:
            engine.run_job(job)
        db.session.remove()


def dispatch(app, pool, inflight, concurrency):
    """Claim due jobs for the free slots and hand them to the pool. `inflight` maps future -> (job id, lane, key).
    Returns how many were started."""
    for future in [f for f in inflight if f.done()]:
        inflight.pop(future)
    running = {job_id for job_id, _lane, _key in inflight.values()}
    busy = {key for _job_id, lane, key in inflight.values() if lane == NOTICE_LANE}
    started = 0
    for job_id in engine.due_ids(concurrency * 4 + 8):
        if len(inflight) >= concurrency:
            break
        if job_id in running:
            continue
        lane, key = lane_of(job_id)
        if lane == NOTICE_LANE and key in busy:
            continue
        if engine._claim(job_id, utcnow()):
            inflight[pool.submit(_run_in_thread, app, job_id)] = (job_id, lane, key)
            if lane == NOTICE_LANE:
                busy.add(key)
            started += 1
    return started


def run(once=False, stopper=None, sleep=time.sleep):
    stopper = stopper or Stopper()
    poll = current_app.config["OUTBOX_POLL_SECONDS"]
    concurrency = current_app.config["WORKER_CONCURRENCY"]
    logger.info("Outbox worker started", extra={"once": once, "concurrency": concurrency})
    total = 0
    if once or concurrency <= 1:
        while not stopper.stopped:
            done = tick()
            total += done
            if once:
                break
            if done == 0 and not stopper.stopped:
                sleep(poll)
        logger.info("Outbox worker stopped", extra={"jobs_run": total})
        return total

    app = current_app._get_current_object()
    pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="outbox")
    inflight = {}
    try:
        while not stopper.stopped:
            try:
                started = dispatch(app, pool, inflight, concurrency)
            except Exception:  # noqa: BLE001  (database hiccup: keep the worker alive)
                db.session.rollback()
                logger.exception("Worker pass failed")
                started = 0
            total += started
            if started == 0 and not stopper.stopped:
                sleep(poll)
    finally:
        pool.shutdown(wait=True)  # let the jobs that run finish
    logger.info("Outbox worker stopped", extra={"jobs_run": total})
    return total
