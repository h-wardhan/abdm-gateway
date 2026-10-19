"""
Duplicate-request guard for every authenticated POST (double clicks, client retries, slow networks).

Without an Idempotency-Key header (i-medicus today):
  the same client sending the same POST (method + path + query + body) while the first one is still
  being processed gets 409 DUPLICATE_REQUEST. Once the first finishes, the same request is allowed again
  (e.g. "resend OTP" a minute later).

With an Idempotency-Key header (any client that wants safe retries):
  the first request runs and its response (< 500) is stored for IDEMPOTENCY_TTL_SECONDS. A repeat with the
  same key and body gets that stored response back (header Idempotent-Replayed: true) without calling ABDM
  again. Same key while the first is still running: 409 DUPLICATE_REQUEST. Same key with a different
  request: 422 IDEMPOTENCY_KEY_REUSED.

Errors raised by the handler (validation, ABDM errors) and 5xx responses are not stored, so a retry with
the same key runs again. Locks live in the cache: with several Gunicorn workers set REDIS_URL, otherwise
each worker only sees its own requests.
"""
import hashlib
import re
from functools import wraps

from flask import current_app, g, make_response, request

from src.app.errors import ConflictError, ValidationError
from src.app.extensions import cache

HEADER = "Idempotency-Key"
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _fingerprint():
    digest = hashlib.sha256()
    for part in (request.method, request.path, request.query_string.decode("latin-1")):
        digest.update(part.encode() + b"\n")
    digest.update(request.get_data(cache=True))
    return digest.hexdigest()


def _duplicate():
    return ConflictError("The same request is already being processed. Please wait for its response.",
                         code="DUPLICATE_REQUEST")


def _storable(result):
    """(body, status) tuples from success()/from_abdm() with status < 500, else None."""
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], dict) and isinstance(result[1], int):
        return result if result[1] < 500 else None
    if isinstance(result, dict):
        return result, 200
    return None


def idempotent(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method != "POST":
            return fn(*args, **kwargs)

        client_id = g.client.client_id
        fingerprint = _fingerprint()
        lock_ttl = current_app.config["IDEMPOTENCY_LOCK_SECONDS"]
        key = request.headers.get(HEADER, "").strip()

        if not key:
            lock = f"inflight:{client_id}:{fingerprint}"
            if not cache.add(lock, 1, ttl=lock_ttl):
                raise _duplicate()
            try:
                return fn(*args, **kwargs)
            finally:
                cache.delete(lock)

        if not _SAFE_KEY.match(key):
            raise ValidationError(f"{HEADER} must be 1-128 characters: letters, digits, '.', '_', ':' or '-'",
                                  code="INVALID_IDEMPOTENCY_KEY")
        slot = f"idem:{client_id}:{key}"
        if not cache.add(slot, {"state": "running", "fp": fingerprint}, ttl=lock_ttl):
            saved = cache.get(slot) or {}
            if saved.get("fp") != fingerprint:
                raise ValidationError(f"This {HEADER} was already used for a different request",
                                      code="IDEMPOTENCY_KEY_REUSED", status=422)
            if saved.get("state") != "done":
                raise _duplicate()
            response = make_response(saved["body"], saved["status"])
            response.headers["Idempotent-Replayed"] = "true"
            return response

        stored = False
        try:
            result = fn(*args, **kwargs)
            storable = _storable(result)
            if storable:
                body, status = storable
                cache.set(slot, {"state": "done", "fp": fingerprint, "body": body, "status": status},
                          ttl=current_app.config["IDEMPOTENCY_TTL_SECONDS"])
                stored = True
            return result
        finally:
            if not stored:
                cache.delete(slot)

    return wrapper
