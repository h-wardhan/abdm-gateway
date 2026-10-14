"""
Gateway (NHA) access token, shared by all ABDM calls.

POST {ABDM_GATEWAY_URL}/api/hiecm/gateway/v3/sessions with our NHA client credentials.
The token is cached until 60 s before it expires, so most requests make no extra call.
"""
import logging
import threading

from flask import current_app

from src.app.errors import AbdmUpstreamError
from src.app.extensions import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "abdm:gateway_token"
SESSION_PATH = "/api/hiecm/gateway/v3/sessions"
_lock = threading.Lock()


def invalidate_gateway_token():
    cache.delete(CACHE_KEY)


def get_gateway_token(force_refresh=False):
    if not force_refresh:
        token = cache.get(CACHE_KEY)
        if token:
            return token

    with _lock:
        if not force_refresh:
            token = cache.get(CACHE_KEY)
            if token:
                return token
        return _fetch_token()


def _fetch_token():
    from src.app.abdm.http import abdm_request

    client_id = current_app.config.get("NHA_CLIENT_ID")
    client_secret = current_app.config.get("NHA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise AbdmUpstreamError(
            "Gateway is not configured with NHA credentials (NHA_CLIENT_ID / NHA_CLIENT_SECRET)",
            code="NHA_CREDENTIALS_MISSING",
            status=503,
        )

    try:
        data = abdm_request(
            "POST",
            SESSION_PATH,
            target="gateway",
            json={"clientId": client_id, "clientSecret": client_secret, "grantType": "client_credentials"},
            authenticated=False,
            endpoint_name="gateway/v3/sessions",
        )
    except AbdmUpstreamError as exc:
        if exc.status in (400, 401, 403):
            raise AbdmUpstreamError("ABDM rejected the gateway's NHA credentials", code="ABDM_AUTH_FAILED", status=502)
        raise

    token = (data or {}).get("accessToken")
    if not token:
        raise AbdmUpstreamError("ABDM session response had no accessToken", code="ABDM_AUTH_FAILED", status=502)

    expires_in = int(data.get("expiresIn") or 600)
    cache.set(CACHE_KEY, token, ttl=max(expires_in - 60, 30))
    logger.info("Fetched new ABDM gateway token", extra={"expires_in": expires_in})
    return token
