"""
ABDM v3 M3 (HIU) calls: consent request, consent notification acknowledgement, consent fetch, health-information
request. Paths, headers and bodies follow ABDM's Milestone 3 Postman collection.

A facility acts as HIU with the same id it has as HIP; ABDM wants it in `X-HIU-ID`.
- consent init and the health-information request create something at ABDM: sent once, errors go back to the
  caller, never queued (a retry could create a second consent request / transaction);
- the answer to a consent notification and the consent fetch are safe to repeat: queued for the worker when ABDM
  is down.
"""
from src.app.abdm.http import abdm_request
from src.app.outbox.abdm_calls import send_or_queue

CONSENT_INIT = "/api/hiecm/consent/v3/request/init"
CONSENT_STATUS = "/api/hiecm/consent/v3/request/status"
CONSENT_ON_NOTIFY = "/api/hiecm/consent/v3/request/hiu/on-notify"
CONSENT_FETCH = "/api/hiecm/consent/v3/fetch"
HI_REQUEST = "/api/hiecm/data-flow/v3/health-information/request"


def consent_init(hiu_id, request_id, body):
    return abdm_request("POST", CONSENT_INIT, target="gateway", json=body,
                        headers={"X-HIU-ID": hiu_id, "REQUEST-ID": request_id}, endpoint_name="hiu/consent/init")


def consent_status(hiu_id, body):
    return abdm_request("POST", CONSENT_STATUS, target="gateway", json=body, headers={"X-HIU-ID": hiu_id},
                        endpoint_name="hiu/consent/status")


def consent_on_notify(hiu_id, body, api_client_id):
    return send_or_queue(CONSENT_ON_NOTIFY, body, headers={"X-HIU-ID": hiu_id},
                         endpoint_name="hiu/consent/on-notify", api_client_id=api_client_id)


def consent_fetch(hiu_id, body, api_client_id):
    return send_or_queue(CONSENT_FETCH, body, headers={"X-HIU-ID": hiu_id}, endpoint_name="hiu/consent/fetch",
                         api_client_id=api_client_id)


def health_information_request(hiu_id, request_id, body):
    return abdm_request("POST", HI_REQUEST, target="gateway", json=body,
                        headers={"X-HIU-ID": hiu_id, "REQUEST-ID": request_id},
                        endpoint_name="hiu/health-information/request")
