from flask import g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_restx import Api
from flask_sqlalchemy import SQLAlchemy

from src.app.cache import Cache

db = SQLAlchemy()
migrate = Migrate()
cache = Cache()


def _rate_limit_key():
    """Rate limit per tenant once authenticated, per IP before that."""
    client = getattr(g, "client", None)
    return f"client:{client.client_id}" if client else get_remote_address()


limiter = Limiter(key_func=_rate_limit_key)

AUTHORIZATIONS = {
    "AccessToken": {
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
        "description": "Access token from POST /api/v1/auth/session. 'Bearer <token>' or the raw token both work.",
    },
    "ClientId": {"type": "apiKey", "in": "header", "name": "Client-Id"},
}

api = Api(
    title="ABDM Gateway API",
    version="1.0",
    description=(
        "Middleware between client applications (any tech stack) and ABDM (Ayushman Bharat Digital Mission).\n\n"
        "Phase 1 covers Milestone 1: ABHA creation, login/verification, profile, card and QR.\n\n"
        "Every JSON response uses one envelope:\n"
        "- success: `{success: true, data: {...}, requestId}`\n"
        "- error: `{success: false, code: \"error\", message: {error: {code, message}}, requestId}`"
    ),
    doc="/docs",
    authorizations=AUTHORIZATIONS,
    security=["AccessToken", "ClientId"],
    catch_all_404s=True,
)
