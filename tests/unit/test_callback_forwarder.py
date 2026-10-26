import hashlib
import hmac

from src.app.callbacks import forwarder, vault
from src.app.extensions import db
from src.app.models import CallbackEvent
from tests.conftest import CALLBACK_URL

# deliberately odd spacing / key order: it must reach the client exactly like this
RAW_BODY = '{"profile":  {"patient": {"abhaAddress": "ravi@sbx", "name": "Ravi \\u0915"}},\n "metaData": {"hipId": "IN0110005723"}}'


def _event(hip, **overrides):
    event = CallbackEvent(
        request_id="req-0001-abcdef", path="api/v3/hip/patient/share", hip_id=hip.hip_id, api_client_id=hip.api_client_id,
        status="received", body_enc=vault.seal_text(RAW_BODY),
        headers={"REQUEST-ID": "req-0001-abcdef", "TIMESTAMP": "2026-10-22T10:00:00.000Z", "X-CM-ID": "sbx"},
    )
    for key, value in overrides.items():
        setattr(event, key, value)
    db.session.add(event)
    db.session.commit()
    return event


def test_sign_matches_a_plain_hmac():
    expected = hmac.new(b"s3cret", b"123.payload", hashlib.sha256).hexdigest()
    assert forwarder.sign("s3cret", "123", b"payload") == f"sha256={expected}"


def test_kept_headers_never_include_authorization():
    kept = forwarder.kept_headers({"Authorization": "Bearer abc", "REQUEST-ID": "r1", "X-CM-ID": "sbx", "Other": "x"})
    assert kept == {"REQUEST-ID": "r1", "X-CM-ID": "sbx"}


def test_request_body_is_forwarded_byte_for_byte(app, hip):
    event = _event(hip)
    url, headers, body = forwarder.build_request(event, hip)
    assert body == RAW_BODY.encode("utf-8")
    assert url == f"{CALLBACK_URL}/api/v3/hip/patient/share"


def test_headers_follow_the_vcdoctor_contract(app, hip):
    event = _event(hip)
    _, headers, _ = forwarder.build_request(event, hip)
    assert headers["request-id"] == "req-0001-abcdef"
    assert headers["Content-Type"] == "application/json"
    assert headers["timestamp"] == "2026-10-22T10:00:00.000Z"
    assert headers["x-cm-id"] == "sbx"
    assert "x-hip-id" not in headers  # was not stored
    assert headers["X-Gateway-Event-Id"] == str(event.id)
    assert headers["User-Agent"].startswith("abdm-gateway/")


def test_signature_can_be_verified_by_the_client(app, hip):
    event = _event(hip)
    _, headers, body = forwarder.build_request(event, hip)
    expected = forwarder.sign(hip.test_secret, headers["X-Gateway-Timestamp"], body)
    assert headers["X-Gateway-Signature"] == expected


def test_event_without_stored_headers(app, hip):
    event = _event(hip, headers=None)
    _, headers, _ = forwarder.build_request(event, hip)
    assert "timestamp" not in headers and headers["request-id"] == "req-0001-abcdef"


def test_callback_url_with_a_path_and_trailing_slash(app, hip):
    hip.callback_url = "https://client.example.test/app/api/abdm-webhook/"
    url, _, _ = forwarder.build_request(_event(hip), hip)
    assert url == "https://client.example.test/app/api/abdm-webhook/api/v3/hip/patient/share"
