"""
The last leg of M2 for a HIP (WBS Day 14): push the encrypted records to the HIU and tell ABDM how the transfer went.

    health-information request acknowledged  ->  HipTransfer (the push URL and the consent are ABDM's, kept here)
    i-medicus: push  ->  the gateway checks consent + care contexts, POSTs to the HIU's push URL (retried by the
                         worker when the HIU is down)
    i-medicus: notify -> ABDM `health-information/notify` (TRANSFERRED / FAILED), for every care context pushed

i-medicus keeps sending the bodies it always sent (`calling-data-push-url`, `data-flow-notification-request`); the
gateway does not trust them where it already knows better: it pushes only to the URL ABDM gave for the transaction,
only care contexts the consent covers, and tells ABDM FAILED itself when the push cannot be delivered.
"""
import hashlib
import ipaddress
import json
import logging
import re
from urllib.parse import urlsplit

import requests
from cryptography.exceptions import InvalidTag
from flask import current_app

from src.app.abdm import fidelius
from src.app.abdm import hip as abdm
from src.app.callbacks import registry, vault
from src.app.common import validators as v
from src.app.errors import AppError, ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.app.extensions import db
from src.app.models import HipTransfer
from src.app.models import hip_state as hs
from src.app.models import outbox_job as oj
from src.app.models.base import as_utc, utcnow
from src.app.outbox import engine
from src.app.outbox.engine import Fail, Ok, Retry
from src.app.services import hip_consent_service

logger = logging.getLogger(__name__)

SESSION_TRANSFERRED = "TRANSFERRED"
SESSION_FAILED = "FAILED"
HI_DELIVERED = "DELIVERED"
HI_ERRORED = "ERRORED"
_RETRYABLE_HTTP = (408, 425, 429)

_sender = requests.Session()


class PushRejected(AppError):
    """The HIU answered the push with an error that will not go away if it is repeated."""
    status = 502
    code = "HIU_REJECTED_PUSH"
    default_message = "The HIU rejected the data push"


# ---- the transfer record -----------------------------------------------------------------------------------------

def _iso(moment=None):
    return (moment or utcnow()).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def register_request(event, original, hi_request, transaction_id):
    """Remember a health-information request when it is acknowledged: which consent, where the HIU wants the data and
    until when its key is valid. A repeated request (ABDM retries) updates the row; a transfer already under way is
    not reset."""
    key = (hi_request.get("keyMaterial") or {}).get("dhPublicKey") or {}
    consent_id = (hi_request.get("consent") or {}).get("id")
    row = HipTransfer.query.filter_by(api_client_id=event.api_client_id, transaction_id=transaction_id).first()
    if row is None:
        row = HipTransfer(api_client_id=event.api_client_id, hip_id=event.hip_id, transaction_id=transaction_id,
                          status=hs.TRANSFER_ACKNOWLEDGED, attempts=0)
        db.session.add(row)
    row.consent_id = str(consent_id) if consent_id else row.consent_id
    row.request_id = event.request_id
    row.data_push_url = hi_request.get("dataPushUrl") or row.data_push_url
    row.key_expiry = hip_consent_service.parse_time(key.get("expiry")) or row.key_expiry
    row.hiu_public_key = _canonical_key(key.get("keyValue")) or row.hiu_public_key
    nonce = (hi_request.get("keyMaterial") or {}).get("nonce")
    row.hiu_nonce = str(nonce)[:64] if nonce else row.hiu_nonce
    db.session.commit()
    return row


def _canonical_key(value):
    """One spelling for a public key (raw point or X.509 in, X.509 out) so two spellings of a key compare equal."""
    try:
        return fidelius.encode_x509_b64(fidelius.parse_public_key(str(value)))
    except fidelius.FideliusError:
        return None


