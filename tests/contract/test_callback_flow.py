"""
The whole callback path, as it will run on the server:
ABDM (signs a JWT) -> gateway receiver -> stored + queued -> worker -> client app (i-medicus style webhook).

Nothing real is called: the client app is a mocked HTTP endpoint that reads the URL the way i-medicus does
(the part after /api/abdm-webhook/ picks the handler).
"""
import json
from datetime import timedelta

import pytest
import responses

from src.app import create_app
from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models.base import utcnow
from src.app.outbox import worker
from tests.conftest import CALLBACK_URL, HIP_ID, make_jwt

# What ABDM sends when a patient scans the facility QR (shape taken from i-medicus handlePatientShare)
PATIENT_SHARE = (
    '{"requestId":"5f7a535d-a3fd-416b-b069-e6a0fc9b6f9d","timestamp":"2026-10-22T10:30:00.000Z",'
    '"metaData":{"hipId":"IN0110005723","context":"hip","hprId":"","latitude":"28.6","longitude":"77.2"},'
    '"profile":{"patient":{"abhaNumber":"91-1234-5678-9012","abhaAddress":"ravi@sbx","name":"Ravi Kumar",'
    '"gender":"M","yearOfBirth":1990}}}'
)
SHARE_PATH = "/api/v3/hip/patient/share"


def _abdm_calls_gateway(client, body=PATIENT_SHARE, path=SHARE_PATH, request_id="a1b2c3d4-0001", **headers):
    return client.post(path, data=body, headers={
        "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "X-CM-ID": "sbx",
        "TIMESTAMP": "2026-10-22T10:30:00.000Z", "Content-Type": "application/json", **headers})


def _imedicus_receives(http, status=200, path="api/v3/hip/patient/share"):
    http.add(responses.POST, f"{CALLBACK_URL}/{path}", json={"status": "success"}, status=status)


def test_patient_share_reaches_imedicus_unchanged(client, jwks, http, hip):
    _imedicus_receives(http)
    assert _abdm_calls_gateway(client).status_code == 202
    assert worker.run(once=True) == 1

    [call] = [c for c in http.calls if c.request.method == "POST" and "client.example.test" in c.request.url]
    # i-medicus: urlKey = everything after /api/abdm-webhook/  ->  picks handlePatientShare
    assert call.request.url.split("/api/abdm-webhook/")[1] == "api/v3/hip/patient/share"
    assert call.request.body == PATIENT_SHARE.encode()  # byte for byte
    assert call.request.headers["request-id"] == "a1b2c3d4-0001"  # i-medicus reads $request->header('request-id')
    assert json.loads(call.request.body)["metaData"]["hipId"] == HIP_ID

    event = CallbackEvent.query.one()
    assert event.status == "delivered" and event.delivered_at is not None
    assert OutboxJob.query.one().status == "done"


def test_several_callback_types_are_routed_by_their_own_path(client, jwks, http, hip):
    paths = ["api/v3/hip/token/on-generate-token", "api/v3/hip/patient/care-context/discover",
             "api/v3/hiu/consent/request/notify"]
    for n, path in enumerate(paths):
        _imedicus_receives(http, path=path)
        _abdm_calls_gateway(client, body=json.dumps({"hipId": HIP_ID}), path="/" + path, request_id=f"req-multi-000{n}")
    assert worker.run(once=True) == 3
    hit = {c.request.url.split("/api/abdm-webhook/")[1] for c in http.calls if "client.example.test" in c.request.url}
    assert hit == set(paths)


def test_client_down_then_up_callback_is_delivered_after_retries(client, jwks, http, hip):
    http.add(responses.POST, f"{CALLBACK_URL}/api/v3/hip/patient/share", status=503)
    http.add(responses.POST, f"{CALLBACK_URL}/api/v3/hip/patient/share", status=503)
    http.add(responses.POST, f"{CALLBACK_URL}/api/v3/hip/patient/share", json={"status": "success"})
    _abdm_calls_gateway(client)

    for _ in range(3):
        OutboxJob.query.update({"next_attempt_at": utcnow() - timedelta(seconds=1)})  # skip the waiting
        db.session.commit()
        worker.run(once=True)

    job = OutboxJob.query.one()
    assert (job.status, job.attempts) == ("done", 3)
    assert CallbackEvent.query.one().status == "delivered"
    assert worker.run(once=True) == 0  # nothing left to do


