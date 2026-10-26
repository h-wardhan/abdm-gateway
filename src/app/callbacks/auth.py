"""
Check that a callback really comes from ABDM.

ABDM signs callbacks with a JWT (RS256) in the Authorization header. The public keys are published as a
JWKS at ABDM_JWKS_URL; they are cached, and refreshed once when a token names a key we do not know yet
(ABDM rotates keys).

CALLBACK_AUTH_MODE:
  jwt      invalid or missing token -> 401 (default, and the only mode allowed in production)
  observe  verify and record the result, never reject (to look at real sandbox callbacks first)
  off      no verification (local mock ABDM only)
"""
import json
import logging

import jwt
import requests
from flask import current_app

from src.app.errors import AuthError
from src.app.extensions import cache

logger = logging.getLogger(__name__)

JWKS_CACHE_KEY = "abdm:jwks"
OK = "ok"
SKIPPED = "skipped"


class CallbackAuthError(Exception):
    """Token missing/invalid. `reason` is a short machine readable word, safe to log."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _fetch_jwks():
    try:
        response = requests.get(
            current_app.config["ABDM_JWKS_URL"],
            headers={"Accept": "application/json"},
            timeout=(current_app.config["ABDM_TIMEOUT_CONNECT"], current_app.config["ABDM_TIMEOUT_READ"]),
        )
        response.raise_for_status()
        keys = response.json().get("keys")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.warning("Could not fetch ABDM JWKS", extra={"error": str(exc)})
        raise CallbackAuthError("jwks_unavailable")
    if not isinstance(keys, list) or not keys:
        raise CallbackAuthError("jwks_empty")
    cache.set(JWKS_CACHE_KEY, keys, ttl=current_app.config["ABDM_JWKS_CACHE_SECONDS"])
    return keys


def _keys(force_refresh=False):
    if not force_refresh:
        cached = cache.get(JWKS_CACHE_KEY)
        if cached:
            return cached
    return _fetch_jwks()


def _find_key(kid, keys):
    return next((k for k in keys if k.get("kid") == kid), None)


def _token_from(header):
    header = (header or "").strip()
    if header.lower() == "bearer":
        header = ""
    elif header.lower().startswith("bearer "):
        header = header[7:].strip()
    if not header:
        raise CallbackAuthError("missing_token")
    return header


def verify(authorization_header):
    """Return the token claims, or raise CallbackAuthError."""
    token = _token_from(authorization_header)
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        raise CallbackAuthError("malformed_token")

    jwk = _find_key(kid, _keys())
    if jwk is None:
        jwk = _find_key(kid, _keys(force_refresh=True))
    if jwk is None:
        raise CallbackAuthError("unknown_key")

    try:
        key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        return jwt.decode(token, key, algorithms=["RS256"], options={"require": ["exp"], "verify_aud": False})
    except jwt.ExpiredSignatureError:
        raise CallbackAuthError("expired")
    except (jwt.PyJWTError, ValueError):
        raise CallbackAuthError("invalid_token")


def authenticate(authorization_header):
    """Apply CALLBACK_AUTH_MODE. Returns the auth_result string to store with the callback."""
    mode = current_app.config["CALLBACK_AUTH_MODE"]
    if mode == "off":
        return SKIPPED
    try:
        verify(authorization_header)
        return OK
    except CallbackAuthError as exc:
        if mode == "observe":
            logger.warning("Callback JWT not valid (observe mode, accepted anyway)", extra={"reason": exc.reason})
            return f"failed:{exc.reason}"
        logger.warning("Callback rejected", extra={"reason": exc.reason})
        raise AuthError("Callback authentication failed", code="CALLBACK_AUTH_FAILED")