def remember_sender_key(client, data, encrypted):
    """An Encrypt call for a HIU key of one of this client's open transfers: remember the sender's key and nonce used.
    The video-call flow of i-medicus sends its push with the *receiver's* nonce / key in `keyMaterial` (the HIU cannot
    decrypt with those); the push uses what was remembered here instead, so the HIU can read the record."""
    receiver = _canonical_key(v.pick(data, "receiverPublicKey"))
    nonce = v.pick(data, "receiverNonce")
    if not receiver or not nonce:
        return
    rows = HipTransfer.query.filter(HipTransfer.api_client_id == client.id, HipTransfer.hiu_public_key == receiver,
                                    HipTransfer.hiu_nonce == str(nonce)[:64],
                                    HipTransfer.status.in_((hs.TRANSFER_ACKNOWLEDGED, hs.TRANSFER_PUSH_QUEUED))).all()
    for row in rows:
        row.sender_public_key = encrypted["keyToShare"]
        row.sender_nonce = str(v.pick(data, "senderNonce"))[:64]
    if rows:
        db.session.commit()


def _use_sender_key(transfer, body):
    """Put the key and nonce the record was encrypted with into the push body when the client sent others."""
    if not transfer.sender_public_key or not transfer.sender_nonce:
        return
    material = body["keyMaterial"]
    given_key = material["dhPublicKey"]["keyValue"]
    if material["nonce"] == transfer.sender_nonce and _canonical_key(given_key) == _canonical_key(
            transfer.sender_public_key):
        return
    logger.warning("push: keyMaterial replaced by the sender key and nonce used to encrypt", extra={
        "transaction_id": transfer.transaction_id})
    material["nonce"] = transfer.sender_nonce
    material["dhPublicKey"]["keyValue"] = transfer.sender_public_key


def _get(client, transaction_id):
    transaction_id = str(v.require(transaction_id, "transactionId")).strip()[:64]
    row = HipTransfer.query.filter_by(api_client_id=client.id, transaction_id=transaction_id).first()
    if row is None:
        raise NotFoundError("No health-information request with this transactionId was acknowledged for your "
                            "facilities", code="TRANSFER_NOT_FOUND", details={"transactionId": transaction_id})
    return row


def describe(row):
    return {"transactionId": row.transaction_id, "hipId": row.hip_id, "consentId": row.consent_id,
            "status": row.status, "careContexts": row.care_contexts or [], "attempts": row.attempts,
            "lastError": row.last_error, "pushedAt": _stamp(row.pushed_at), "notifiedAt": _stamp(row.notified_at),
            "createdAt": _stamp(row.created_at)}


def _result(transfer, outcome, **extra):
    """What push / notify answer: `status` is the outcome of this call (sent, queued, held, already_sent) and
    `transferStatus` where the transfer stands."""
    return {**describe(transfer), "status": outcome, "transferStatus": transfer.status, **extra}


def _stamp(value):
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def status_of(client, transaction_id):
    return describe(_get(client, transaction_id))


# ---- the push URL -------------------------------------------------------------------------------------------------

def validate_push_url(url):
    """The HIU's push URL comes from ABDM's request, but it is still a URL we are about to call from inside our network:
    https (http only when PUSH_ALLOW_HTTP), no credentials, and nothing private or internal (SSRF) unless
    PUSH_ALLOW_PRIVATE_URLS (local development)."""
    config = current_app.config
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        raise ValidationError("The HIU's push URL is not a valid URL", code="UNSAFE_PUSH_URL")
    schemes = ("https", "http") if config["PUSH_ALLOW_HTTP"] else ("https",)
    if parts.scheme not in schemes:
        raise ValidationError("The HIU's push URL must use https", code="UNSAFE_PUSH_URL")
    if not host:
        raise ValidationError("The HIU's push URL has no host", code="UNSAFE_PUSH_URL")
    if parts.username or parts.password:
        raise ValidationError("The HIU's push URL must not contain credentials", code="UNSAFE_PUSH_URL")
    if not config["PUSH_ALLOW_PRIVATE_URLS"]:
        try:
            addresses = registry._addresses(host)
        except registry.UnresolvableHostError:
            raise ValidationError("The HIU's push host does not resolve", code="UNSAFE_PUSH_URL")
        if any(registry._is_forbidden_ip(ip) for ip in addresses):
            raise ValidationError("The HIU's push URL must not point to a private or internal address",
                                  code="UNSAFE_PUSH_URL")
    return parts.geturl()


# ---- push ---------------------------------------------------------------------------------------------------------

def _checksum(content):
    return hashlib.md5(content.encode("utf-8")).hexdigest()  # noqa: S324  (an integrity tag ABDM asks for, not security)


