"""
Consents ABDM notified us about, and the check a health-information request must pass (M2, WBS Day 11).

`record_notification` runs when the consent/request/hip/notify callback arrives (the callback receiver calls it).
`assert_usable` runs when the client app acknowledges a health-information request: the consent must exist, still be
GRANTED, not be past its end date, and the requested date range must lie inside the granted one.
"""
import json
import logging
from datetime import datetime, timezone

from src.app.callbacks import vault
from src.app.errors import ForbiddenError
from src.app.extensions import db
from src.app.models import CallbackEvent, HipConsent
from src.app.models.base import as_utc, utcnow

logger = logging.getLogger(__name__)

GRANTED = "GRANTED"


def parse_time(value):
    """ABDM timestamps ("2024-05-09T09:57:59.626Z") -> aware datetime, or None when missing/unreadable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_notification(body, hip_id, api_client_id):
    """Store or update the consent named by a consent/request/hip/notify body. Never raises: a bookkeeping problem
    must not make the callback fail (ABDM would retry it)."""
    try:
        notification = (body or {}).get("notification") or {}
        consent_id = notification.get("consentId")
        status = str(notification.get("status") or "").upper()
        if not consent_id or not status:
            return None
        detail = notification.get("consentDetail") or {}
        permission = detail.get("permission") or {}
        date_range = permission.get("dateRange") or {}
        row = HipConsent.query.filter_by(consent_id=str(consent_id)).first()
        if row is None:
            row = HipConsent(consent_id=str(consent_id), status=status)
            db.session.add(row)
        row.status = status
        row.hip_id = (detail.get("hip") or {}).get("id") or hip_id
        row.api_client_id = api_client_id
        if detail:  # a REVOKED notification may carry no detail: keep what we stored when it was granted
            row.hi_types = detail.get("hiTypes")
            row.care_contexts = detail.get("careContexts")
            row.date_from = parse_time(date_range.get("from"))
            row.date_to = parse_time(date_range.get("to"))
            row.erase_at = parse_time(permission.get("dataEraseAt"))
        db.session.commit()
        return row
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("Could not store consent notification")
        return None


def request_of(event):
    """The body of the stored health-information request callback, or {}."""
    try:
        data = json.loads(vault.open_text(event.body_enc))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _deny(reason, message, consent_id):
    raise ForbiddenError(message, code="CONSENT_INVALID", details={"reason": reason, "consentId": consent_id})


def assert_usable(consent_id, hi_request, client_id):
    """Raise CONSENT_INVALID (HTTP 403) unless the consent allows this request. Returns the consent row."""
    row = HipConsent.query.filter_by(consent_id=str(consent_id)).first() if consent_id else None
    if row is None or (row.api_client_id is not None and row.api_client_id != client_id):
        _deny("NOT_FOUND", "No consent with this id was received", consent_id)
    if row.status != GRANTED:
        _deny(row.status, f"Consent is {row.status}", consent_id)
    now = utcnow()
    if row.erase_at and as_utc(row.erase_at) <= now:
        _deny("EXPIRED", "Consent has expired", consent_id)
    asked = (hi_request or {}).get("dateRange") or {}
    asked_from, asked_to = parse_time(asked.get("from")), parse_time(asked.get("to"))
    if (asked_from and row.date_from and asked_from < as_utc(row.date_from)) or \
            (asked_to and row.date_to and asked_to > as_utc(row.date_to)):
        _deny("OUT_OF_RANGE", "Requested date range is outside the consent", consent_id)
    return row
