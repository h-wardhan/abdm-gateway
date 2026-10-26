import json

import responses

from src.app.callbacks import vault
from src.app.extensions import db
from src.app.models import ApiAuditLog, CallbackEvent, OutboxJob
from tests.conftest import HIP_ID, make_jwt

SHARE = {"metaData": {"hipId": HIP_ID}, "profile": {"patient": {"abhaAddress": "ravi@sbx"}}}


def _post(client, path="/api/v3/hip/patient/share", body=SHARE, request_id="req-0001-abcdef", token=True, **headers):
    hdrs = {"REQUEST-ID": request_id, "X-CM-ID": "sbx", "Content-Type": "application/json", **headers}
    if token is True:
        hdrs["Authorization"] = f"Bearer {make_jwt()}"
    elif token:
        hdrs["Authorization"] = token
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.post(path, data=data, headers=hdrs)


def test_valid_callback_is_accepted_stored_and_queued(client, jwks, hip):
    res = _post(client)
    assert res.status_code == 202
    assert res.get_json()["data"] == {"status": "accepted"}

    event = CallbackEvent.query.one()
    assert (event.request_id, event.path, event.hip_id, event.status) == (
        "req-0001-abcdef", "api/v3/hip/patient/share", HIP_ID, "received")
    assert event.api_client_id == hip.api_client_id
    assert event.auth_result == "ok"
    assert "ravi@sbx" not in event.body_enc  # health data is encrypted at rest
    assert json.loads(vault.open_text(event.body_enc)) == SHARE
    assert "Authorization" not in (event.headers or {})

    job = OutboxJob.query.one()
    assert (job.kind, job.status, job.callback_event_id) == ("forward_callback", "pending", event.id)


def test_both_url_prefixes_map_to_the_same_forward_path(client, jwks, hip):
    _post(client, "/api/v3/hip/patient/share", request_id="req-prefix-0001")
    _post(client, "/v3/hip/patient/share", request_id="req-prefix-0002")
    assert {e.path for e in CallbackEvent.query.all()} == {"api/v3/hip/patient/share"}
    assert CallbackEvent.query.count() == 2


def test_request_without_authorization_is_rejected(client, jwks, hip):
    res = _post(client, token=False)
    assert res.status_code == 401
    assert res.get_json()["message"]["error"]["code"] == "CALLBACK_AUTH_FAILED"
    assert CallbackEvent.query.count() == 0 and OutboxJob.query.count() == 0


def test_invalid_jwt_is_rejected(client, jwks, hip):
    assert _post(client, token="Bearer garbage").status_code == 401
    assert CallbackEvent.query.count() == 0


def test_observe_mode_accepts_and_records_the_failure(app, client, jwks, hip):
    app.config["CALLBACK_AUTH_MODE"] = "observe"
    assert _post(client, token=False).status_code == 202
    assert CallbackEvent.query.one().auth_result == "failed:missing_token"
    assert OutboxJob.query.count() == 1  # still delivered, it is only observing


def test_off_mode_needs_no_token_and_no_jwks(app, client, http, hip):
    app.config["CALLBACK_AUTH_MODE"] = "off"
    assert _post(client, token=False).status_code == 202
    assert CallbackEvent.query.one().auth_result == "skipped"
    assert len(http.calls) == 0


def test_duplicate_request_id_and_path_is_dropped(client, jwks, hip):
    assert _post(client).status_code == 202
    res = _post(client)
    assert res.status_code == 202 and res.get_json()["data"]["duplicate"] is True
    assert CallbackEvent.query.count() == 1 and OutboxJob.query.count() == 1


def test_same_request_id_on_another_path_is_not_a_duplicate(client, jwks, hip):
    _post(client)
    _post(client, "/api/v3/hip/token/on-generate-token")
    assert CallbackEvent.query.count() == 2


def test_missing_request_id_gets_one_and_is_never_a_duplicate(client, jwks, hip):
    _post(client, request_id="")
    _post(client, request_id="")
    ids = [e.request_id for e in CallbackEvent.query.all()]
    assert len(set(ids)) == 2 and all(ids)


