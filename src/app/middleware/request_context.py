"""
Per-request context: request id, timing, access log, security headers.

The client's REQUEST-ID header (i-medicus sends one) is reused when it looks sane, so one id
can be followed across the client app's logs, our logs and the ABDM call.
"""
import logging
import re
import time
import uuid

from flask import g, request

logger = logging.getLogger("abdm_gateway.access")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def _incoming_request_id():
    for header in ("REQUEST-ID", "X-Request-ID"):
        value = request.headers.get(header, "").strip()
        if value and _SAFE_ID.match(value):
            return value
    return str(uuid.uuid4())


def init_request_context(app):
    @app.before_request
    def _start():
        g.request_id = _incoming_request_id()
        g.started_at = time.perf_counter()

    @app.after_request
    def _finish(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"

        if not request.path.startswith(("/swaggerui", "/docs")):
            latency_ms = round((time.perf_counter() - getattr(g, "started_at", time.perf_counter())) * 1000, 1)
            g.latency_ms = latency_ms
            logger.info(
                "%s %s %s",
                request.method,
                request.path,
                response.status_code,
                extra={
                    "method": request.method,
                    "route": request.path,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                    "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
                },
            )
        return response