def _push_body(data, consent):
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValidationError("entries must be a non-empty list", details={"field": "entries"})
    allowed = {c.get("careContextReference") for c in (consent.care_contexts or []) if isinstance(c, dict)}
    out = []
    for entry in entries:
        content = entry.get("content") if isinstance(entry, dict) else None
        reference = str(entry.get("careContextReference") or "") if isinstance(entry, dict) else ""
        if not isinstance(content, str) or not content or not reference:
            raise ValidationError("every entry needs `content` (the encrypted record) and `careContextReference`",
                                  details={"field": "entries"})
        if allowed and reference not in allowed:
            raise ForbiddenError("This care context is not covered by the consent", code="CARE_CONTEXT_NOT_CONSENTED",
                                 details={"careContextReference": reference})
        out.append({"content": content, "media": entry.get("media") or "application/fhir+json",
                    "checksum": _checksum(content), "careContextReference": reference})
    block = data.get("keyMaterial")
    try:
        key_value, nonce = fidelius.read_key_material_block(block)
    except fidelius.FideliusError as exc:
        raise ValidationError(f"Invalid keyMaterial: {exc}", code="INVALID_KEY_MATERIAL", details={"field": "keyMaterial"})
    key = block["dhPublicKey"]
    return {"pageNumber": int(data.get("pageNumber") or 0), "pageCount": int(data.get("pageCount") or 1),
            "transactionId": str(data.get("transactionId")), "entries": out,
            "keyMaterial": {"cryptoAlg": block.get("cryptoAlg") or "ECDH", "curve": block.get("curve") or "Curve25519",
                            "dhPublicKey": {"expiry": key["expiry"], "parameters": key.get("parameters")
                                            or "Curve25519/32byte random key", "keyValue": key_value},
                            "nonce": nonce}}


def _post(url, body):
    """POST to the HIU. Returns ("ok" | "retry" | "fail", http status or None, message)."""
    config = current_app.config
    try:
        with _sender.post(url, json=body, timeout=(config["PUSH_TIMEOUT_CONNECT"], config["PUSH_TIMEOUT_READ"]),
                          allow_redirects=False, stream=True) as response:
            status = response.status_code
            excerpt = ""
            if not 200 <= status < 300:  # what the HIU said, a few characters of it: the reason is in there
                excerpt = response.raw.read(300, decode_content=True).decode("utf-8", "replace")
                excerpt = " ".join(excerpt.split())
    except (requests.Timeout, requests.ConnectionError) as exc:
        return "retry", None, f"HIU not reachable: {type(exc).__name__}"
    except requests.RequestException as exc:
        return "fail", None, f"push failed: {type(exc).__name__}"
    if 200 <= status < 300:
        return "ok", status, None
    message = f"HIU answered HTTP {status}" + (f": {excerpt}" if excerpt else "")
    if status >= 500 or status in _RETRYABLE_HTTP:
        return "retry", status, message
    return "fail", status, message


def _consent_ok(transfer, client_id):
    return hip_consent_service.assert_usable(transfer.consent_id, {}, client_id)


def _mark_pushed(transfer, references):
    transfer.status = hs.TRANSFER_PUSHED
    transfer.care_contexts = references
    transfer.pushed_at = utcnow()
    transfer.last_error = None
    db.session.commit()