def test_client_stays_down_callback_is_kept_and_marked_failed(client, jwks, http, hip):
    http.add(responses.POST, f"{CALLBACK_URL}/api/v3/hip/patient/share", status=500)
    _abdm_calls_gateway(client)
    for _ in range(6):
        OutboxJob.query.update({"next_attempt_at": utcnow() - timedelta(seconds=1)})
        db.session.commit()
        worker.run(once=True)
    assert OutboxJob.query.one().status == "dead"
    event = CallbackEvent.query.one()
    assert event.status == "failed" and "HTTP 500" in event.error  # kept for inspection / manual replay


def test_abdm_retrying_the_same_callback_does_not_deliver_twice(client, jwks, http, hip):
    _imedicus_receives(http)
    _abdm_calls_gateway(client)
    _abdm_calls_gateway(client)  # ABDM re-sends the same REQUEST-ID
    assert worker.run(once=True) == 1
    assert len([c for c in http.calls if "client.example.test" in c.request.url]) == 1


def test_callback_for_an_unknown_facility_is_kept_not_delivered(client, jwks, http, hip):
    body = PATIENT_SHARE.replace(HIP_ID, "IN9999999999")
    assert _abdm_calls_gateway(client, body=body).status_code == 202
    assert worker.run(once=True) == 0
    event = CallbackEvent.query.one()
    assert (event.status, event.hip_id) == ("unrouted", "IN9999999999")
    assert not [c for c in http.calls if "client.example.test" in c.request.url]


def test_forged_callback_is_rejected_and_nothing_is_stored(client, jwks, http, hip):
    res = client.post(SHARE_PATH, data=PATIENT_SHARE, headers={"REQUEST-ID": "forged-0001", "Content-Type": "application/json"})
    assert res.status_code == 401
    assert CallbackEvent.query.count() == 0 and OutboxJob.query.count() == 0


def test_disabling_the_hip_stops_delivery_of_queued_callbacks(client, jwks, http, hip):
    _imedicus_receives(http)
    _abdm_calls_gateway(client)
    hip.is_active = False
    db.session.commit()
    worker.run(once=True)
    assert OutboxJob.query.one().status == "dead"
    assert not [c for c in http.calls if "client.example.test" in c.request.url]


def test_production_webhook_can_never_be_delivered_to(client, jwks, http, hip):
    """Even if a registration were changed to the production host behind our back, nothing is sent there."""
    _abdm_calls_gateway(client)
    hip.callback_url = "https://app.i-medicus.in/api/abdm-webhook"
    db.session.commit()
    worker.run(once=True)
    assert OutboxJob.query.one().status == "dead"
    assert not [c for c in http.calls if "i-medicus.in" in c.request.url]


def test_callbacks_are_not_rate_limited(monkeypatch):
    """The per-IP default limit would throttle ABDM; callbacks are exempt (a normal endpoint is not)."""
    from src.config.index import TestingConfig

    monkeypatch.setattr(TestingConfig, "RATELIMIT_ENABLED", True)
    monkeypatch.setattr(TestingConfig, "DEFAULT_RATE_LIMIT", "1 per minute")
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        client = app.test_client()
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 429  # the limiter is really on
        app.config["CALLBACK_AUTH_MODE"] = "off"
        for n in range(4):
            res = client.post(SHARE_PATH, data=PATIENT_SHARE, headers={"REQUEST-ID": f"req-limit-000{n}"})
            assert res.status_code == 202
        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize("auth_mode", ["observe", "off"])
def test_sandbox_modes_still_deliver(app, client, jwks, http, hip, auth_mode):
    app.config["CALLBACK_AUTH_MODE"] = auth_mode
    _imedicus_receives(http)
    res = client.post(SHARE_PATH, data=PATIENT_SHARE, headers={"REQUEST-ID": "no-jwt-0001", "Content-Type": "application/json"})
    assert res.status_code == 202
    assert worker.run(once=True) == 1
    assert CallbackEvent.query.one().status == "delivered"
