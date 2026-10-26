"""
Replies the gateway sends to ABDM that must not get lost, e.g. the answer to a callback ("on-share", "on-discover"):
call ABDM now, and if ABDM is down or answers with a temporary error, queue the call and let the worker retry it.

Only for calls that are safe to repeat and go to the ABDM gateway (target "gateway"). Calls to the ABHA
service (OTPs, logins) are never queued: an OTP must not be sent twice.
"""
import json
import logging

from cryptography.exceptions import InvalidTag

from src.app.abdm.http import abdm_request
from src.app.callbacks import vault
from src.app.errors import AbdmUnavailableError, AbdmUpstreamError, AppError
from src.app.extensions import db
from src.app.models import outbox_job as oj
from src.app.outbox import engine
from src.app.outbox.engine import Fail, Ok, Retry, handler

logger = logging.getLogger(__name__)

QUEUEABLE_TARGETS = ("gateway",)


def is_retryable(exc):
    """ABDM unreachable, or a temporary error (5xx, 408, 429). Anything else will fail again the same way."""
    if isinstance(exc, AbdmUnavailableError):
        return True
    return isinstance(exc, AbdmUpstreamError) and (exc.status >= 500 or exc.status in (408, 429))


def enqueue_abdm_call(path, body, *, target="gateway", method="POST", headers=None, endpoint_name=None,
                      api_client_id=None, delay=0):
    """Queue an ABDM call for the worker. The caller commits."""
    if target not in QUEUEABLE_TARGETS:
        raise ValueError(f"Calls to the {target!r} service are never queued (they may not be repeated)")
    payload = {"method": method, "path": path, "target": target, "body": body,
               "headers": headers, "endpoint_name": endpoint_name}
    return engine.enqueue(oj.KIND_ABDM_CALL, payload_enc=vault.seal_text(json.dumps(payload)),
                          api_client_id=api_client_id, delay=delay)


def send_or_queue(path, body, *, target="gateway", method="POST", headers=None, endpoint_name=None, api_client_id=None):
    """Call ABDM now. If that fails in a way worth retrying, queue it: returns {"status": "queued", "jobId"}.
    On success returns {"status": "sent", "result": <ABDM's answer>}. Other errors are raised."""
    if target not in QUEUEABLE_TARGETS:
        raise ValueError(f"Calls to the {target!r} service are never queued (they may not be repeated)")
    try:
        result = abdm_request(method, path, target=target, json=body, headers=headers, endpoint_name=endpoint_name)
        return {"status": "sent", "result": result}
    except AppError as exc:
        if not is_retryable(exc):
            raise
        job = enqueue_abdm_call(path, body, target=target, method=method, headers=headers,
                                endpoint_name=endpoint_name, api_client_id=api_client_id)
        db.session.commit()
        logger.warning("ABDM call queued for retry", extra={"abdm_endpoint": endpoint_name or path, "job_id": job.id,
                                                            "error": exc.message})
        return {"status": "queued", "jobId": job.id}


@handler(oj.KIND_ABDM_CALL)
def abdm_call(job):
    try:
        payload = json.loads(vault.open_text(job.payload_enc))
        method, path, target, body = payload["method"], payload["path"], payload["target"], payload["body"]
    except (TypeError, ValueError, KeyError, InvalidTag):  # missing, corrupt, incomplete, or sealed with another key
        return Fail("job has no readable payload")
    try:
        abdm_request(method, path, target=target, json=body,
                     headers=payload.get("headers"), endpoint_name=payload.get("endpoint_name"))
    except AppError as exc:
        if is_retryable(exc):
            return Retry(exc.message, exc.status)
        return Fail(exc.message, exc.status)
    return Ok()
