"""
Build the request that delivers a stored ABDM callback to the client app.

The client app (i-medicus) already has webhook handlers written for VCDoctor, which forwarded ABDM's body
as it was, with the ABDM path after the webhook URL and the request id in a `request-id` header. We do
the same, so those handlers keep working:

    POST {callback_url}/api/v3/hip/patient/share
    request-id: <ABDM REQUEST-ID>
    <body exactly as ABDM sent it>

On top of that the gateway adds an event id (to dedupe retries) and an HMAC signature the client may verify:
    X-Gateway-Signature: sha256=HMAC_SHA256(webhook_secret, "<X-Gateway-Timestamp>.<body>")
"""
import hashlib
import hmac
from datetime import datetime, timezone

from flask import current_app

from src.app.callbacks import vault

# Stored ABDM headers that are passed on to the client app (header name as stored -> as sent)
PASSED_HEADERS = {"timestamp": "timestamp", "x-cm-id": "x-cm-id", "x-hip-id": "x-hip-id"}
# ABDM headers worth keeping with the stored event (never Authorization)
KEPT_HEADERS = ("REQUEST-ID", "TIMESTAMP", "X-CM-ID", "X-HIP-ID", "Content-Type")


def sign(secret, timestamp, body_bytes):
    message = timestamp.encode() + b"." + body_bytes
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def kept_headers(headers):
    return {name: headers[name] for name in KEPT_HEADERS if headers.get(name)}


def build_request(event, registration):
    """Return (url, headers, body_bytes) for delivering `event` to `registration.callback_url`."""
    body_bytes = vault.open_text(event.body_enc).encode("utf-8")
    secret = vault.open_secret(registration.webhook_secret_enc)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"abdm-gateway/{current_app.config['VERSION']}",
        "request-id": event.request_id,
        "X-Gateway-Event-Id": str(event.id),
        "X-Gateway-Timestamp": timestamp,
        "X-Gateway-Signature": sign(secret, timestamp, body_bytes),
    }
    stored = {k.lower(): v for k, v in (event.headers or {}).items()}
    for stored_name, sent_name in PASSED_HEADERS.items():
        if stored.get(stored_name):
            headers[sent_name] = stored[stored_name]

    url = f"{registration.callback_url.rstrip('/')}/{event.path}"
    return url, headers, body_bytes
