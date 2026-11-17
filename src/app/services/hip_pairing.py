"""
Make i-medicus build the records with the transaction and keys of the health-information request (M2, HIP), however many
consents and record types arrive and however slowly the HIU sends its request.

What i-medicus does (WebhookTrait, Nov 2026), on `consent/request/hip/notify` (the notice):
  +0 s   the notice arrives, it sleeps 5 s
  +5 s   it saves the consent id on its rows, acknowledges to ABDM (through the gateway), sleeps 10 s
  +16 s  it builds the records (ConvertRecordToFHIR) with the transaction id and keys it holds *at that moment*
and on `hip/health-information/request` (the request) it finds its rows by consent id, saves the transaction id,
acknowledges and fetches the sender keys. So the request must be handled after +5 s and before +16 s.

ABDM sends the request only after the HIP acknowledged the notice, ~14 s after it, and the HIU may send it much later
(a consent for eight record types makes eight artefacts, which i-medicus as HIU handles one by one). An acknowledgement that
waits for the request cannot work either (25 s ran out, eleven times in one test).

What works, and needs nothing from i-medicus:
  1. THE GATEWAY ACKNOWLEDGES A GRANTED NOTICE ITSELF, at once (`ack_notice_now`): it holds the notice durably, which is all
     ABDM asks of a HIP. The request then arrives whenever the HIU sends it, independently of i-medicus. i-medicus' own
     acknowledgement later finds it already sent (idempotent) and returns at once.
  2. THE NOTICE WAITS IN THE DATABASE (not in a thread) until its request has arrived (`enqueue_forward` holds it for
     HIP_NOTICE_HOLD_SECONDS at most); the request releases it.
  3. The request is delivered to i-medicus only once its notice has been running for HIP_PAIR_DELAY_SECONDS (the worker checks
     it: `worker.request_ready`): after i-medicus saved the consent id (+5 s), before it builds the records (+16 s).
Notices of one patient still go one at a time (the worker's lanes), because they share i-medicus' rows.
"""
import logging
import threading
import time

from flask import current_app

from src.app.extensions import db
from src.app.models import ApiClient, CallbackEvent, HipConsent, HipTransfer, OutboxJob
from src.app.models import outbox_job as oj
from src.app.models.base import utcnow
from src.app.outbox import engine

logger = logging.getLogger(__name__)

NOTICE = "consent/request/hip/notify"
REQUEST = "hip/health-information/request"
GRANTED = "GRANTED"

_slots = {}


def _wait_slots(limit):
    """One counter per process: how many acknowledgements may be waiting at once."""
    if limit not in _slots:
        _slots[limit] = threading.BoundedSemaphore(limit)
    return _slots[limit]


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


def acks_at_once():
    return bool(current_app.config.get("HIP_ACK_NOTICE_AT_ONCE"))


def _held_notices(consent_id):
    """Pending forward jobs of the GRANTED notice of `consent_id`."""
    rows = (db.session.query(OutboxJob, CallbackEvent).join(CallbackEvent, OutboxJob.callback_event_id == CallbackEvent.id)
            .filter(OutboxJob.kind == oj.KIND_FORWARD_CALLBACK, OutboxJob.status == oj.PENDING,
                    OutboxJob.hold_consent_id == consent_id).all())
    return [job for job, event in rows if event.path.endswith(NOTICE)]


def enqueue_forward(event, body, path, api_client_id):
    """Queue the delivery of a callback to the client app.
    A GRANTED notice is tagged with its consent and, when the gateway acknowledges notices itself, waits for its request
    (at most HIP_NOTICE_HOLD_SECONDS); the request, tagged the same way, releases it."""
    hold = current_app.config.get("HIP_NOTICE_HOLD_SECONDS", 0) if acks_at_once() else 0
    notice_consent = _notice_consent(body) if path.endswith(NOTICE) else None
    request_consent = _request_consent(body) if path.endswith(REQUEST) else None
    delay = hold if notice_consent else 0
    job = engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=api_client_id, delay=delay)
    consent_id = notice_consent or request_consent
    if consent_id:
        job.hold_consent_id = consent_id
    if request_consent:
        for notice in _held_notices(request_consent):
            notice.next_attempt_at = utcnow()  # its request is here: deliver the notice now
    return job


def ack_notice_now(event, body, api_client_id):
    """Acknowledge a GRANTED consent notice to ABDM ourselves, right after it was stored. Never raises: the callback must be
    accepted, and the acknowledgement is idempotent (HipReply), so i-medicus' later one is "already_sent"."""
    if not acks_at_once() or not _notice_consent(body):
        return None
    try:
        from src.app.abdm import hip as abdm  # late: services import each other
        from src.app.services import hip_service
        consent_id = _notice_consent(body)
        client = db.session.get(ApiClient, api_client_id)
        if client is None or not event.hip_id:
            return None
        ack = {"acknowledgement": {"status": "OK", "consentId": consent_id}, "response": {"requestId": event.request_id}}
        return hip_service._answer(client, event, "consent_on_notify", abdm.CONSENT_ON_NOTIFY, ack, "hip/consent/on-notify")
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("Could not acknowledge the consent notice at once")
        return None


def ack_request_now(event, body, api_client_id):
    """A health-information request arrived: remember the transfer (transaction, HIU key, push URL) and acknowledge it to ABDM,
    right away, as a HIP should. i-medicus' own acknowledgement later is "already_sent". The gateway then holds the HIU's key
    before i-medicus has even seen the request, which is what lets encrypt / push / notify not depend on i-medicus' rows. Never
    raises: a consent that is not usable is simply not acknowledged here (i-medicus' own call is then refused as before)."""
    if not acks_at_once() or not _request_consent(body) or not _dict(body).get("transactionId"):
        return None
    try:
        from src.app.services import hip_service  # late: services import each other
        client = db.session.get(ApiClient, api_client_id)
        if client is None or not event.hip_id:
            return None
        return hip_service.hi_on_request(client, {"transactionId": body["transactionId"], "requestId": event.request_id})
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.info("Health-information request not acknowledged at once: %s", exc)
        return None


def wait_for_request(consent_id):
    """Fallback when the gateway does not acknowledge notices itself: hold the answer to i-medicus' acknowledgement until the
    request of this consent has been handled. Returns True when it was (and its keys had time to be fetched), False when the
    wait ran out or does not apply."""
    config = current_app.config
    wait = config["HIP_CONSENT_ACK_WAIT_SECONDS"]
    if wait <= 0 or not consent_id:
        return False
    consent = HipConsent.query.filter_by(consent_id=str(consent_id)).first()
    if consent is None or consent.status != GRANTED:
        return False
    slots = _wait_slots(config["HIP_MAX_ACK_WAITS"])
    if not slots.acquire(blocking=False):  # a flood of notices: do not tie up every web thread, answer at once
        logger.warning("consent notice: too many acknowledgements waiting, answering at once",
                       extra={"consent_id": consent_id})
        return False
    try:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            db.session.expire_all()
            if HipTransfer.query.filter_by(consent_id=str(consent_id)).first() is not None:
                # i-medicus acknowledges the request and then fetches the sender keys: give it a moment
                time.sleep(config["HIP_KEYS_SETTLE_SECONDS"])
                return True
            time.sleep(0.5)
        logger.warning("consent notice: no health-information request arrived in time",
                       extra={"consent_id": consent_id})
        return False
    finally:
        slots.release()
