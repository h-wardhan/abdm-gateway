"""ABDM client behaviour: gateway token cache, retries, error mapping, public key, audit fields."""
import base64

import pytest
import requests
import responses
from cryptography.hazmat.primitives import serialization

from src.app.abdm import crypto
from src.app.abdm.http import abdm_request
from src.app.errors import AbdmUnavailableError, AbdmUpstreamError
from src.app.models import AbdmErrorLog, ApiAuditLog
from tests.conftest import ABHA, GATEWAY, _PRIVATE_KEY, decrypt

SESSION_URL = f"{GATEWAY}/api/hiecm/gateway/v3/sessions"
CERT_URL = f"{ABHA}/abha/api/v3/profile/public/certificate"


def _session_calls(mock):
    return [c for c in mock.calls if c.request.url == SESSION_URL]


def test_gateway_token_is_cached(app, abdm):
    abdm.add(responses.GET, f"{ABHA}/x", json={"ok": True})
    abdm_request("GET", "/x")
    abdm_request("GET", "/x")
    assert len(_session_calls(abdm)) == 1


def test_session_sends_nha_credentials(app, abdm):
    abdm.add(responses.GET, f"{ABHA}/x", json={})
    abdm_request("GET", "/x")
    body = _session_calls(abdm)[0].request.body
    assert b'"clientId": "test-client"' in body and b'"grantType": "client_credentials"' in body


def test_401_refreshes_gateway_token_once(app, abdm):
    abdm.add(responses.POST, f"{ABHA}/y", status=401, json={"message": "expired"})
    abdm.add(responses.POST, f"{ABHA}/y", json={"ok": True})
    assert abdm_request("POST", "/y", json={}) == {"ok": True}
    assert len(_session_calls(abdm)) == 2


def test_persistent_401_without_user_token_is_gateway_problem(app, abdm):
    abdm.add(responses.POST, f"{ABHA}/y", status=401, json={"message": "bad"})
    with pytest.raises(AbdmUpstreamError) as exc:
        abdm_request("POST", "/y", json={})
    assert exc.value.status == 502 and exc.value.code == "ABDM_AUTH_FAILED"


def test_post_is_not_retried_on_5xx(app, abdm):
    abdm.add(responses.POST, f"{ABHA}/otp", status=500, json={"message": "boom"})
    with pytest.raises(AbdmUpstreamError) as exc:
        abdm_request("POST", "/otp", json={})
    assert exc.value.status == 502
    assert len([c for c in abdm.calls if c.request.url.endswith("/otp")]) == 1


def test_get_is_retried_on_5xx(app, abdm):
    abdm.add(responses.GET, f"{ABHA}/g", status=503)
    abdm.add(responses.GET, f"{ABHA}/g", json={"ok": True})
    assert abdm_request("GET", "/g") == {"ok": True}


def test_timeout_maps_to_504(app, abdm):
    abdm.add(responses.POST, f"{ABHA}/slow", body=requests.Timeout())
    with pytest.raises(AbdmUnavailableError) as exc:
        abdm_request("POST", "/slow", json={})
    assert exc.value.status == 504


def test_field_validation_errors_are_joined(app, abdm):
    abdm.add(responses.POST, f"{ABHA}/v", status=400, json={"loginId": "Invalid LoginId", "timestamp": "x"})
    with pytest.raises(AbdmUpstreamError) as exc:
        abdm_request("POST", "/v", json={})
    assert exc.value.status == 400 and "Invalid LoginId" in exc.value.message


def test_errors_are_stored_masked(app, abdm):
    abdm.add(responses.POST, f"{ABHA}/e", status=422, json={"code": "ABDM-1100", "message": "mobile 9876543210 invalid"})
    with pytest.raises(AbdmUpstreamError):
        abdm_request("POST", "/e", json={}, endpoint_name="test/e")
    row = AbdmErrorLog.query.one()
    assert row.abdm_endpoint == "test/e" and row.abdm_code == "ABDM-1100" and row.http_status == 422
    assert "9876543210" not in row.abdm_message and "3210" in row.abdm_message


def test_missing_nha_credentials(app, abdm):
    app.config["NHA_CLIENT_ID"] = None
    with pytest.raises(AbdmUpstreamError) as exc:
        abdm_request("GET", "/x")
    assert exc.value.code == "NHA_CREDENTIALS_MISSING"


def test_public_key_fetched_and_cached(app, abdm):
    app.config["ABDM_PUBLIC_KEY"] = None
    der = _PRIVATE_KEY.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    abdm.add(responses.GET, CERT_URL, json={"publicKey": base64.b64encode(der).decode(),
                                            "encryptionAlgorithm": "RSA/ECB/OAEPWithSHA-1AndMGF1Padding"})
    assert decrypt(crypto.encrypt("123456")) == "123456"
    crypto.encrypt("654321")
    assert len([c for c in abdm.calls if c.request.url == CERT_URL]) == 1


def test_audit_row_records_abdm_endpoint(client, auth, abdm):
    abdm.add(responses.POST, f"{ABHA}/abha/api/v3/enrollment/request/otp", json={"txnId": "t" * 36, "message": "sent"})
    client.post("/api/v1/abha/enrol/aadhaar/otp", json={"aadhaarNumber": "999941057058"}, headers=auth)
    row = ApiAuditLog.query.filter_by(route="/api/v1/abha/enrol/aadhaar/otp").one()
    assert row.abdm_endpoint == "enrollment/request/otp[aadhaar]" and row.abdm_status == 200
    assert row.request_body["aadhaarNumber"] == "********7058"
