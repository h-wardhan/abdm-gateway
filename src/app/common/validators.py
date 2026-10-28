"""
Request input helpers + validation, run before anything is sent to ABDM.

Clients are not consistent about key names (i-medicus sends otp / otpValue / OTP, scope / scop,
mobileNumber / mobilenumber), so `pick()` accepts aliases.
"""
import re

from flask import g, request

from src.app.errors import ValidationError
from src.utils.aadhaar_validator import normalize_aadhaar_digits, validate_aadhaar_number

_OTP_RE = re.compile(r"^\d{6}$")
_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
_ABHA_NUMBER_RE = re.compile(r"^\d{14}$")
_ABHA_ADDRESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._]{2,49}(@[A-Za-z]+)?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TXN_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def body():
    data = request.get_json(silent=True)
    if data is None and request.form:
        data = request.form.to_dict()
    return data if isinstance(data, dict) else {}


def pick(data, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value.strip() if isinstance(value, str) else value
    return None


def require(value, field):
    if value in (None, ""):
        raise ValidationError(f"{field} is required", details={"field": field})
    return value


def aadhaar(value):
    digits = normalize_aadhaar_digits(require(value, "aadhaarNumber"))
    if not validate_aadhaar_number(digits):
        raise ValidationError("Invalid Aadhaar number. Please check the number and try again.",
                              code="INVALID_AADHAAR", details={"field": "aadhaarNumber"})
    return digits


def otp(value):
    value = str(require(value, "otp")).strip()
    if not _OTP_RE.match(value):
        raise ValidationError("OTP must be 6 digits", code="INVALID_OTP", details={"field": "otp"})
    return value


def mobile(value):
    digits = re.sub(r"\D", "", str(require(value, "mobileNumber")))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if not _MOBILE_RE.match(digits):
        raise ValidationError("Mobile number must be a valid 10-digit Indian number",
                              code="INVALID_MOBILE", details={"field": "mobileNumber"})
    return digits


def abha_number(value):
    """Accept 14 digits with or without hyphens; return ABDM's NN-NNNN-NNNN-NNNN format."""
    digits = re.sub(r"\D", "", str(require(value, "abhaNumber")))
    if not _ABHA_NUMBER_RE.match(digits):
        raise ValidationError("ABHA number must be 14 digits", code="INVALID_ABHA_NUMBER",
                              details={"field": "abhaNumber"})
    return f"{digits[:2]}-{digits[2:6]}-{digits[6:10]}-{digits[10:]}"


def abha_address(value):
    value = str(require(value, "abhaAddress")).strip()
    if not _ABHA_ADDRESS_RE.match(value):
        raise ValidationError("ABHA address is not valid", code="INVALID_ABHA_ADDRESS",
                              details={"field": "abhaAddress"})
    return value


def email(value):
    value = str(require(value, "email")).strip()
    if not _EMAIL_RE.match(value):
        raise ValidationError("Email address is not valid", code="INVALID_EMAIL", details={"field": "email"})
    return value


def txn_id(value):
    value = str(require(value, "txnId")).strip()
    if not _TXN_RE.match(value):
        raise ValidationError("txnId is not valid", code="INVALID_TXN_ID", details={"field": "txnId"})
    return value


def x_token(data=None):
    """ABHA user token: `xToken` query/body param or `X-Token` header, with or without 'Bearer '."""
    token = (
        request.args.get("xToken")
        or request.headers.get("X-Token")
        or (pick(data or {}, "xToken", "x_token") if data else None)
        or getattr(g, "user_token", None)  # the ABHA token sent in Authorization (tenant_or_credentials_required)
    )
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise ValidationError("ABHA user token is required (xToken query param or X-Token header)",
                              code="MISSING_X_TOKEN", details={"field": "xToken"})
    return token
