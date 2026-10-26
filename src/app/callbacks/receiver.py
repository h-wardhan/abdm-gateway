"""
Where ABDM delivers callbacks: POST /api/v3/<path> (and /v3/<path>).

The handler only checks, stores and queues; it answers 202 at once so ABDM is never kept waiting.
The actual delivery to the client app is done by the outbox worker (see outbox/engine.py).

1. Authenticate the callback (ABDM's JWT, see callbacks/auth.py)
2. Find the facility it is about and the client app that owns it (callbacks/routing.py, registry.py)
3. Drop it if ABDM already sent this REQUEST-ID for this path
4. Store it (encrypted) and queue a forward job. With no matching facility the callback is stored as
   `unrouted` and logged as an error: it can be re-routed by hand, nothing is lost.
"""
import json
import logging
import uuid

from flask import Blueprint, current_app, g, request
from sqlalchemy.exc import IntegrityError

from src.app.callbacks import auth, forwarder, registry, vault
from src.app.callbacks.routing import normalize_path, resolve_hip_id
from src.app.common.response import success
from src.app.errors import ValidationError
from src.app.extensions import db, limiter
from src.app.models import CallbackEvent
from src.app.models import callback_event as ce
from src.app.models import outbox_job as oj
from src.app.outbox import engine

logger = logging.getLogger("abdm_gateway.callbacks")

bp = Blueprint("abdm_callbacks", __name__)


@bp.before_request
def _allow_large_bodies():
    request.max_content_length = current_app.config["CALLBACK_MAX_BYTES"]


def _parse_body(raw):
    try:
        text = raw.decode("utf-8")
        body = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise ValidationError("Callback body must be JSON", code="INVALID_CALLBACK_BODY")
    if not isinstance(body, (dict, list)):
        raise ValidationError("Callback body must be a JSON object", code="INVALID_CALLBACK_BODY")
    return text, body


def _route(headers, body):
    """(registration or None, hip_id or None, reason when there is no registration)."""
    hip_id = resolve_hip_id(headers, body if isinstance(body, dict) else {})
    if hip_id:
        registration = registry.get(hip_id)
        if registration is None:
            return None, hip_id, "hip is not registered"
        if not registration.is_active:
            return None, hip_id, "hip is disabled"
        return registration, hip_id, None
    registration = registry.single_active_registration()
    if registration is not None:
        return registration, registration.hip_id, None
    return None, None, "no hip id in the callback and more than one (or no) hip is registered"


@bp.route("/api/v3/<path:subpath>", methods=["POST"])
@bp.route("/v3/<path:subpath>", methods=["POST"])
@limiter.exempt
def receive(subpath):
    path = normalize_path(f"v3/{subpath}")
    auth_result = auth.authenticate(request.headers.get("Authorization"))
    text, body = _parse_body(request.get_data())

    request_id = (request.headers.get("REQUEST-ID") or "").strip()[:64] or str(uuid.uuid4())
    registration, hip_id, unrouted_reason = _route(request.headers, body)

    event = CallbackEvent(
        request_id=request_id,
        path=path,
        hip_id=hip_id,
        api_client_id=registration.api_client_id if registration else None,
        status=ce.RECEIVED if registration else ce.UNROUTED,
        auth_result=auth_result,
        body_enc=vault.seal_text(text),
        headers=forwarder.kept_headers(request.headers),
        error=unrouted_reason,
    )
    db.session.add(event)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        logger.info("Duplicate callback ignored", extra={"callback_path": path, "abdm_request_id": request_id})
        return success({"status": "accepted", "duplicate": True}, 202)

    if registration:
        engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=registration.api_client_id)
    db.session.commit()

    if registration:
        logger.info("Callback queued", extra={"callback_path": path, "hip_id": hip_id, "abdm_request_id": request_id,
                                              "auth_result": auth_result})
    else:
        logger.error("Callback could not be routed", extra={"callback_path": path, "hip_id": hip_id,
                                                            "abdm_request_id": request_id, "reason": unrouted_reason})
    g.callback_event_id = event.id
    return success({"status": "accepted"}, 202)
