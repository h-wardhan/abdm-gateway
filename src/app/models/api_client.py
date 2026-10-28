"""
A client application (tenant) allowed to call the gateway, e.g. i-medicus or RazorClinic.

The client secret is a high-entropy random string, so it is stored as a SHA-256 hash: that is
safe for random API keys and keeps verification fast (i-medicus opens a session before every call,
so bcrypt here would add ~200 ms to each request).
"""
import hashlib
import hmac
import re
import secrets

from src.app.extensions import db
from src.app.models.base import TimestampMixin


def hash_secret(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "client"


class ApiClient(TimestampMixin, db.Model):
    __tablename__ = "api_clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    client_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    client_secret_hash = db.Column(db.String(64), nullable=False)
    # Bumped on secret rotation / disable: tokens carrying an older version stop working
    token_version = db.Column(db.Integer, nullable=False, default=1)
    hip_id = db.Column(db.String(64), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # Flask-Limiter syntax, e.g. "120 per minute"; empty = DEFAULT_RATE_LIMIT
    rate_limit = db.Column(db.String(50), nullable=True)
    # Comma-separated IPs; empty = any IP
    allowed_ips = db.Column(db.Text, nullable=True)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Default webhook of the client app (one URL for all its clinics). ABDM callbacks for a facility that
    # is not registered yet go here; the secret signs what is forwarded (stored encrypted, see callbacks/vault.py)
    callback_url = db.Column(db.String(500), nullable=True)
    webhook_secret_enc = db.Column(db.Text, nullable=True)

    @classmethod
    def create(cls, name, hip_id=None, rate_limit=None, allowed_ips=None):
        """Create a client. Returns (client, plain_secret); the secret is shown only once."""
        secret = secrets.token_urlsafe(32)
        client = cls(
            name=name,
            client_id=f"{_slug(name)}-{secrets.token_hex(6)}",
            client_secret_hash=hash_secret(secret),
            hip_id=hip_id,
            rate_limit=rate_limit,
            allowed_ips=allowed_ips,
            token_version=1,
            is_active=True,
        )
        return client, secret

    def rotate_secret(self):
        secret = secrets.token_urlsafe(32)
        self.client_secret_hash = hash_secret(secret)
        self.token_version += 1
        return secret

    def verify_secret(self, secret):
        return bool(secret) and hmac.compare_digest(self.client_secret_hash, hash_secret(secret))

    def ip_allowed(self, ip):
        if not self.allowed_ips:
            return True
        allowed = {item.strip() for item in self.allowed_ips.split(",") if item.strip()}
        return ip in allowed

    def __repr__(self):
        return f"<ApiClient {self.client_id} active={self.is_active}>"
