"""ABHA profile, card and QR (M1 items #21-#25)."""
import base64

from src.app.abdm import profile as abdm
from src.app.errors import AbdmUpstreamError
from src.app.mappers.abha import sniff_content_type

# Keys under which ABDM may return a file as base64 JSON instead of raw bytes
_B64_KEYS = ("abhaCard", "card", "phrCard", "qrCode", "qr_code", "image")


def account_profile(x_token):
    return abdm.get_account_profile(x_token)


def phr_profile(x_token):
    result = abdm.get_phr_profile(x_token)
    if isinstance(result, dict) and isinstance(result.get("ABHAProfile"), dict):
        return result["ABHAProfile"]
    return result


def _file(response, what):
    """Return (bytes, content_type) from a card/QR response (raw bytes or base64 JSON)."""
    content_type = sniff_content_type(response)
    body = response.content or b""
    if content_type == "application/json":
        try:
            data = response.json() or {}
        except ValueError:
            data = {}
        encoded = next((data[k] for k in _B64_KEYS if isinstance(data.get(k), str)), None)
        if not encoded:
            raise AbdmUpstreamError(f"ABDM returned no {what}", code="ABDM_EMPTY_FILE", status=502)
        body = base64.b64decode(encoded.split(",", 1)[-1])
        content_type = "application/pdf" if body[:4] == b"%PDF" else "image/png"
    if not body:
        raise AbdmUpstreamError(f"ABDM returned an empty {what}", code="ABDM_EMPTY_FILE", status=502)
    return body, content_type


def abha_card(x_token):
    return _file(abdm.get_abha_card(x_token), "ABHA card")


def phr_card(x_token):
    return _file(abdm.get_phr_card(x_token), "PHR card")


def qr_code(x_token):
    return _file(abdm.get_qr_code(x_token), "QR code")
