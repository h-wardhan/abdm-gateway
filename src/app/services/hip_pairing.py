"""
Hand a GRANTED consent notice and the health-information request of that consent to i-medicus together (M2, HIP).

What i-medicus does today (WebhookTrait, line numbers as of Nov 2026): on `consent/request/hip/notify` it stores the
consent id, acknowledges, SLEEPS 10 SECONDS, then builds, encrypts and pushes the records (ConvertRecordToFHIR) with
the keys of the health-information request. Those keys are made when `hip/health-information/request` arrives and finds
the consent id. When the request arrives later than the sleep (the HIU fetches the consent first: 25-30 seconds on the
sandbox) there are no keys, `fhir/encrypt` is called with an empty private key and the records are never pushed.

So: the notice of a GRANTED consent waits (at most HIP_CONSENT_HOLD_SECONDS) for the request of that consent. When the
request arrives, the notice is released at once and the request follows a few seconds later, inside i-medicus' sleep:
the request is delivered by a timer thread because the worker is busy with the notice for its whole sleep.
Without a request the notice is delivered after the hold, so nothing is lost.
"""
import logging
import threading
from datetime import timedelta

from flask import current_app

from src.app.callbacks import vault
from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models import outbox_job as oj
from src.app.models.base import utcnow
from src.app.outbox import engine

logger = logging.getLogger(__name__)

NOTICE = "consent/request/hip/notify"
REQUEST = "hip/health-information/request"


def _body(raw):
    import json
    try:
        data = json.loads(vault.open_text(raw))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _notice_consent(body):
    notification = (body or {}).get("notification") or {}
    if str(notification.get("status") or "").upper() != "GRANTED":
        return None
    return str(notification["consentId"]) if notification.get("consentId") else None


def _request_consent(body):
    consent = ((body or {}).get("hiRequest") or {}).get("consent") or {}
    return str(consent["id"]) if consent.get("id") else None


def _held(consent_id, path_suffix):
    """Pending forward jobs waiting on `consent_id` whose callback path ends with `path_suffix`."""
    rows = (db.session.query(OutboxJob, CallbackEvent).join(CallbackEvent, OutboxJob.callback_event_id == CallbackEvent.id)
            .filter(OutboxJob.kind == oj.KIND_FORWARD_CALLBACK, OutboxJob.status == oj.PENDING,
                    OutboxJob.hold_consent_id == consent_id).all())
    return [job for job, event in rows if event.path.endswith(path_suffix)]


def enqueue_forward(event, body, path, api_client_id):
    """Queue the delivery of a callback to the client app; the two callbacks above may wait for each other."""
    hold = current_app.config["HIP_CONSENT_HOLD_SECONDS"]
    if hold > 0 and isinstance(body, dict):
        if path.endswith(NOTICE) and _notice_consent(body):
            job = engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=api_client_id,
                                 delay=hold)
            job.hold_consent_id = _notice_consent(body)
            return job
        consent_id = _request_consent(body) if path.endswith(REQUEST) else None
        if consent_id:
            notices = _held(consent_id, NOTICE)
            if notices:  # release the notices now; the request waits and is handed over with them
                for notice in notices:
                    notice.next_attempt_at = utcnow()
                job = engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id,
                                     api_client_id=api_client_id, delay=hold)
                job.hold_consent_id = consent_id
                return job
    return engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=api_client_id)


def _run_claimed(app, job_id):
    with app.app_context():
        job = db.session.get(OutboxJob, job_id)
        if job is not None:
            engine.run_job(job)


def _spawn(delay, fn, *args):
    timer = threading.Timer(delay, fn, args=args)
    timer.daemon = True
    timer.start()


def claim_requests_of(job):
    """Called by the worker before it delivers a held notice: take the held requests of the same consent and deliver
    them after HIP_PAIR_DELAY_SECONDS from a timer thread. Returns how many were taken."""
    if not job.hold_consent_id:
        return 0
    event = db.session.get(CallbackEvent, job.callback_event_id) if job.callback_event_id else None
    if event is None or not event.path.endswith(NOTICE):
        return 0
    taken = 0
    app = current_app._get_current_object()
    for request_job in _held(job.hold_consent_id, REQUEST):
        request_job.next_attempt_at = utcnow()
        db.session.commit()
        if engine._claim(request_job.id, utcnow() + timedelta(seconds=1)):
            _spawn(current_app.config["HIP_PAIR_DELAY_SECONDS"], _run_claimed, app, request_job.id)
            taken += 1
    return taken
