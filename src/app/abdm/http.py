"""
Low-level HTTP client for ABDM.

Every ABDM call goes through `abdm_request()`, which:
- adds REQUEST-ID / TIMESTAMP / X-CM-ID and the cached gateway token (Authorization: Bearer ...)
- retries once with a fresh gateway token if ABDM answers 401
- retries GETs once on timeouts / 5xx (never POSTs: an OTP must not be sent twice)
- turns ABDM errors into AbdmUpstreamError / AbdmUnavailableError, keeping ABDM's status, code and text
- logs every call to the "abdm.http" logger (logs/abdm.log) and failed calls to abdm_error_logs
"""
import logging
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import current_app, g, has_request_context

from src.app.errors import AbdmUnavailableError, AbdmUpstreamError, AuthError
from src.config.logger import ABDM_LOGGER
from src.utils.masking import mask_text

logger = logging.getLogger(ABDM_LOGGER)

_session = requests.Session()


def abdm_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def base_headers():
    return {
        "Accept": "application/json",
        "REQUEST-ID": str(uuid.uuid4()),
        "TIMESTAMP": abdm_timestamp(),
        "X-CM-ID": current_app.config["ABDM_X_CM_ID"],
    }


def bearer(token):
    token = (token or "").strip()
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def base_url(target):
    return {"abha": current_app.config["ABDM_ABHA_URL"], "facility": current_app.config["ABDM_FACILITY_URL"]}.get(
        target, current_app.config["ABDM_GATEWAY_URL"])


def parse_abdm_error(response):
    """Pull (code, message) out of the different error shapes ABDM uses.

    Seen shapes: {"code": "ABDM-1204", "message": "..."}, {"error": {"code", "message"}},
    {"message": "..."}, and field validation maps like {"loginId": "Invalid LoginId"}.
    """
    try:
        body = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return None, text[:500] or f"ABDM returned HTTP {response.status_code}"

    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else None
        if error:
            return error.get("code"), str(error.get("message") or "")
        if body.get("message") or body.get("code"):
            message = body.get("message")
            if isinstance(message, dict):
                message = message.get("message") or str(message)
            return body.get("code"), str(message or body.get("code"))
        # Field validation map: {"loginId": "Invalid LoginId", "otpValue": "..."}
        fields = {k: v for k, v in body.items() if isinstance(v, str) and k not in ("timestamp", "path", "status")}
        if fields:
            return "ABDM-VALIDATION", ", ".join(fields.values())
    return None, str(body)[:500]


def _record_error(endpoint, status, code, message, raw):
    try:
        from src.app.extensions import db
        from src.app.models import AbdmErrorLog

        client = getattr(g, "client", None) if has_request_context() else None
        db.session.add(AbdmErrorLog(
            request_id=getattr(g, "request_id", None) if has_request_context() else None,
            api_client_id=client.id if client else None,
            abdm_endpoint=endpoint,
            http_status=status,
            abdm_code=code,
            abdm_message=mask_text(message)[:2000] if message else None,
            raw_body=mask_text(raw)[:4000] if raw else None,
        ))
        db.session.commit()
    except Exception:  # noqa: BLE001  (never let error bookkeeping break the request)
        logger.exception("Could not store ABDM error log")
        try:
            from src.app.extensions import db
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass


def _send(method, url, headers, json, timeout):
    return _session.request(method, url, headers=headers, json=json, timeout=timeout)


def abdm_request(
    method,
    path,
    *,
    target="abha",
    json=None,
    headers=None,
    authenticated=True,
    raw=False,
    endpoint_name=None,
):
    """Call ABDM. Returns parsed JSON (dict/list), or the `requests.Response` when raw=True.

    `path` is appended to ABDM_ABHA_URL (target="abha"), ABDM_FACILITY_URL (target="facility") or
    ABDM_GATEWAY_URL (target="gateway").
    """
    from src.app.abdm.gateway_session import get_gateway_token, invalidate_gateway_token

    url = base_url(target) + path
    endpoint = endpoint_name or path
    timeout = (current_app.config["ABDM_TIMEOUT_CONNECT"], current_app.config["ABDM_TIMEOUT_READ"])
    max_attempts = 2 if method.upper() == "GET" else 1
    auth_retried = False
    attempt = 0

    while True:
        attempt += 1
        request_headers = base_headers()
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        if authenticated:
            request_headers["Authorization"] = bearer(get_gateway_token())
        request_headers.update(headers or {})

        started = time.perf_counter()
        try:
            response = _send(method, url, request_headers, json, timeout)
        except requests.Timeout:
            latency = round((time.perf_counter() - started) * 1000, 1)
            logger.warning("ABDM timeout", extra={"abdm_endpoint": endpoint, "latency_ms": latency, "attempt": attempt})
            if attempt < max_attempts:
                continue
            _note_call(endpoint, 504)
            _record_error(endpoint, 504, "TIMEOUT", "ABDM did not respond in time", None)
            raise AbdmUnavailableError("ABDM did not respond in time. Please try again.", code="ABDM_TIMEOUT", status=504)
        except requests.RequestException as exc:
            logger.warning("ABDM connection error", extra={"abdm_endpoint": endpoint, "error": str(exc), "attempt": attempt})
            if attempt < max_attempts:
                continue
            _note_call(endpoint, 503)
            _record_error(endpoint, 503, "CONNECTION_ERROR", str(exc), None)
            raise AbdmUnavailableError()

        latency = round((time.perf_counter() - started) * 1000, 1)
        status = response.status_code
        _note_call(endpoint, status)
        logger.info(
            "ABDM %s %s -> %s",
            method.upper(),
            endpoint,
            status,
            extra={"abdm_endpoint": endpoint, "abdm_status": status, "latency_ms": latency},
        )

        # Expired/invalid gateway token: refresh once and retry
        if status == 401 and authenticated and not auth_retried:
            auth_retried = True
            invalidate_gateway_token()
            continue
        if status >= 500 and attempt < max_attempts:
            continue

        if 200 <= status < 300:
            if raw:
                return response
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                return {"message": response.text}

        code, message = parse_abdm_error(response)
        _record_error(endpoint, status, code, message, response.text)
        logger.warning(
            "ABDM error",
            extra={"abdm_endpoint": endpoint, "abdm_status": status, "abdm_code": code, "abdm_message": message},
        )
        raise _to_app_error(status, code, message, user_token=_has_user_token(headers))


def _has_user_token(headers):
    return any(k.lower() in ("x-token", "t-token", "r-token") for k in (headers or {}))


def _to_app_error(status, code, message, user_token):
    if status == 401:
        if user_token:
            return AuthError(
                message or "ABHA user token is invalid or expired. Please log in to ABHA again.",
                code=code or "ABHA_TOKEN_INVALID",
            )
        # Our own NHA credentials were rejected: not the caller's fault
        return AbdmUpstreamError("ABDM rejected the gateway credentials", code="ABDM_AUTH_FAILED", status=502)
    if status >= 500:
        return AbdmUpstreamError(message or "ABDM is having trouble right now. Please try again later.",
                                 code=code or "ABDM_SERVER_ERROR", status=502)
    # 4xx: keep ABDM's status and text (clients match on it, e.g. 429 "request for new OTP after 30 seconds")
    return AbdmUpstreamError(message or "ABDM rejected the request", code=code or "ABDM_ERROR", status=status)


def _note_call(endpoint, status):
    if has_request_context():
        g.abdm_endpoint = endpoint
        g.abdm_status = status
