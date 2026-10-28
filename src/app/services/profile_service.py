"""ABHA profile, card and QR (M1 items #21-#25)."""
import base64

from src.app.abdm import profile as abdm
from src.app.abdm.crypto import encrypt
from src.app.common import validators as v
from src.app.errors import AbdmUpstreamError, ValidationError
from src.app.mappers.abha import sniff_content_type
from src.app.services import enrolment_service, txn_service

FLOW = "profile_update"

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


def send_profile_otp(kind, data):
    """OTP to a new mobile / an email of an existing ABHA. `kind` is "mobile" or "email"."""
    token = v.x_token(data)
    if kind == "mobile":
        value = v.mobile(v.pick(data, "mobilenumber", "mobileNumber", "mobile"))
    else:
        value = v.email(v.pick(data, "email"))
    result = abdm.request_profile_otp(token, kind, encrypt(value))
    txn_service.record_from(result, FLOW, f"{kind}_otp_sent")
    return {"txnId": result.get("txnId"), "message": result.get("message")}


def verify_profile_otp(kind, data):
    token = v.x_token(data)
    txn_id = v.txn_id(v.pick(data, "txnId"))
    otp = v.otp(v.pick(data, "otp", "otpValue", "OTP"))
    txn_service.assert_owner(txn_id)
    result = abdm.verify_profile_otp(token, kind, txn_id, encrypt(otp))
    txn_service.record_from(result, FLOW, f"{kind}_verified")
    return result


# --- The calls i-medicus makes (its request bodies are kept as they were) ----------------------------------------
# One endpoint for "mobile OTP" / "verify mobile": the ABHA user token means an existing ABHA (profile update),
# a txnId without a token means an ABHA being created (enrolment).

def _user_token_or_none(data):
    try:
        return v.x_token(data)
    except ValidationError:
        return None


def _otp_checked(result):
    """A wrong OTP is an error (HTTP 400) here, so the caller does not treat it as success."""
    if isinstance(result, dict) and str(result.get("authResult", "")).lower() == "failed":
        raise ValidationError(result.get("message") or "Invalid OTP", code="INVALID_OTP", details={"field": "otp"})
    return result


def mobile_otp(data):
    if _user_token_or_none(data):
        return send_profile_otp("mobile", data)
    if v.pick(data, "txnId"):
        return enrolment_service.send_mobile_update_otp(data)
    v.x_token(data)  # neither: asks for the ABHA user token


def mobile_verify(data):
    if _user_token_or_none(data):
        return _otp_checked(verify_profile_otp("mobile", data))
    if v.pick(data, "txnId"):
        return _otp_checked(enrolment_service.verify_mobile_update_otp(data))
    v.x_token(data)


def email_verify(data):
    return _otp_checked(verify_profile_otp("email", data))
