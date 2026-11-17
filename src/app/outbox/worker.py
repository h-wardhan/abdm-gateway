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
from datetime import timedelta

from flask import current_app

from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models.base import as_utc, utcnow
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
REQUEST_LANE = "request"
OTHER_LANE = "other"
_LANES = {}  # job id -> (lane, key): a job waiting for its patient is looked at every pass; its lane does not change


def lane_of(job_id):
    """(lane, key). HIP consent notices of ONE patient go one at a time: i-medicus keeps the consent id and transaction of a
    care context on one row, so two notices of the same patient handled together overwrite each other. A health-information
    request is its own lane (key: the consent) and is held back until its notice is running (see request_ready). Notices of
    different patients and ABDM's other callbacks all run alongside."""
    import json
    from src.app.callbacks import vault
    from src.app.services import hip_pairing  # late: the service imports the engine
    row = (db.session.query(OutboxJob.hold_consent_id, CallbackEvent.path, CallbackEvent.body_enc)
           .outerjoin(CallbackEvent, OutboxJob.callback_event_id == CallbackEvent.id)
           .filter(OutboxJob.id == job_id).first())
    if not (row and row[0] and row[1]):
        return OTHER_LANE, None
    if row[1].endswith(hip_pairing.REQUEST):
        return REQUEST_LANE, str(row[0])
    if not row[1].endswith(hip_pairing.NOTICE):
        return OTHER_LANE, None
    try:
        patient = json.loads(vault.open_text(row[2]))["notification"]["consentDetail"]["patient"]["id"]
    except (ValueError, KeyError, TypeError):
        patient = None
    return NOTICE_LANE, str(patient or row[0])


def request_ready(consent_id):
    """May the health-information request of this consent go to i-medicus now? Not before its notice: i-medicus finds the
    request's rows by the consent id that the notice handler saves 5 s after it starts, and builds the records 16 s after.
    So: the notice is done, or has been running for HIP_PAIR_DELAY_SECONDS."""
    from src.app.services import hip_pairing  # late: the service imports the engine
    notice = (db.session.query(OutboxJob).join(CallbackEvent, OutboxJob.callback_event_id == CallbackEvent.id)
              .filter(OutboxJob.kind == "forward_callback", OutboxJob.hold_consent_id == consent_id,
                      CallbackEvent.path.like("%" + hip_pairing.NOTICE)).order_by(OutboxJob.id.desc()).first())
    if notice is None or notice.status not in ("pending", "processing"):
        return True  # no notice, or it has been delivered (or given up)
    if notice.status == "pending":
        return False
    started = as_utc(notice.updated_at)
    return utcnow() - started >= timedelta(seconds=current_app.config.get("HIP_PAIR_DELAY_SECONDS", 8))


def _run_in_thread(app, job_id):
    with app.app_context():
        job = db.session.get(OutboxJob, job_id)
        if job is not None:
            engine.run_job(job)
        db.session.remove()


def dispatch(app, pool, inflight, concurrency):
    """Claim due jobs for the free slots and hand them to the pool. `inflight` maps future -> (job id, lane, key).
    `concurrency` jobs besides the requests may run, and up to `concurrency` health-information requests beside them (a notice
    waits in a slot for its request, so the request must always find one). Returns how many were started."""
    for future in [f for f in inflight if f.done()]:
        inflight.pop(future)
    running = {job_id for job_id, _lane, _key in inflight.values()}
    busy = {key for _job_id, lane, key in inflight.values() if lane == NOTICE_LANE}
    requests = sum(1 for _j, lane, _k in inflight.values() if lane == REQUEST_LANE)
    others = len(inflight) - requests
    started = 0
    seen = set()
    # Walk the queue in batches until the slots are full or nothing new is due. Jobs that must wait (a patient whose notice is
    # already running, a request whose notice has not started) are skipped, but they must not hide the jobs behind them: 100
    # queued notices of one patient used to fill the window and starve everything else.
    for _round in range(50):
        batch = engine.due_ids(concurrency * 4 + 8, exclude=seen)
        if not batch or (others >= concurrency and requests >= concurrency):
            break
        for job_id in batch:
            seen.add(job_id)
            if job_id in running:
                continue
            if job_id not in _LANES:
                if len(_LANES) > 20000:
                    _LANES.clear()
                _LANES[job_id] = lane_of(job_id)
            lane, key = _LANES[job_id]
            if lane == REQUEST_LANE:
                if requests >= concurrency or not request_ready(key):
                    continue
            else:
                if others >= concurrency or (lane == NOTICE_LANE and key in busy):
                    continue
            if engine._claim(job_id, utcnow()):
                _LANES.pop(job_id, None)
                inflight[pool.submit(_run_in_thread, app, job_id)] = (job_id, lane, key)
                started += 1
                if lane == REQUEST_LANE:
                    requests += 1
                else:
                    others += 1
                    if lane == NOTICE_LANE:
                        busy.add(key)
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
    pool = ThreadPoolExecutor(max_workers=concurrency * 2, thread_name_prefix="outbox")
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
