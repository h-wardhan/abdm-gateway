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


def _patient_of_notice(body_enc):
    import json
    from src.app.callbacks import vault
    try:
        return str(json.loads(vault.open_text(body_enc))["notification"]["consentDetail"]["patient"]["id"])
    except (ValueError, KeyError, TypeError):
        return None


def _patient_of_consent(consent_id):
    """The patient a consent belongs to: read from the stored consent notice of that consent."""
    from src.app.services import hip_pairing  # late: the service imports the engine
    row = (db.session.query(CallbackEvent.body_enc)
           .join(OutboxJob, OutboxJob.callback_event_id == CallbackEvent.id)
           .filter(OutboxJob.kind == "forward_callback", OutboxJob.hold_consent_id == consent_id,
                   CallbackEvent.path.like("%" + hip_pairing.NOTICE)).order_by(OutboxJob.id.desc()).first())
    return _patient_of_notice(row[0]) if row else None


def lane_of(job_id):
    """(lane, key, patient, consent). i-medicus keeps the consent id and the transaction of a care context on ONE row, so the
    notice and the health-information request handlers of one patient must not overlap: a notice of the same patient
    overwrites the consent id while the request of the previous consent is still being handled (the records then go out
    with another consent's transaction: ABDM 403 / "Invalid Consent artefact id"). So per PATIENT: one notice or one request
    at a time, except a request beside its own notice (the older i-medicus handled both together). A health-information
    request is held back until its notice is running or done (see request_ready). Different patients, and ABDM's other
    callbacks, all run alongside. With HIP_ONE_NOTICE_PER_CLIENT the notices of a client also go one at a time."""
    from src.app.services import hip_pairing  # late: the service imports the engine
    row = (db.session.query(OutboxJob.hold_consent_id, CallbackEvent.path, CallbackEvent.body_enc, OutboxJob.api_client_id)
           .outerjoin(CallbackEvent, OutboxJob.callback_event_id == CallbackEvent.id)
           .filter(OutboxJob.id == job_id).first())
    if not (row and row[0] and row[1]):
        return OTHER_LANE, None, None, None
    consent = str(row[0])
    if row[1].endswith(hip_pairing.REQUEST):
        return REQUEST_LANE, consent, _patient_of_consent(consent), consent
    if not row[1].endswith(hip_pairing.NOTICE):
        return OTHER_LANE, None, None, None
    patient = _patient_of_notice(row[2])
    if current_app.config.get("HIP_ONE_NOTICE_PER_CLIENT"):
        return NOTICE_LANE, f"client:{row[3]}", patient, consent
    return NOTICE_LANE, str(patient or consent), patient, consent


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


def _patient_free(patient, lane, consent, flying):
    """May a job of this patient start beside the ones in flight? A notice waits for every request of the patient; a request waits
    for the other requests and for the notices of other consents (its own notice may still be running)."""
    if not patient:
        return True
    for _job, other_lane, _key, other_patient, other_consent in flying:
        if other_patient != patient or other_lane == OTHER_LANE:
            continue
        if lane == NOTICE_LANE or other_lane == REQUEST_LANE or other_consent != consent:
            return False
    return True


def dispatch(app, pool, inflight, concurrency):
    """Claim due jobs for the free slots and hand them to the pool. `inflight` maps future -> (job id, lane, key, patient,
    consent). `concurrency` jobs besides the requests may run, and up to `concurrency` health-information requests beside them
    (a notice may wait in a slot for its request, so the request must always find one). Returns how many were started."""
    for future in [f for f in inflight if f.done()]:
        inflight.pop(future)
    flying = list(inflight.values())
    running = {item[0] for item in flying}
    busy = {item[2] for item in flying if item[1] == NOTICE_LANE}
    requests = sum(1 for item in flying if item[1] == REQUEST_LANE)
    others = len(flying) - requests
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
            lane, key, patient, consent = _LANES[job_id]
            if lane == REQUEST_LANE:
                if requests >= concurrency or not _patient_free(patient, lane, consent, flying) or not request_ready(key):
                    continue
            else:
                if others >= concurrency or (lane == NOTICE_LANE and (key in busy or not _patient_free(patient, lane, consent, flying))):
                    continue
            if engine._claim(job_id, utcnow()):
                _LANES.pop(job_id, None)
                item = (job_id, lane, key, patient, consent)
                inflight[pool.submit(_run_in_thread, app, job_id)] = item
                flying.append(item)
                running.add(job_id)
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
