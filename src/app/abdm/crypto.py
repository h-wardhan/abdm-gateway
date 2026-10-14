"""
RSA encryption of sensitive fields (Aadhaar, OTP, mobile, email, ABHA number/address, index)
with the ABDM public key: RSA/ECB/OAEPWithSHA-1AndMGF1Padding, base64 output.

The key comes from ABDM_PUBLIC_KEY if set, otherwise from
GET /abha/api/v3/profile/public/certificate (cached).
"""
import base64
import logging

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from flask import current_app

from src.app.errors import AbdmUpstreamError
from src.app.extensions import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "abdm:public_key"
CERTIFICATE_PATH = "/abha/api/v3/profile/public/certificate"

_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None)


def load_public_key(key_text):
    """Accept PEM, or bare base64 DER (what ABDM's certificate API returns)."""
    key_text = key_text.strip()
    if "BEGIN" in key_text:
        return serialization.load_pem_public_key(key_text.encode())
    return serialization.load_der_public_key(base64.b64decode(key_text))


def _fetch_public_key_text():
    from src.app.abdm.http import abdm_request

    data = abdm_request("GET", CERTIFICATE_PATH, endpoint_name="profile/public/certificate")
    key_text = (data or {}).get("publicKey") if isinstance(data, dict) else None
    if not key_text:
        raise AbdmUpstreamError("ABDM public key response had no publicKey", code="ABDM_PUBLIC_KEY", status=502)
    return key_text


def get_public_key_text():
    configured = current_app.config.get("ABDM_PUBLIC_KEY")
    if configured:
        return configured
    key_text = cache.get(CACHE_KEY)
    if not key_text:
        key_text = _fetch_public_key_text()
        cache.set(CACHE_KEY, key_text, ttl=current_app.config["ABDM_PUBLIC_KEY_CACHE_SECONDS"])
        logger.info("Fetched ABDM public key")
    return key_text


def encrypt(value):
    """Encrypt a value for ABDM. Returns base64 text."""
    if value is None:
        return None
    public_key = load_public_key(get_public_key_text())
    cipher = public_key.encrypt(str(value).encode("utf-8"), _OAEP)
    return base64.b64encode(cipher).decode("ascii")
