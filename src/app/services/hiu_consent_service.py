"""
M3 (HIU) consent and transfer state, kept from ABDM's callbacks 

Recorded when the callback arrives (the callback receiver calls the `record_*` functions; they never raise, so a
bookkeeping problem cannot make ABDM retry the callback):
    hiu/consent/request/notify        status of a consent request + the artefact ids   -> HiuConsent
    hiu/consent/on-fetch              the artefact: granted range, hi types, erase date -> HiuConsent
    hiu/health-information/on-request the transactionId of our request                -> HiuTransfer

Used when the client app asks for records: `assert_usable` refuses a consent that is denied, revoked, expired or
does not cover the date range (HTTP 403 `CONSENT_INVALID`), before anything is sent to ABDM. A consent the gateway
never heard of is let through unless HIU_REQUIRE_KNOWN_CONSENT is set (consents made before this was deployed).
A client sees only its own consents.
"""
import logging

from flask import current_app

from src.app.errors import ForbiddenError, NotFoundError
from src.app.extensions import db
from src.app.models import HiuConsent, HiuTransfer
from src.app.models import hip_state as st
from src.app.models.base import as_utc, utcnow
from src.app.services.hip_consent_service import GRANTED, parse_time

logger = logging.getLogger(__name__)


def _status(value):
    return str(value or "").strip().upper()


def _apply_detail(row, detail):
    permission = detail.get("permission") or {}
    date_range = permission.get("dateRange") or {}
    row.hip_id = (detail.get("hip") or {}).get("id") or row.hip_id
    row.hi_types = detail.get("hiTypes") or row.hi_types
    row.date_from = parse_time(date_range.get("from")) or row.date_from
    row.date_to = parse_time(date_range.get("to")) or row.date_to
    row.erase_at = parse_time(permission.get("dataEraseAt")) or row.erase_at


def record_notification(body, hiu_id, api_client_id):
    """`hiu/consent/request/notify`: {notification: {consentRequestId, status, consentArtefacts: [{id}]}}."""
    try:
        notification = (body or {}).get("notification") or {}
        status = _status(notification.get("status"))
        request_id = notification.get("consentRequestId")
        if not status:
            return None
        artefacts = [a.get("id") for a in notification.get("consentArtefacts") or [] if isinstance(a, dict) and a.get("id")]
        ids = [str(a) for a in artefacts] or [None]
        for consent_id in ids:
            if consent_id:
                row = HiuConsent.query.filter_by(consent_id=consent_id).first()
            else:
                row = HiuConsent.query.filter_by(consent_request_id=str(request_id or ""), consent_id=None).first() \
                    if request_id else None
            if row is None:
                row = HiuConsent(api_client_id=api_client_id, hiu_id=hiu_id, status=status)
                db.session.add(row)
            row.status = status
            row.consent_id = consent_id or row.consent_id
            row.consent_request_id = str(request_id) if request_id else row.consent_request_id
        db.session.commit()
        return True
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("Could not store HIU consent notification")
        return None


def record_fetch(body, hiu_id, api_client_id):
    """`hiu/consent/on-fetch`: {consent: {status, consentDetail: {consentId, permission, hiTypes, hip, ...}}}."""
    try:
        consent = (body or {}).get("consent") or {}
        detail = consent.get("consentDetail") or {}
        consent_id = detail.get("consentId")
        if not consent_id:
            return None
        row = HiuConsent.query.filter_by(consent_id=str(consent_id)).first()
        if row is None:
            row = HiuConsent(api_client_id=api_client_id, hiu_id=hiu_id, consent_id=str(consent_id), status=GRANTED)
            db.session.add(row)
        row.status = _status(consent.get("status")) or row.status
        _apply_detail(row, detail)
        db.session.commit()
        return row
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("Could not store HIU consent artefact")
        return None


