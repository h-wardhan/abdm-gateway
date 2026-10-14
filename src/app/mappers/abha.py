"""
ABDM v3 responses → our `data` payloads.

Most ABDM responses are passed through unchanged inside the envelope (`data`), because that is the
shape client apps (i-medicus) already parse. Only enrolment needs reshaping: ABDM returns
`ABHAProfile.ABHANumber`, clients read `abhaProfile.abhaNumber`.
"""


def _camel_profile(profile):
    if not isinstance(profile, dict):
        return profile
    mapped = dict(profile)
    if "ABHANumber" in mapped:
        mapped.setdefault("abhaNumber", mapped["ABHANumber"])
    if "preferredAbhaAddress" in mapped:
        mapped.setdefault("preferredAddress", mapped["preferredAbhaAddress"])
    phr = mapped.get("phrAddress")
    if isinstance(phr, str):
        mapped["phrAddress"] = [p.strip() for p in phr.split(",") if p.strip()]
    return mapped


def enrolment_result(abdm):
    """enrol/byAadhaar → {txnId, message, isNew, tokens, abhaProfile}."""
    abdm = abdm or {}
    profile = abdm.get("ABHAProfile") or abdm.get("abhaProfile")
    return {
        "txnId": abdm.get("txnId"),
        "message": abdm.get("message"),
        "isNew": abdm.get("isNew", True),
        "tokens": abdm.get("tokens") or {},
        "abhaProfile": _camel_profile(profile),
    }


def sniff_content_type(response):
    """Content type of a card/QR download, falling back to magic bytes."""
    declared = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    body = response.content or b""
    if body[:4] == b"%PDF":
        return "application/pdf"
    if body[:4] == b"\x89PNG":
        return "image/png"
    if body[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return declared or "application/octet-stream"
