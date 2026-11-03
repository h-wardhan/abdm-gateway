"""
M2 (HIP) linking, consent acknowledgement and health-information acknowledgement (WBS Days 9-11).

The client app (i-medicus) keeps its business logic (matching the patient, sending the OTP, preparing records) and
keeps sending the bodies it always sent; this module turns them into ABDM v3 bodies and does the ABDM protocol:
the facility headers, the link token, finding which callback an answer belongs to, sending it once, retrying it when
ABDM is down, and (Day 11) checking consent before a health-information request is acknowledged.

An answer to a callback is matched to the callback by its REQUEST-ID (`response.requestId`). The facility is the one
the callback arrived for, and a client can only answer callbacks of its own facilities.
"""
import uuid
from datetime import datetime, timezone

from flask import request

from src.app.abdm import hip as abdm
from src.app.callbacks import registry
from src.app.common import validators as v
from src.app.errors import NotFoundError, ValidationError
from src.app.extensions import db
from src.app.models import CallbackEvent, HipReply
from src.app.models import hip_state
from src.app.services import hip_consent_service


# ---- helpers ------------------------------------------------------------------------------------------------

def _request_id(data):
    inner = data.get("response") if isinstance(data.get("response"), dict) else {}
    value = inner.get("requestId") or v.pick(data, "requestId", "request_id")
    return str(v.require(value, "requestId")).strip()[:64]


def _callback(client, request_id):
    """The stored callback an answer belongs to (only callbacks of this client's facilities)."""
    event = CallbackEvent.query.filter_by(request_id=request_id, api_client_id=client.id).first()
    if event is None or not event.hip_id:
        raise NotFoundError("No callback with this requestId for your facilities", code="CALLBACK_NOT_FOUND")
    return event


def _answer(client, event, kind, path, body, name):
    """Send an answer once. A second answer to the same callback is a duplicate and is not sent."""
    request_id = event.request_id
    if HipReply.query.filter_by(request_id=request_id, kind=kind).first():
        return {"status": "already_sent", "hipId": event.hip_id}
    result = abdm.reply(path, body, event.hip_id, client.id, name)
    db.session.add(HipReply(api_client_id=client.id, request_id=request_id, kind=kind,
                            status=hip_state.REPLY_QUEUED if result["status"] == "queued" else hip_state.REPLY_SENT))
    db.session.commit()
    out = {"status": result["status"], "hipId": event.hip_id}
    if result["status"] == "queued":
        out["jobId"] = result["jobId"]
    return out


def _patients(data):
    """patient[] as ABDM wants it: referenceNumber, display, careContexts[], hiType, count."""
    items = data.get("patient") or data.get("Patient")
    if not isinstance(items, list) or not items:
        raise ValidationError("patient must be a non-empty list", details={"field": "patient"})
    patients = []
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("patient entries must be objects", details={"field": "patient"})
        contexts = item.get("careContexts")
        if not isinstance(contexts, list) or not contexts:
            raise ValidationError("every patient needs careContexts", details={"field": "careContexts"})
        for ctx in contexts:
            v.require(ctx.get("referenceNumber") if isinstance(ctx, dict) else None, "careContexts.referenceNumber")
        patients.append({
            "referenceNumber": str(v.require(item.get("referenceNumber"), "referenceNumber")),
            "display": str(item.get("display") or item["referenceNumber"]),
            "careContexts": [{"referenceNumber": str(c["referenceNumber"]),
                              "display": str(c.get("display") or c["referenceNumber"])} for c in contexts],
            "hiType": v.require(item.get("hiType"), "hiType"),
            "count": int(item.get("count") or len(contexts)),
        })
    return patients


def _hip_id(client, data):
    """Facility of a call we start: `hipid` / `hipId` in the body or the X-HIP-ID header; must be the client's own."""
    hip_id = v.pick(data, "hipid", "hipId", "hip_id") or request.headers.get("X-HIP-ID")
    return registry.get_for_client(client, registry.validate_hip_id(hip_id)).hip_id


def _link_token(data):
    token = request.headers.get("X-Link-Token") or v.pick(data, "linkToken", "link_token")
    if not token or not str(token).strip():
        raise ValidationError("link token is required (X-Link-Token header or linkToken)", code="MISSING_LINK_TOKEN",
                              details={"field": "linkToken"})
    return str(token).strip()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---- user-initiated linking: answers to ABDM's discover / init / confirm callbacks ---------------------------------

def on_discover(client, data):
    request_id = _request_id(data)
    event = _callback(client, request_id)
    body = {
        "transactionId": str(v.require(v.pick(data, "transactionId"), "transactionId")),
        "patient": _patients(data),
        "matchedBy": data.get("matchedBy") or ["MR"],
        "response": {"requestId": request_id},
    }
    return _answer(client, event, "on_discover", abdm.ON_DISCOVER, body, "hip/on-discover")


