"""
Work out where a callback from ABDM belongs.

- `normalize_path()` turns the URL ABDM called into the path forwarded to the client app
  ("api/v3/hip/patient/share"), refusing anything odd.
- `resolve_hip_id()` finds the facility the callback is about: the X-HIP-ID header first, then the
  places ABDM puts it in the body (patient share, consent notify, ...).
"""
import re

from src.app.errors import ValidationError

_SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.\-]{0,200}$")

# Where ABDM puts the HIP id in callback bodies, most specific first
HIP_BODY_PATHS = (
    ("metaData", "hipId"),
    ("hip", "id"),
    ("hip", "hipId"),
    ("hipId",),
    ("notification", "consentDetail", "hip", "id"),
    ("consentDetail", "hip", "id"),
)


def normalize_path(subpath):
    """`v3/hip/patient/share` (the part after /api/ or /) -> `api/v3/hip/patient/share`."""
    subpath = (subpath or "").strip("/")
    segments = subpath.split("/")
    if not _SAFE_PATH.match(subpath) or any(seg in ("", ".", "..") for seg in segments):
        raise ValidationError("Invalid callback path", code="INVALID_CALLBACK_PATH")
    return f"api/{subpath}"


def _dig(data, keys):
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data if isinstance(data, str) and data.strip() else None


def resolve_hip_id(headers, body):
    header = (headers.get("X-HIP-ID") or "").strip()
    if header:
        return header[:64]
    for keys in HIP_BODY_PATHS:
        value = _dig(body, keys)
        if value:
            return value.strip()[:64]
    return None
