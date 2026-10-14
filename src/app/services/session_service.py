"""
Access/refresh tokens issued to client apps.

Tokens are stateless JWTs (HS256). Revocation works through ApiClient.token_version: rotating a
secret or disabling a client bumps the version, and tokens with an older version are rejected.
No row is written per session, which matters because i-medicus opens a session before every call.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from flask import current_app

from src.app.errors import AuthError, ForbiddenError, ValidationError
from src.app.extensions import db
from src.app.models import ApiClient

ISSUER = "abdm-gateway"
ALGORITHM = "HS256"


def _encode(client, token_type, ttl):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER,
        "sub": client.client_id,
        "typ": token_type,
        "ver": client.token_version,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET_KEY"], algorithm=ALGORITHM)


def issue_tokens(client):
    access_ttl = current_app.config["CLIENT_TOKEN_EXPIRES"]
    refresh_ttl = current_app.config["CLIENT_REFRESH_TOKEN_EXPIRES"]
    return {
        "accessToken": _encode(client, "access", access_ttl),
        "expiresIn": access_ttl,
        "refreshToken": _encode(client, "refresh", refresh_ttl),
        "refreshExpiresIn": refresh_ttl,
        "tokenType": "Bearer",
    }


def _active_client(client_id):
    client = ApiClient.query.filter_by(client_id=client_id).first() if client_id else None
    if client is None:
        raise AuthError("Invalid client credentials", code="INVALID_CLIENT")
    if not client.is_active:
        raise ForbiddenError("This client is disabled", code="CLIENT_DISABLED")
    return client


def create_session(client_id, client_secret):
    if not client_id or not client_secret:
        raise ValidationError("clientId and clientSecret are required")
    client = _active_client(client_id)
    if not client.verify_secret(client_secret):
        raise AuthError("Invalid client credentials", code="INVALID_CLIENT")
    client.last_used_at = datetime.now(timezone.utc)
    db.session.commit()
    return client, issue_tokens(client)


def decode_token(token, expected_type):
    try:
        claims = jwt.decode(
            token,
            current_app.config["JWT_SECRET_KEY"],
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "typ", "ver"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Access token has expired. Create a new session.", code="TOKEN_EXPIRED")
    except jwt.InvalidTokenError:
        raise AuthError("Invalid access token", code="INVALID_TOKEN")
    if claims.get("typ") != expected_type:
        raise AuthError("Invalid access token", code="INVALID_TOKEN")
    return claims


def client_from_token(token, expected_type="access"):
    claims = decode_token(token, expected_type)
    client = _active_client(claims["sub"])
    if claims["ver"] != client.token_version:
        raise AuthError("Token has been revoked. Create a new session.", code="TOKEN_REVOKED")
    return client


def refresh_session(refresh_token):
    if not refresh_token:
        raise ValidationError("refreshToken is required")
    client = client_from_token(refresh_token, expected_type="refresh")
    return client, issue_tokens(client)
