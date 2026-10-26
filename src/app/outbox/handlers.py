"""Job handlers. Each returns Ok / Retry / Fail (see engine.py)."""
import logging

import requests
from flask import current_app

from src.app.callbacks import forwarder, registry
from src.app.extensions import db
from src.app.models import CallbackEvent
from src.app.models import outbox_job as oj
from src.app.outbox.engine import Fail, Ok, Retry, handler

logger = logging.getLogger(__name__)

_session = requests.Session()
CONNECT_TIMEOUT = 5


@handler(oj.KIND_FORWARD_CALLBACK)
def forward_callback(job):
    """Deliver a stored ABDM callback to the client app's webhook."""
    event = db.session.get(CallbackEvent, job.callback_event_id) if job.callback_event_id else None
    if event is None:
        return Fail("callback event not found")
    registration = registry.get(event.hip_id) if event.hip_id else None
    if registration is None:
        return Fail("hip is not registered")
    if not registration.is_active:
        return Fail("hip is disabled")

    # The URL was checked when it was registered; check again, it may have been changed or its DNS may have moved
    try:
        registry.validate_callback_url(registration.callback_url)
    except registry.UnresolvableHostError as exc:
        return Retry(exc.message)
    except registry.UnsafeUrlError as exc:
        return Fail(exc.message)

    url, headers, body = forwarder.build_request(event, registration)
    try:
        # No redirects: a redirect could send the callback somewhere that was never checked
        response = _session.post(
            url, data=body, headers=headers, allow_redirects=False,
            timeout=(CONNECT_TIMEOUT, current_app.config["OUTBOX_HTTP_TIMEOUT"]),
        )
    except requests.Timeout:
        return Retry("client app timed out")
    except requests.RequestException as exc:
        return Retry(f"could not reach client app ({type(exc).__name__})")

    status = response.status_code
    if 200 <= status < 300:
        return Ok()
    if status in (408, 429) or status >= 500:
        return Retry(f"client app returned HTTP {status}", status)
    return Fail(f"client app returned HTTP {status}", status)
