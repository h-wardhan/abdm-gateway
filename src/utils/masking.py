"""
Masking helpers so Aadhaar, OTP, mobile numbers, tokens and secrets never reach logs or the audit table.
"""
import re

# Keys whose values are hidden completely
_SECRET_KEYS = {
    "otp", "otpvalue", "password", "clientsecret", "client_secret", "client-secret",
    "secret", "authorization", "x-token", "xtoken", "t-token", "r-token", "token",
    "accesstoken", "refreshtoken", "refresh_token", "access_token", "logintoken",
    "loginid", "encryptionkey", "profilephoto", "photo", "preferredabhaaddresscard",
}
# Keys whose values keep only the last 4 characters
_PARTIAL_KEYS = {
    "aadhaar", "aadhaarnumber", "aadharnumber", "aadhar_number", "aadhaar_number",
    "mobile", "mobilenumber", "mobile_number", "abhanumber", "abha_number", "healthidnumber",
}

_AADHAAR_RE = re.compile(r"(?<!\d)(\d{8})(\d{4})(?!\d)")          # 12 digits
_MOBILE_RE = re.compile(r"(?<!\d)([6-9]\d{5})(\d{4})(?!\d)")       # 10-digit Indian mobile
_JWT_RE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
_BEARER_RE = re.compile(r"(Bearer\s+)[\w\-.~+/]+=*", re.IGNORECASE)


def _norm(key):
    return str(key).replace(" ", "").lower()


def mask_tail(value, keep=4):
    text = str(value)
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


def mask_text(text):
    """Mask sensitive patterns inside free text (log messages, raw upstream bodies)."""
    if not text:
        return text
    text = str(text)
    text = _BEARER_RE.sub(r"\1***", text)
    text = _JWT_RE.sub("***jwt***", text)
    text = _AADHAAR_RE.sub(lambda m: "********" + m.group(2), text)
    text = _MOBILE_RE.sub(lambda m: "******" + m.group(2), text)
    return text


def mask_payload(data):
    """Return a deep copy of dict/list data with sensitive fields masked."""
    if isinstance(data, dict):
        masked = {}
        for key, value in data.items():
            norm = _norm(key)
            if norm in _SECRET_KEYS:
                masked[key] = "***" if value not in (None, "") else value
            elif norm in _PARTIAL_KEYS and isinstance(value, (str, int)):
                masked[key] = mask_tail(value)
            else:
                masked[key] = mask_payload(value)
        return masked
    if isinstance(data, list):
        return [mask_payload(item) for item in data]
    if isinstance(data, str):
        return mask_text(data)
    return data


def mask_headers(headers):
    return {k: ("***" if _norm(k) in _SECRET_KEYS else v) for k, v in dict(headers).items()}