def test_unknown_hip_is_stored_unrouted_and_not_queued(client, jwks, hip):
    body = {"metaData": {"hipId": "IN-NOT-REGISTERED"}}
    assert _post(client, body=body).status_code == 202
    event = CallbackEvent.query.one()
    assert (event.status, event.hip_id, event.error) == ("unrouted", "IN-NOT-REGISTERED", "hip is not registered")
    assert event.api_client_id is None
    assert OutboxJob.query.count() == 0


def test_disabled_hip_is_unrouted(client, jwks, hip):
    hip.is_active = False
    db.session.commit()
    _post(client)
    event = CallbackEvent.query.one()
    assert (event.status, event.error) == ("unrouted", "hip is disabled")
    assert OutboxJob.query.count() == 0


def test_callback_without_hip_id_goes_to_the_only_registered_hip(client, jwks, hip):
    assert _post(client, body={"transactionId": "t1"}).status_code == 202
    event = CallbackEvent.query.one()
    assert (event.status, event.hip_id) == ("received", HIP_ID)
    assert OutboxJob.query.count() == 1


def test_callback_without_hip_id_and_no_unique_hip_is_unrouted(client, jwks, hip, api_client):
    from src.app.callbacks import registry
    registry.upsert(api_client[0], "IN0000000002", "https://client.example.test/x")
    _post(client, body={"transactionId": "t1"})
    event = CallbackEvent.query.one()
    assert event.status == "unrouted" and event.hip_id is None
    assert "more than one" in event.error


def test_hip_id_from_header(client, jwks, hip):
    _post(client, body={"transactionId": "t1"}, **{"X-HIP-ID": HIP_ID})
    assert CallbackEvent.query.one().hip_id == HIP_ID


def test_json_list_body_is_accepted(client, jwks, hip):
    assert _post(client, body=[{"a": 1}]).status_code == 202
    assert CallbackEvent.query.one().status == "received"  # single active HIP fallback


def test_non_json_body_is_a_400(client, jwks, hip):
    res = _post(client, body="this is not json")
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "INVALID_CALLBACK_BODY"
    assert CallbackEvent.query.count() == 0


def test_json_scalar_body_is_a_400(client, jwks, hip):
    assert _post(client, body="42").status_code == 400


def test_invalid_utf8_body_is_a_400(client, jwks, hip):
    assert _post(client, body=b"\xff\xfe\x00").status_code == 400


def test_bad_callback_path_is_a_400(client, jwks, hip):
    res = _post(client, "/api/v3/hip/..%2Fsecret")
    assert res.status_code in (400, 404)


def test_get_is_not_allowed(client, jwks, hip):
    assert client.get("/api/v3/hip/patient/share").status_code == 405


def test_bodies_bigger_than_the_normal_api_limit_are_accepted(client, jwks, hip):
    big = {"metaData": {"hipId": HIP_ID}, "entries": ["x" * 1000] * 3000}  # ~3 MB, the API limit is 1 MB
    assert _post(client, body=big).status_code == 202


def test_callback_limit_is_enforced(app, client, jwks, hip):
    app.config["CALLBACK_MAX_BYTES"] = 2000
    big = {"metaData": {"hipId": HIP_ID}, "entries": ["x" * 3000]}
    assert _post(client, body=big).status_code == 413
    assert CallbackEvent.query.count() == 0


def test_no_rate_limit_on_callbacks(app, client, jwks, hip):
    app.config["RATELIMIT_ENABLED"] = True
    app.config["RATELIMIT_DEFAULT"] = "1 per minute"
    for n in range(3):
        assert _post(client, request_id=f"req-rate-000{n}").status_code == 202


def test_callbacks_are_audited_without_the_token(client, jwks, hip):
    _post(client)
    row = ApiAuditLog.query.filter_by(route="/api/v3/hip/patient/share").one()
    assert row.http_status == 202
    assert "Bearer" not in json.dumps(row.request_body or {})


def test_jwks_is_fetched_only_once_for_many_callbacks(client, jwks, hip):
    for n in range(3):
        _post(client, request_id=f"req-cache-000{n}")
    jwks_calls = [c for c in jwks.calls if c.request.method == responses.GET]
    assert len(jwks_calls) == 1
