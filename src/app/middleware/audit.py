"""
Writes one masked api_audit_logs row per /api/ request.

Auditing must never break the actual response, so failures are logged and swallowed.
"""
import logging

from flask import g, request

from src.app.extensions import db
from src.app.middleware.tenant_auth import client_ip
from src.app.models import ApiAuditLog
from src.utils.masking import mask_payload, shorten_payload

logger = logging.getLogger(__name__)

AUDITED_PREFIX = "/api/"
# Routes whose bodies are the patient's health record or key material: the audit row keeps the call, never the body
# (the masked, shortened copy of a FHIR payload would still hold names, ABHA addresses and medicines).
NO_BODY_PREFIXES = ("/api/v1/fhir/",)


def _error_code(response):
    if response.status_code < 400 or not response.is_json:
        return None
    body = response.get_json(silent=True) or {}
    return ((body.get("message") or {}).get("error") or {}).get("code") if isinstance(body.get("message"), dict) else None


def _audited_body():
    if not request.is_json:
        return None
    if request.path.startswith(NO_BODY_PREFIXES):
        return {"note": "not stored: health record or key material"}
    return mask_payload(shorten_payload(request.get_json(silent=True)))


def init_audit(app):
    @app.after_request
    def _audit(response):
        if not request.path.startswith(AUDITED_PREFIX):
            return response
        try:
            client = getattr(g, "client", None)
            db.session.add(ApiAuditLog(
                request_id=getattr(g, "request_id", "-"),
                api_client_id=client.id if client else None,
                method=request.method,
                route=request.path,
                http_status=response.status_code,
                error_code=_error_code(response),
                abdm_endpoint=getattr(g, "abdm_endpoint", None),
                abdm_status=getattr(g, "abdm_status", None),
                latency_ms=getattr(g, "latency_ms", None),
                ip=client_ip(),
                request_body=_audited_body(),
            ))
            db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            logger.exception("Could not write audit log")
        return response