def push(client, data):
    """Push the encrypted records to the HIU. Returns {"status": "sent" | "queued" | "already_sent", ...}."""
    transfer = _get(client, data.get("transactionId"))
    if transfer.status in (hs.TRANSFER_PUSHED, hs.TRANSFER_NOTIFIED):
        return _result(transfer, "already_sent")
    if transfer.status == hs.TRANSFER_PUSH_QUEUED:
        return _result(transfer, "queued")
    if transfer.status == hs.TRANSFER_FAILED:
        raise ConflictError("This transfer failed and ABDM was told so: ask the patient's HIU for a new request",
                            code="TRANSFER_FAILED", details={"lastError": transfer.last_error})

    given = v.pick(data, "pushUrl", "dataPushUrl")
    if given and transfer.data_push_url and given.rstrip("/") != transfer.data_push_url.rstrip("/"):
        raise ValidationError("pushUrl is not the one ABDM gave for this transaction", code="PUSH_URL_MISMATCH")
    url = validate_push_url(transfer.data_push_url or given)
    try:
        consent = _consent_ok(transfer, client.id)
    except ForbiddenError as exc:
        _fail(transfer, exc.message)
        raise
    body = _push_body(data, consent)
    _use_sender_key(transfer, body)
    references = [e["careContextReference"] for e in body["entries"]]

    transfer.attempts += 1
    outcome, http_status, message = _post(url, body)
    if outcome == "ok":
        _mark_pushed(transfer, references)
        return _result(transfer, "sent")
    if outcome == "fail":
        _fail(transfer, message)
        raise PushRejected(message, details={"httpStatus": http_status})

    transfer.status = hs.TRANSFER_PUSH_QUEUED
    transfer.care_contexts = references
    transfer.last_error = message
    job = engine.enqueue(oj.KIND_HIU_PUSH, api_client_id=client.id,
                         payload_enc=vault.seal_text(json.dumps({"transferId": transfer.id, "url": url, "body": body})))
    db.session.commit()
    logger.warning("HIU push queued for retry", extra={"job_id": job.id, "transaction_id": transfer.transaction_id,
                                                       "error": message})
    return _result(transfer, "queued", jobId=job.id)


def run_push_job(job):
    """The worker's retry of a push (job kind hiu_push)."""
    try:
        payload = json.loads(vault.open_text(job.payload_enc))
        transfer = db.session.get(HipTransfer, payload["transferId"])
        url, body = payload["url"], payload["body"]
    except (TypeError, ValueError, KeyError, InvalidTag):
        return Fail("job has no readable payload")
    if transfer is None:
        return Fail("the transfer no longer exists")
    if transfer.status != hs.TRANSFER_PUSH_QUEUED:
        return Ok()  # pushed or failed in the meantime
    try:
        _consent_ok(transfer, transfer.api_client_id)
    except ForbiddenError as exc:  # revoked / expired while the push waited
        _fail(transfer, exc.message)
        return Fail(exc.message)
    try:
        url = validate_push_url(url)
    except ValidationError as exc:
        _fail(transfer, exc.message)
        return Fail(exc.message)

    transfer.attempts += 1
    outcome, http_status, message = _post(url, body)
    if outcome == "ok":
        _mark_pushed(transfer, transfer.care_contexts or [e["careContextReference"] for e in body["entries"]])
        _flush_pending(transfer)
        return Ok()
    transfer.last_error = message
    if outcome == "retry" and job.attempts < job.max_attempts:
        db.session.commit()
        return Retry(message, http_status)
    reason = message if outcome == "fail" else f"{message}; gave up after {job.attempts} tries"
    _fail(transfer, reason)
    return Fail(reason, http_status)


# ---- the status notification --------------------------------------------------------------------------------------

def _description(text, default):
    """ABDM refuses a statusResponses description with braces, quotes or other symbols ("Invalid description", seen on the
    sandbox when the HIU's JSON error was copied in): keep letters, digits, spaces and . _ - only."""
    clean = " ".join(re.sub(r"[^A-Za-z0-9 ._-]", " ", str(text or "")).split())[:100]
    return clean or default


def _responses(transfer, sent, session_status, reason=None):
    """statusResponses for every care context of the transfer: what the client sent, completed with the ones it left
    out (i-medicus reports only the first), and ERRORED for all of them when the transfer failed."""
    by_ref = {}
    for item in sent or []:
        if isinstance(item, dict) and item.get("careContextReference"):
            by_ref[str(item["careContextReference"])] = item
    references = list(transfer.care_contexts or [])
    if not references:
        consent = hs.HipConsent.query.filter_by(consent_id=transfer.consent_id).first() if transfer.consent_id else None
        references = [c.get("careContextReference") for c in (consent.care_contexts if consent else None) or []
                      if isinstance(c, dict) and c.get("careContextReference")]
    references += [r for r in by_ref if r not in references]
    out = []
    for reference in references:
        item = by_ref.get(reference, {})
        if session_status == SESSION_FAILED:
            status = HI_ERRORED
            text = _description(reason or item.get("description"), "Data could not be pushed")
        else:
            status = str(item.get("hiStatus") or HI_DELIVERED).upper()
            text = _description(item.get("description"), "Data Pushed")
        out.append({"careContextReference": reference, "hiStatus": status, "description": text})
    return out


