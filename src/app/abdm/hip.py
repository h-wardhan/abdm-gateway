"""
ABDM v3 M2 (HIP) calls: linking, link notifications, consent acknowledgement, health-information acknowledgement.
Paths, headers and bodies follow ABDM's Milestone 2 Postman collection.

Two kinds of call:
- answers to a callback ABDM sent us (on-discover, on-init, on-confirm, consent on-notify, health-information
  on-request): safe to repeat, so they go through `send_or_queue` (queued for the worker when ABDM is down);
- calls we start (generate-token, link care context, link notify, SMS notify): sent once, errors are returned to the
  caller, which decides whether to try again. Never queued, so a patient is not notified twice.
"""
from src.app.abdm.http import abdm_request
from src.app.outbox.abdm_calls import send_or_queue

ON_DISCOVER = "/api/hiecm/user-initiated-linking/v3/patient/care-context/on-discover"
ON_INIT = "/api/hiecm/user-initiated-linking/v3/link/care-context/on-init"
ON_CONFIRM = "/api/hiecm/user-initiated-linking/v3/link/care-context/on-confirm"
CONSENT_ON_NOTIFY = "/api/hiecm/consent/v3/request/hip/on-notify"
HI_ON_REQUEST = "/api/hiecm/data-flow/v3/health-information/hip/on-request"

GENERATE_TOKEN = "/api/hiecm/v3/token/generate-token"
LINK_CARE_CONTEXT = "/api/hiecm/hip/v3/link/carecontext"
LINK_NOTIFY = "/api/hiecm/hip/v3/link/context/notify"
SMS_NOTIFY = "/api/hiecm/hip/v3/link/patient/links/sms/notify2"


def reply(path, body, hip_id, api_client_id, name):
    """Answer a callback. Returns {"status": "sent" | "queued", ...}."""
    return send_or_queue(path, body, headers={"X-HIP-ID": hip_id}, endpoint_name=name, api_client_id=api_client_id)


def generate_token(hip_id, body):
    return abdm_request("POST", GENERATE_TOKEN, target="gateway", json=body, headers={"X-HIP-ID": hip_id},
                        endpoint_name="hip/generate-token")


def link_care_context(hip_id, link_token, body):
    return abdm_request("POST", LINK_CARE_CONTEXT, target="gateway", json=body,
                        headers={"X-HIP-ID": hip_id, "X-LINK-TOKEN": link_token}, endpoint_name="hip/link/carecontext")


def link_notify(hip_id, link_token, body):
    return abdm_request("POST", LINK_NOTIFY, target="gateway", json=body,
                        headers={"X-HIP-ID": hip_id, "X-LINK-TOKEN": link_token}, endpoint_name="hip/link/context/notify")


def sms_notify(body):
    return abdm_request("POST", SMS_NOTIFY, target="gateway", json=body, endpoint_name="hip/link/sms/notify")
