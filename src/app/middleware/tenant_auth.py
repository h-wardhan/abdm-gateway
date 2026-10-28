"""
Tenant authentication for every ABHA endpoint.

Headers:
  Authorization: <access token>   ("Bearer <token>" also accepted; i-medicus sends the raw token)
  Client-Id:     <client id>      optional, but if sent it must match the token
"""
from functools import wraps

from flask import current_app, g, request
from flask_restx import Resource

from src.app.errors import AuthError, ForbiddenError
from src.app.extensions import limiter
from src.app.middleware.idempotency import idempotent
from src.app.services.session_service import authenticate_client, client_from_token


def _bearer_token():
    raw = request.headers.get("Authorization", "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() if forwarded else request.remote_addr


def tenant_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token:
            raise AuthError("Authorization header is missing", code="MISSING_TOKEN")
        client = client_from_token(token)

        header_client_id = request.headers.get("Client-Id", "").strip()
        if header_client_id and header_client_id != client.client_id:
            raise AuthError("Client-Id does not match the access token", code="CLIENT_MISMATCH")
        if not client.ip_allowed(client_ip()):
            raise ForbiddenError("Requests from this IP are not allowed for this client", code="IP_NOT_ALLOWED")

        g.client = client
        return fn(*args, **kwargs)

    return wrapper


def _credentials_client():
    """The client named by the Client-Id + Client-Secret headers, or None when they are not both sent."""
    client_id = request.headers.get("Client-Id", "").strip()
    secret = request.headers.get("Client-Secret", "").strip()
    return authenticate_client(client_id, secret) if client_id and secret else None


def tenant_or_credentials_required(fn):
    """Like tenant_required, but also accepts Client-Id + Client-Secret headers.

    i-medicus sends the ABHA user token in `Authorization` when it changes a mobile or verifies an email, so that
    header is not a gateway token then. In that case the client is identified by Client-Id + Client-Secret and the
    token is kept as g.user_token (see validators.x_token). A valid gateway token works as usual.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        g.user_token = None
        client = None
        if token:
            try:
                client = client_from_token(token)
            except AuthError:
                client = _credentials_client()
                if client is None:
                    raise
                g.user_token = token
            else:
                header_client_id = request.headers.get("Client-Id", "").strip()
                if header_client_id and header_client_id != client.client_id:
                    raise AuthError("Client-Id does not match the access token", code="CLIENT_MISMATCH")
        else:
            client = _credentials_client()
            if client is None:
                raise AuthError("Authorization header is missing", code="MISSING_TOKEN")
        if not client.ip_allowed(client_ip()):
            raise ForbiddenError("Requests from this IP are not allowed for this client", code="IP_NOT_ALLOWED")

        g.client = client
        return fn(*args, **kwargs)

    return wrapper


def _client_rate_limit():
    client = getattr(g, "client", None)
    return (client.rate_limit if client and client.rate_limit else None) or current_app.config["DEFAULT_RATE_LIMIT"]


class TenantResource(Resource):
    """Base class for authenticated endpoints: tenant auth first, then the per-client rate limit,
    then the duplicate-request guard on POSTs.

    restx applies method_decorators in order, so the last one is outermost and runs first.
    """

    method_decorators = [idempotent, limiter.limit(_client_rate_limit), tenant_required]


class CredentialsResource(TenantResource):
    """TenantResource that also accepts Client-Id + Client-Secret (see tenant_or_credentials_required)."""

    method_decorators = [idempotent, limiter.limit(_client_rate_limit), tenant_or_credentials_required]