def record_on_request(body, api_client_id):
    """`hiu/health-information/on-request`: {hiRequest: {transactionId, sessionStatus}, response: {requestId}}."""
    try:
        body = body or {}
        request_id = (body.get("response") or {}).get("requestId")
        hi_request = body.get("hiRequest") or {}
        row = HiuTransfer.query.filter_by(request_id=str(request_id or ""), api_client_id=api_client_id).first()
        if row is None or not hi_request.get("transactionId"):
            return None
        row.transaction_id = str(hi_request["transactionId"])
        row.session_status = _status(hi_request.get("sessionStatus")) or None
        row.status = st.HIU_TRANSFER_ACKNOWLEDGED
        db.session.commit()
        return row
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("Could not store HIU health-information answer")
        return None


def _deny(reason, message, consent_id):
    raise ForbiddenError(message, code="CONSENT_INVALID", details={"reason": reason, "consentId": consent_id})


def _own(consent_id, client_id):
    row = HiuConsent.query.filter_by(consent_id=str(consent_id)).first()
    return row if row is not None and row.api_client_id == client_id else None


def reason_unusable(row, asked_from=None, asked_to=None):
    """None when the consent can be used, else why not (DENIED, REVOKED, EXPIRED, OUT_OF_RANGE, ...)."""
    if row.status != GRANTED:
        return row.status
    if row.erase_at and as_utc(row.erase_at) <= utcnow():
        return "EXPIRED"
    if (asked_from and row.date_from and asked_from < as_utc(row.date_from)) or \
            (asked_to and row.date_to and asked_to > as_utc(row.date_to)):
        return "OUT_OF_RANGE"
    return None


def assert_usable(client_id, consent_id, date_from=None, date_to=None):
    """Raise CONSENT_INVALID (403) when the consent is known and cannot be used for this request."""
    row = _own(consent_id, client_id)
    if row is None:
        if current_app.config.get("HIU_REQUIRE_KNOWN_CONSENT"):
            _deny("NOT_FOUND", "No consent with this id was received", consent_id)
        return None
    reason = reason_unusable(row, parse_time(date_from), parse_time(date_to))
    if reason:
        _deny(reason, f"Consent is {reason}" if reason != "OUT_OF_RANGE" else "Requested date range is outside the consent",
              consent_id)
    return row


def note_request(client_id, hiu_id, request_id, consent_id):
    db.session.add(HiuTransfer(api_client_id=client_id, hiu_id=hiu_id, request_id=request_id, consent_id=str(consent_id)))
    db.session.commit()


def _iso(value):
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.000Z") if value else None


def describe(client_id, consent_id):
    """The consent as the gateway knows it, for the client's own support screens."""
    row = _own(consent_id, client_id)
    if row is None:
        raise NotFoundError("No consent with this id for your facilities", code="CONSENT_NOT_FOUND")
    transfers = HiuTransfer.query.filter_by(api_client_id=client_id, consent_id=row.consent_id) \
        .order_by(HiuTransfer.id).all()
    reason = reason_unusable(row)
    return {"consentId": row.consent_id, "consentRequestId": row.consent_request_id, "hipId": row.hiu_id,
            "status": row.status, "usable": reason is None, "reason": reason, "hiTypes": row.hi_types,
            "dateRange": {"from": _iso(row.date_from), "to": _iso(row.date_to)}, "dataEraseAt": _iso(row.erase_at),
            "transactions": [{"requestId": t.request_id, "transactionId": t.transaction_id, "status": t.status,
                              "sessionStatus": t.session_status} for t in transfers]}



def assert_may_view(client_id, transaction_id=None, consent_id=None):
    """Access control on received data: records of a consent that has ended are not decrypted for viewing."""
    if not consent_id and transaction_id:
        transfer = HiuTransfer.query.filter_by(api_client_id=client_id, transaction_id=str(transaction_id)).first()
        consent_id = transfer.consent_id if transfer else None
    if not consent_id:
        return
    row = _own(consent_id, client_id)
    if row is None:
        return
    reason = reason_unusable(row)
    if reason:
        _deny(reason, f"Consent is {reason}: its records may not be opened", consent_id)