def _notification(transfer, session_status, responses):
    return {"notification": {
        "consentId": transfer.consent_id, "transactionId": transfer.transaction_id, "doneAt": _iso(),
        "notifier": {"type": "HIP", "id": transfer.hip_id},
        "statusNotification": {"sessionStatus": session_status, "hipId": transfer.hip_id, "statusResponses": responses}}}


def _send_notification(transfer, session_status, responses):
    result = abdm.reply(abdm.HI_NOTIFY, _notification(transfer, session_status, responses), transfer.hip_id,
                        transfer.api_client_id, "hip/health-information/notify")
    transfer.status = hs.TRANSFER_NOTIFIED if session_status == SESSION_TRANSFERRED else hs.TRANSFER_FAILED
    transfer.notified_at = utcnow()
    transfer.pending_notify = None
    db.session.commit()
    return result


def _fail(transfer, reason):
    """Give up on this transfer and tell ABDM (once)."""
    transfer.status = hs.TRANSFER_FAILED
    transfer.last_error = (reason or "")[:500]
    db.session.commit()
    if transfer.notified_at is not None:
        return
    try:
        _send_notification(transfer, SESSION_FAILED, _responses(transfer, None, SESSION_FAILED, reason))
    except AppError as exc:  # ABDM refused it; the transfer stays FAILED and the error is on the row
        db.session.rollback()
        transfer = db.session.get(HipTransfer, transfer.id)
        transfer.status = hs.TRANSFER_FAILED
        transfer.last_error = f"{reason}; ABDM refused the failure notification: {exc.message}"[:500]
        db.session.commit()
        logger.error("Could not tell ABDM a transfer failed", extra={"transaction_id": transfer.transaction_id})


def _flush_pending(transfer):
    """A notification i-medicus sent while the push was still queued goes out now that the push landed."""
    pending = transfer.pending_notify
    if not pending or transfer.notified_at is not None:
        return
    _send_notification(transfer, SESSION_TRANSFERRED, _responses(transfer, pending.get("statusResponses"),
                                                                   SESSION_TRANSFERRED))


def notify(client, data):
    """Tell ABDM how the transfer went. Accepts i-medicus' `{notification: {...}}`."""
    n = data.get("notification") if isinstance(data.get("notification"), dict) else {}
    transfer = _get(client, n.get("transactionId") or v.pick(data, "transactionId"))
    consent_id = n.get("consentId") or v.pick(data, "consentId")
    if consent_id and transfer.consent_id and str(consent_id) != transfer.consent_id:
        raise ValidationError("consentId is not the consent of this transaction", code="CONSENT_MISMATCH",
                              details={"field": "notification.consentId"})
    status_block = n.get("statusNotification") if isinstance(n.get("statusNotification"), dict) else {}
    session = str(v.require(status_block.get("sessionStatus"), "notification.statusNotification.sessionStatus")).upper()
    if session not in (SESSION_TRANSFERRED, SESSION_FAILED):
        raise ValidationError("sessionStatus must be TRANSFERRED or FAILED", details={"field": "sessionStatus"})
    sent = status_block.get("statusResponses")

    if transfer.notified_at is not None:
        return _result(transfer, "already_sent")
    if session == SESSION_FAILED:
        reason = next((r.get("description") for r in sent or [] if isinstance(r, dict) and r.get("description")), None)
        result = _send_notification(transfer, SESSION_FAILED, _responses(transfer, sent, SESSION_FAILED, reason))
        return _result(transfer, result["status"])

    if transfer.status == hs.TRANSFER_PUSH_QUEUED:  # the records are not at the HIU yet: say so when they are
        transfer.pending_notify = {"statusResponses": sent}
        db.session.commit()
        return _result(transfer, "held")
    if transfer.status == hs.TRANSFER_FAILED:
        raise ConflictError("This transfer failed and ABDM was told so", code="TRANSFER_FAILED",
                            details={"lastError": transfer.last_error})
    if transfer.status != hs.TRANSFER_PUSHED:
        raise ValidationError("The records were not pushed to the HIU: push them before reporting TRANSFERRED",
                              code="NOT_PUSHED")
    result = _send_notification(transfer, SESSION_TRANSFERRED, _responses(transfer, sent, SESSION_TRANSFERRED))
    return _result(transfer, result["status"])
