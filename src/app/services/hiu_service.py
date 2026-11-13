"""
M3 (HIU) calls for a facility that requests records from other facilities (i-medicus is both HIP and HIU).

Same rule as the HIP side: i-medicus keeps sending the bodies it sent VCDoctor; this module turns them into ABDM v3
bodies and does the protocol (facility header, REQUEST-ID, matching an answer to the callback it answers, sending once).

    consent init      -> ABDM `consent/v3/request/init`        (REQUEST-ID = the requestId i-medicus keeps, so the
                                                                 `on-init` callback can be matched to its row)
    consent on-notify -> answer to `hiu/consent/request/notify`
    consent fetch     -> ABDM `consent/v3/fetch`
    HI request        -> ABDM `data-flow/v3/health-information/request` (with the HIU's key material and the push URL)
"""
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import request

from src.app.abdm import fidelius
from src.app.abdm import hiu as abdm
from src.app.callbacks import registry
from src.app.common import validators as v
from src.app.errors import ValidationError
from src.app.models import HiuTransfer
from src.app.services import hip_consent_service, hip_service, hiu_consent_service

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{7,63}$")
_PURPOSES = {"CAREMGT": "Care Management", "BTG": "Break the Glass", "PUBHLTH": "Public Health",
             "HPAYMT": "Healthcare Payment", "DSRCH": "Disease Specific Healthcare Research",
             "PATRQT": "Self Requested"}
_REQUESTER_SYSTEMS = {"REGN": "https://www.mciindia.org"}  # ABDM's example for a doctor's registration number


def _facility(client, data):
    """The facility acting as HIU: `hipId` / `hiuId` in the body or the X-HIU-ID header; must be the client's own."""
    hiu_id = v.pick(data, "hiuId", "hipId", "hipid", "hip_id") or request.headers.get("X-HIU-ID") \
        or request.headers.get("X-HIP-ID")
    return registry.for_call(client, registry.validate_hip_id(hiu_id)).hip_id


def _request_id(data):
    value = v.pick(data, "requestId", "request_id")
    if value is None:
        return str(uuid.uuid4())
    value = str(value).strip()
    if not _REQUEST_ID_RE.match(value):
        raise ValidationError("requestId must be 8-64 letters, digits or '-'", details={"field": "requestId"})
    return value


def _time(value, field, future=False):
    """ABDM timestamp ("2026-11-20T10:00:00.000Z", UTC) from the shapes i-medicus sends."""
    parsed = hip_consent_service.parse_time(value if isinstance(value, str) else None)
    if parsed is None:
        raise ValidationError(f"{field} is required and must be a date-time", details={"field": field})
    if future and parsed <= datetime.now(timezone.utc):
        raise ValidationError(f"{field} must be in the future", details={"field": field})
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _hi_types(data):
    types = data.get("hiType") if data.get("hiType") is not None else data.get("hiTypes")
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or not types or not all(isinstance(t, str) and t.strip() for t in types):
        raise ValidationError("hiType must be a non-empty list", details={"field": "hiType"})
    return [t.strip() for t in types]


def _purpose(value):
    value = str(v.require(value, "purposeOfRequest")).strip()
    if value.upper() in _PURPOSES:
        return {"code": value.upper(), "text": _PURPOSES[value.upper()]}
    for code, text in _PURPOSES.items():
        if value.lower() == text.lower():
            return {"code": code, "text": text}
    raise ValidationError(f"purposeOfRequest must be one of {sorted(_PURPOSES)}", details={"field": "purposeOfRequest"})


# ---- consent --------------------------------------------------------------------------------------------------------

def consent_init(client, data):
    hiu_id = _facility(client, data)
    request_id = _request_id(data)
    purpose = _purpose(v.pick(data, "purposeOfRequest", "purpose"))
    requester_type = str(v.pick(data, "requesterType") or "REGN")
    identifier = {"type": requester_type, "value": str(v.require(v.pick(data, "requesterValue"), "requesterValue"))}
    system = v.pick(data, "requesterSystem") or _REQUESTER_SYSTEMS.get(requester_type)
    if system:
        identifier["system"] = system
    start, end = _time(v.pick(data, "fromDate"), "fromDate"), _time(v.pick(data, "toDate"), "toDate")
    if start > end:
        raise ValidationError("fromDate must not be after toDate", details={"field": "fromDate"})
    body = {"consent": {
        "purpose": {**purpose, "refUri": v.pick(data, "refUri") or "www.abdm.gov.in"},
        "patient": {"id": v.abha_address(v.pick(data, "patientId", "abhaAddress"))},
        "hiu": {"id": hiu_id},
        "hip": None,
        "careContexts": None,
        "requester": {"name": str(v.require(v.pick(data, "requesterName"), "requesterName")), "identifier": identifier},
        "hiTypes": _hi_types(data),
        "permission": {"accessMode": "VIEW", "dateRange": {"from": start, "to": end},
                       "dataEraseAt": _time(v.pick(data, "dataEraseAt"), "dataEraseAt", future=True),
                       "frequency": {"unit": "HOUR", "value": 0, "repeats": 0}},
    }}
    abdm.consent_init(hiu_id, request_id, body)
    return {"hipId": hiu_id, "requestId": request_id, "status": "requested"}


