"""
Link tokens (M2, HIP-initiated linking): ABDM issues one per patient and facility, asynchronously.

generate-token answers 202 and the token comes later as the callback `hip/token/on-generate-token`. i-medicus asks for
the token and uses it right away, so its first `link/carecontext` goes out before the token exists ("Invalid X Auth
token"), and asking again is refused ("Duplicate Link token request"). The gateway sees the callback first, so it keeps
the token (`record_issued`) and then:
  - a link call uses the token ABDM issued, not the one the client app sent, and waits a few seconds for a token that
    is on its way;
  - a request for a token that already exists is not sent to ABDM again: the stored callback is delivered to the client
    app once more, which is what it was waiting for;
  - a request for a token that is already on its way is not sent twice.
"""
import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from flask import current_app

from src.app.callbacks import vault
from src.app.extensions import db
from src.app.models import HipLinkToken
from src.app.models import outbox_job as oj
from src.app.models.base import as_utc, utcnow
from src.app.outbox import engine

logger = logging.getLogger(__name__)


def _expiry(token):
    """`exp` of a JWT (not verified: only used to know when to ask for a new one)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def _row(hip_id, address):
    return HipLinkToken.query.filter_by(hip_id=hip_id, abha_address=address).first()


def record_issued(body, hip_id, event_id):
    """The `hip/token/on-generate-token` callback: keep the token. Never raises (the callback must be accepted)."""
    try:
        address, token = (body or {}).get("abhaAddress"), (body or {}).get("linkToken")
        if not hip_id or not address or not token or not isinstance(token, str):
            return None
        row = _row(hip_id, address)
        if row is None:
            row = HipLinkToken(hip_id=hip_id, abha_address=address)
            db.session.add(row)
        row.token_enc = vault.seal_text(token)
        row.issued_at = utcnow()
        row.expires_at = _expiry(token)
        row.callback_event_id = event_id
        db.session.commit()
        return row
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("Could not store the link token")
        return None


def _valid(row):
    if row is None or not row.token_enc or row.issued_at is None:
        return False
    return row.expires_at is None or as_utc(row.expires_at) > utcnow() + timedelta(days=1)


def current(hip_id, address):
    """The row of a token that can still be used, or None."""
    row = _row(hip_id, address)
    return row if _valid(row) else None


def _on_its_way(row):
    if row is None or row.requested_at is None:
        return False
    if row.issued_at is not None and as_utc(row.issued_at) >= as_utc(row.requested_at):
        return False
    return as_utc(row.requested_at) > utcnow() - timedelta(seconds=current_app.config["HIP_LINK_TOKEN_PENDING_SECONDS"])


def is_on_its_way(hip_id, address):
    return _on_its_way(_row(hip_id, address))


def note_requested(hip_id, address):
    row = _row(hip_id, address)
    if row is None:
        row = HipLinkToken(hip_id=hip_id, abha_address=address)
        db.session.add(row)
    row.requested_at = utcnow()
    db.session.commit()


def clear_requested(hip_id, address):
    """ABDM refused the request: nothing is on its way."""
    db.session.rollback()
    row = _row(hip_id, address)
    if row is not None:
        row.requested_at = None
        db.session.commit()


def replay(row):
    """Deliver the stored callback to the client app again (it is waiting for exactly this)."""
    if row.callback_event_id is None:
        return False
    event_job = engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=row.callback_event_id, delay=0)
    db.session.commit()
    return event_job is not None


def token_for_call(hip_id, address, client_token):
    """The token to send to ABDM: the one it issued (after waiting a little for one on its way), else the client's."""
    deadline = time.monotonic() + current_app.config["HIP_LINK_TOKEN_WAIT_SECONDS"]
    while True:
        db.session.expire_all()
        if not _on_its_way(_row(hip_id, address)) or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    row = _row(hip_id, address)
    return vault.open_text(row.token_enc) if _valid(row) else client_token
