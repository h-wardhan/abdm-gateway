"""
Scan & Share answer for the client app: find the scan it is about and tell ABDM the token.

The facility is not sent by the client: it is the one the scan callback arrived for (stored when the callback was
received, matched by ABDM's REQUEST-ID). That is also what keeps clinics apart: a client can only answer scans of
its own facilities.
"""
import json

from src.app.abdm import scan_share
from src.app.callbacks import vault
from src.app.common.validators import abha_address, pick, require
from src.app.errors import NotFoundError, ValidationError
from src.app.models import CallbackEvent

SHARE_PATH = "api/v3/hip/patient/share"


def _token_number(value):
    value = str(require(value, "tokenNumber")).strip()
    if not value.isdigit() or len(value) > 10:
        raise ValidationError("tokenNumber must be a number", code="INVALID_TOKEN_NUMBER",
                              details={"field": "tokenNumber"})
    return value


def _expiry(value):
    if value is None:
        return scan_share.DEFAULT_EXPIRY
    value = str(value).strip()
    if not value.isdigit() or not 0 < int(value) <= 86400:
        raise ValidationError("expiry must be a number of seconds (1 to 86400)", code="INVALID_EXPIRY",
                              details={"field": "expiry"})
    return value


def _context(event):
    """metaData.context of the scan callback, which ABDM wants back in the answer."""
    try:
        context = json.loads(vault.open_text(event.body_enc))["metaData"]["context"]
    except (ValueError, KeyError, TypeError):
        return None
    return context if isinstance(context, str) and context.strip() else None


def respond(client, data):
    """Answer a scan. Returns (result of send_or_queue, hip_id)."""
    request_id = str(require(pick(data, "requestId", "request_id"), "requestId")).strip()
    address = abha_address(pick(data, "abhaAddress", "abha_address"))
    token = _token_number(pick(data, "tokenNumber", "token_number", "token"))
    expiry = _expiry(pick(data, "expiry"))

    event = CallbackEvent.query.filter_by(request_id=request_id, path=SHARE_PATH, api_client_id=client.id).first()
    if event is None:
        raise NotFoundError("No Scan & Share request with this requestId for your facilities",
                            code="SHARE_REQUEST_NOT_FOUND")
    body = scan_share.build_on_share_body(address, token, request_id, _context(event), expiry)
    return scan_share.send_on_share(event.hip_id, body, client.id), event.hip_id