def consent_on_notify(client, data):
    """Acknowledge ABDM's consent notification (callback `hiu/consent/request/notify`). Accepts i-medicus' body
    `{acknowledgement: [{status, consentId}], response: {requestId}, hipId}`."""
    request_id = hip_service._request_id(data)
    event = hip_service._callback(client, request_id)
    raw = data.get("acknowledgement")
    raw = [raw] if isinstance(raw, dict) else raw
    if raw is None and v.pick(data, "consentId"):
        raw = [{"consentId": v.pick(data, "consentId"), "status": v.pick(data, "status")}]
    if not isinstance(raw, list) or not raw:
        raise ValidationError("acknowledgement must list the consents acknowledged", details={"field": "acknowledgement"})
    acks = [{"status": str(a.get("status") or "OK").upper(),
             "consentId": str(v.require(a.get("consentId"), "acknowledgement.consentId"))}
            for a in raw if isinstance(a, dict)]
    body = {"acknowledgement": acks, "response": {"requestId": request_id}}
    return hip_service._answer(client, event, "hiu_consent_on_notify", abdm.CONSENT_ON_NOTIFY, body,
                               "hiu/consent/on-notify", header="X-HIU-ID")


def consent_fetch(client, data):
    hiu_id = _facility(client, data)
    consent_id = str(v.require(v.pick(data, "consentId"), "consentId"))
    result = abdm.consent_fetch(hiu_id, {"consentId": consent_id}, client.id)
    out = {"hipId": hiu_id, "status": result["status"]}
    if result["status"] == "queued":
        out["jobId"] = result["jobId"]
    return out


def consent_status(client, data):
    """Ask ABDM where a consent request stands (answer arrives as `hiu/consent/request/on-status`). Body:
    `{consentRequestId, hipId}`: the id is the one ABDM's `on-init` callback returned for our request."""
    hiu_id = _facility(client, data)
    request_id = str(v.require(v.pick(data, "consentRequestId", "consentRequestID"), "consentRequestId"))
    result = abdm.consent_status(hiu_id, {"consentRequestId": request_id}, client.id)
    out = {"hipId": hiu_id, "status": result["status"]}
    if result["status"] == "queued":
        out["jobId"] = result["jobId"]
    return out


# ---- health-information request -------------------------------------------------------------------------------------

def _push_url(value):
    url = str(v.require(value, "dataPushUrl")).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValidationError("dataPushUrl must be an http(s) URL without credentials", details={"field": "dataPushUrl"})
    return url


def health_information_request(client, data):
    """Ask ABDM to have the HIPs of a consent send us the records, encrypted for our key. i-medicus sends
    `{consentId, fromDate, toDate, expiryDate, dataPushUrl, publicKey, nonce, hipId, requestId}`."""
    hiu_id = _facility(client, data)
    request_id = _request_id(data)
    key_material = {"cryptoAlg": "ECDH", "curve": "Curve25519",
                    "dhPublicKey": {"expiry": _time(v.pick(data, "expiryDate", "expiry"), "expiryDate", future=True),
                                    "parameters": "Curve25519/32byte random key",
                                    "keyValue": str(v.require(v.pick(data, "publicKey"), "publicKey"))},
                    "nonce": str(v.require(v.pick(data, "nonce"), "nonce"))}
    try:
        fidelius.read_key_material_block(key_material)
    except fidelius.FideliusError as exc:
        raise ValidationError(f"Invalid key material: {exc}", code="INVALID_KEY_MATERIAL")
    start, end = _time(v.pick(data, "fromDate"), "fromDate"), _time(v.pick(data, "toDate"), "toDate")
    consent_id = str(v.require(v.pick(data, "consentId"), "consentId"))
    body = {"hiRequest": {"consent": {"id": consent_id},
                          "dateRange": {"from": start, "to": end},
                          "dataPushUrl": _push_url(v.pick(data, "dataPushUrl")),
                          "keyMaterial": key_material}}
    # a denied, revoked, expired or out-of-range consent is stopped here, not sent to ABDM
    hiu_consent_service.assert_usable(client.id, consent_id, start, end)
    abdm.health_information_request(hiu_id, request_id, body)
    if HiuTransfer.query.filter_by(request_id=request_id).first() is None:
        hiu_consent_service.note_request(client.id, hiu_id, request_id, consent_id)
    return {"hipId": hiu_id, "requestId": request_id, "status": "requested"}
