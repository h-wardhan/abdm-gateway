"""
Make i-medicus build the records with the transaction and keys of the health-information request (M2, HIP).

What i-medicus does today (WebhookTrait::onRequestHipNotify, line numbers as of Nov 2026), on `consent/request/hip/notify`:
  +0 s   the notice arrives, it sleeps 5 s
  +5 s   it saves the consent id on its rows, acknowledges to ABDM (through the gateway), sleeps 10 s
  +16 s  it builds the records (ConvertRecordToFHIR) with the transaction id and keys it holds *at that moment*
and on `hip/health-information/request` it finds its rows by consent id, saves the transaction id, acknowledges
and fetches the sender keys.

ABDM sends the HIP that request only after the HIP acknowledged the consent notice, and about 14 s after it (seen at
every round on the sandbox: ack 09:26:43 / request handled 09:26:57; 10:02:54 / 10:03:08; 10:50:09 / 10:50:23). So the
request is handled about 4 s AFTER i-medicus built the records: they go out with the transaction of the previous round
(ABDM answers the status notification with "Invalid Consent artefact id") or without keys (`fhir/encrypt` 400).
Waiting for the request before delivering the notice cannot work: the request only comes after the notice's ack.

What works (the gateway sees both sides):
  1. the answer to i-medicus' consent acknowledgement is held until the request of that consent has been acknowledged and
     its keys fetched (`wait_for_request`, called by hip_service.consent_on_notify AFTER the acknowledgement went to
     ABDM), so i-medicus' 10 s sleep starts when everything it needs is in place;
  2. the worker is busy with the notice for that whole time (it waits for i-medicus' answer), so the request is delivered
     at once by a thread of its own (`enqueue_forward`) instead of waiting behind it.
"""
import logging
import threading
import time
from datetime import timedelta

from flask import current_app

from src.app.extensions import db
from src.app.models import CallbackEvent, HipConsent, HipTransfer, OutboxJob
from src.app.models import outbox_job as oj
from src.app.models.base import utcnow
from src.app.outbox import engine

logger = logging.getLogger(__name__)

NOTICE = "consent/request/hip/notify"
REQUEST = "hip/health-information/request"
GRANTED = "GRANTED"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _notice_consent(body):
    """The consent id of a GRANTED notice; a body of any other shape (ABDM or a test sending rubbish) is not ours to judge."""
    notification = _dict(_dict(body).get("notification"))
    if str(notification.get("status") or "").upper() != GRANTED:
        return None
    return str(notification["consentId"]) if notification.get("consentId") else None


def _request_consent(body):
    consent = _dict(_dict(_dict(body).get("hiRequest")).get("consent"))
    return str(consent["id"]) if consent.get("id") else None


def _notice_in_flight(consent_id):
    """True while the worker is delivering the consent notice of `consent_id` to the client app."""
    rows = (db.session.query(OutboxJob, CallbackEvent).join(CallbackEvent, OutboxJob.callback_event_id == CallbackEvent.id)
            .filter(OutboxJob.kind == oj.KIND_FORWARD_CALLBACK, OutboxJob.status == oj.PROCESSING,
                    OutboxJob.hold_consent_id == consent_id).all())
    return any(event.path.endswith(NOTICE) for _job, event in rows)


def _deliver(app, job_id):
    """Thread body: claim the job (the worker may have taken it meanwhile) and deliver it."""
    with app.app_context():
        if engine._claim(job_id, utcnow() + timedelta(seconds=1)):
            job = db.session.get(OutboxJob, job_id)
            if job is not None:
                engine.run_job(job)


def _spawn(delay, fn, *args):
    timer = threading.Timer(delay, fn, args=args)
    timer.daemon = True
    timer.start()


def enqueue_forward(event, body, path, api_client_id):
    """Queue the delivery of a callback to the client app. The job of a GRANTED consent notice is tagged with its consent; a
    health-information request whose notice is being delivered right now is not left behind the worker that is busy with
    it: a thread delivers it (the job stays in the queue, so a failure is retried by the worker like any other)."""
    job = engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=api_client_id)
    if not isinstance(body, dict):
        return job
    if path.endswith(NOTICE):
        consent_id = _notice_consent(body)
        if consent_id:
            job.hold_consent_id = consent_id
    elif path.endswith(REQUEST):
        consent_id = _request_consent(body)
        if consent_id:
            job.hold_consent_id = consent_id
            if _notice_in_flight(consent_id):
                db.session.flush()
                # after the caller's commit (the receiver commits right after this returns)
                _spawn(1.0, _deliver, current_app._get_current_object(), job.id)
    return job


def wait_for_request(consent_id):
    """Hold the answer to i-medicus' consent acknowledgement until the request of this consent has been handled.
    Returns True when it was (and its keys had time to be fetched), False when the wait ran out or does not apply."""
    config = current_app.config
    wait = config["HIP_CONSENT_ACK_WAIT_SECONDS"]
    if wait <= 0 or not consent_id:
        return False
    consent = HipConsent.query.filter_by(consent_id=str(consent_id)).first()
    if consent is None or consent.status != GRANTED:
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        db.session.expire_all()
        if HipTransfer.query.filter_by(consent_id=str(consent_id)).first() is not None:
            # i-medicus acknowledges the request and then fetches the sender keys: give it a moment
            time.sleep(config["HIP_KEYS_SETTLE_SECONDS"])
            return True
        time.sleep(0.5)
    logger.warning("consent notice: no health-information request arrived in time", extra={"consent_id": consent_id})
    return False