def on_init(client, data):
    request_id = _request_id(data)
    event = _callback(client, request_id)
    link = data.get("link")
    if not isinstance(link, dict):
        raise ValidationError("link is required", details={"field": "link"})
    v.require(link.get("referenceNumber"), "link.referenceNumber")
    v.require(link.get("authenticationType"), "link.authenticationType")
    body = {
        "transactionId": str(v.require(v.pick(data, "transactionId"), "transactionId")),
        "link": link,
        "response": {"requestId": request_id},
    }
    return _answer(client, event, "on_init", abdm.ON_INIT, body, "hip/on-init")


def on_confirm(client, data):
    request_id = _request_id(data)
    event = _callback(client, request_id)
    body = {"patient": _patients(data), "response": {"requestId": request_id}}
    return _answer(client, event, "on_confirm", abdm.ON_CONFIRM, body, "hip/on-confirm")


# ---- HIP-initiated linking: calls we start ------------------------------------------------------------------------

def generate_token(client, data):
    hip_id = _hip_id(client, data)
    number = v.abha_number(v.pick(data, "abhaNumber", "ABHANumber")).replace("-", "")
    body = {
        "abhaNumber": int(number),  # a JSON number in ABDM's collection
        "abhaAddress": v.abha_address(v.pick(data, "abhaAddress")),
        "name": str(v.require(v.pick(data, "name"), "name")),
        "gender": str(v.require(v.pick(data, "gender"), "gender")),
        "yearOfBirth": int(v.require(v.pick(data, "yearOfBirth"), "yearOfBirth")),
    }
    abdm.generate_token(hip_id, body)
    return {"hipId": hip_id}


def link_care_context(client, data):
    """Single or several patients in one call; i-medicus sends them under `singlecarecontext`."""
    hip_id = _hip_id(client, data)
    token = _link_token(data)
    source = data.get("singlecarecontext") or data.get("carecontext") or data
    body = {
        "abhaNumber": v.abha_number(v.pick(source, "abhaNumber", "ABHANumber")).replace("-", ""),
        "abhaAddress": v.abha_address(v.pick(source, "abhaAddress")),
        "patient": _patients(source),
    }
    abdm.link_care_context(hip_id, token, body)
    return {"hipId": hip_id}


def link_notify(client, data):
    hip_id = _hip_id(client, data)
    token = _link_token(data)
    types = v.pick(data, "hiType", "hiTypes")
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or not types:
        raise ValidationError("hiType is required", details={"field": "hiType"})
    address = v.abha_address(v.pick(data, "abhaAddress"))
    body = {"notification": {
        "patient": {"id": address},
        "careContext": {"patientReference": str(v.require(v.pick(data, "patientReference"), "patientReference")),
                        "careContextReference": str(v.require(v.pick(data, "careContextReference"),
                                                              "careContextReference"))},
        "hiTypes": types,
        "date": v.pick(data, "date") or _now(),
        "hip": {"id": hip_id},
    }}
    abdm.link_notify(hip_id, token, body)
    return {"hipId": hip_id}


def sms_notify(client, data):
    """SMS to a patient who has no ABHA yet, asking them to link records.

    The clinic name is the one registered for the facility: the `name` i-medicus sends is the patient's."""
    hip_id = _hip_id(client, data)
    reg = registry.get_for_client(client, hip_id)
    body = {
        "requestId": str(uuid.uuid4()),
        "timestamp": _now(),
        "notification": {
            "phoneNo": v.mobile(v.pick(data, "phoneNumber", "phoneNo", "mobile")),
            "hip": {"name": str(reg.name or hip_id), "id": hip_id},
        },
    }
    abdm.sms_notify(body)
    return {"hipId": hip_id}


# ---- consent and health-information requests -----------------------------------------------------------------------

def consent_on_notify(client, data):
    """Acknowledge a consent notification. Accepts the i-medicus body {consentId, requestId, hipId} and ABDM's own."""
    request_id = _request_id(data)
    event = _callback(client, request_id)
    ack = data.get("acknowledgement") if isinstance(data.get("acknowledgement"), dict) else {}
    consent_id = str(v.require(ack.get("consentId") or v.pick(data, "consentId"), "consentId"))
    status = str(ack.get("status") or "OK").upper()
    body = {"acknowledgement": {"status": status, "consentId": consent_id}, "response": {"requestId": request_id}}
    return _answer(client, event, "consent_on_notify", abdm.CONSENT_ON_NOTIFY, body, "hip/consent/on-notify")


def hi_on_request(client, data):
    """Acknowledge a health-information request, after checking its consent (revoked / expired / out of range)."""
    request_id = _request_id(data)
    event = _callback(client, request_id)
    original = hip_consent_service.request_of(event)
    hi_request = original.get("hiRequest") or {}
    transaction_id = v.pick(data, "transactionId") or v.pick(original, "transactionId")
    transaction_id = str(v.require(transaction_id, "transactionId"))
    consent_id = (hi_request.get("consent") or {}).get("id")
    hip_consent_service.assert_usable(consent_id, hi_request, client.id)
    body = {"hiRequest": {"transactionId": transaction_id, "sessionStatus": "ACKNOWLEDGED"},
            "response": {"requestId": request_id}}
    return _answer(client, event, "hi_on_request", abdm.HI_ON_REQUEST, body, "hip/health-information/on-request")
