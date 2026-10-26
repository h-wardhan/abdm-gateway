"""
HIP registry: which client app owns a facility and the URL its callbacks are delivered to.

Callback URLs come from client apps, so they are checked before they are stored and again before every
delivery:
- https only (http only when CALLBACK_ALLOW_HTTP, for staging servers without TLS)
- no credentials in the URL
- never a host in CALLBACK_BLOCKED_HOSTS (production webhooks)
- never a private / loopback / link-local address (SSRF), unless CALLBACK_ALLOW_PRIVATE_URLS (local dev)
"""
import ipaddress
import re
import secrets
import socket
from urllib.parse import urlsplit

from flask import current_app

from src.app.callbacks import vault
from src.app.errors import ConflictError, NotFoundError, ValidationError
from src.app.extensions import db
from src.app.models import HipRegistration

_HIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{2,63}$")


class UnsafeUrlError(ValidationError):
    """The callback URL points somewhere we must not call."""

    code = "UNSAFE_CALLBACK_URL"


class UnresolvableHostError(ValidationError):
    """The callback host has no DNS record right now (temporary: delivery is retried)."""

    code = "CALLBACK_HOST_UNRESOLVABLE"


def _is_forbidden_ip(ip):
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified)


def _addresses(host):
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise UnresolvableHostError(f"Callback host {host!r} does not resolve")
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def validate_callback_url(url):
    """Return the cleaned URL (no trailing slash) or raise UnsafeUrlError / UnresolvableHostError."""
    config = current_app.config
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        raise UnsafeUrlError("callbackUrl is not a valid URL")

    allowed_schemes = ("https", "http") if config["CALLBACK_ALLOW_HTTP"] else ("https",)
    if parts.scheme not in allowed_schemes:
        raise UnsafeUrlError("callbackUrl must use https")
    if not host:
        raise UnsafeUrlError("callbackUrl has no host")
    if parts.username or parts.password:
        raise UnsafeUrlError("callbackUrl must not contain credentials")
    if parts.query or parts.fragment:
        raise UnsafeUrlError("callbackUrl must not contain a query string or fragment")
    if host in config["CALLBACK_BLOCKED_HOSTS"]:
        raise UnsafeUrlError("callbackUrl points to a blocked host")
    if not config["CALLBACK_ALLOW_PRIVATE_URLS"] and any(_is_forbidden_ip(ip) for ip in _addresses(host)):
        raise UnsafeUrlError("callbackUrl must not point to a private or internal address")
    return url.rstrip("/")


def validate_hip_id(hip_id):
    hip_id = (hip_id or "").strip()
    if not _HIP_ID_RE.match(hip_id):
        raise ValidationError("hipId must be 3-64 characters: letters, digits, '_', '.' or '-'",
                              code="INVALID_HIP_ID", details={"field": "hipId"})
    return hip_id


def get(hip_id):
    return HipRegistration.query.filter_by(hip_id=hip_id).first()


def upsert(client, hip_id, callback_url, name=None):
    """Create or update a HIP for `client`. Returns (registration, plain_secret_or_None).

    The webhook secret is returned only when the registration is created."""
    hip_id = validate_hip_id(hip_id)
    callback_url = validate_callback_url(callback_url)
    reg = get(hip_id)
    if reg is not None and reg.api_client_id != client.id:
        raise ConflictError("This hipId is registered to another client", code="HIP_TAKEN")

    secret = None
    if reg is None:
        secret = secrets.token_urlsafe(32)
        reg = HipRegistration(api_client_id=client.id, hip_id=hip_id, webhook_secret_enc=vault.seal_secret(secret))
        db.session.add(reg)
    reg.callback_url = callback_url
    reg.is_active = True
    if name is not None:
        reg.name = name
    db.session.commit()
    return reg, secret


def get_for_client(client, hip_id):
    reg = get(hip_id)
    if reg is None or reg.api_client_id != client.id:
        raise NotFoundError("HIP not found", code="HIP_NOT_FOUND")
    return reg


def disable(reg):
    reg.is_active = False
    db.session.commit()
    return reg


def rotate_secret(reg):
    secret = secrets.token_urlsafe(32)
    reg.webhook_secret_enc = vault.seal_secret(secret)
    db.session.commit()
    return secret


def single_active_registration():
    """The only active registration, when exactly one exists (fallback for callbacks without a hip id)."""
    rows = HipRegistration.query.filter_by(is_active=True).limit(2).all()
    return rows[0] if len(rows) == 1 else None
