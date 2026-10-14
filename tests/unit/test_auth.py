from src.app.extensions import db
from src.app.models import ApiAuditLog


def test_session_returns_tokens_in_imedicus_shape(client, api_client):
    tenant, secret = api_client
    res = client.post("/api/v1/auth/session", json={"clientId": tenant.client_id, "clientSecret": secret})
    assert res.status_code == 200
    data = res.get_json()["data"]
    # keys i-medicus reads in ABDMService::getBearerToken
    for key in ("accessToken", "expiresIn", "refreshToken", "refreshExpiresIn"):
        assert data[key]


def test_session_accepts_header_credentials(client, api_client):
    tenant, secret = api_client
    res = client.post("/api/v1/auth/session", headers={"Client-Id": tenant.client_id, "Client-Secret": secret})
    assert res.status_code == 200


def test_session_rejects_wrong_secret(client, api_client):
    tenant, _ = api_client
    res = client.post("/api/v1/auth/session", json={"clientId": tenant.client_id, "clientSecret": "nope"})
    assert res.status_code == 401
    assert res.get_json()["message"]["error"]["code"] == "INVALID_CLIENT"


def test_session_requires_both_fields(client):
    res = client.post("/api/v1/auth/session", json={})
    assert res.status_code == 400


def test_raw_and_bearer_token_both_work(client, access_token):
    for header in (access_token, f"Bearer {access_token}"):
        res = client.get("/api/v1/auth/me", headers={"Authorization": header})
        assert res.status_code == 200
        assert res.get_json()["data"]["name"] == "i-medicus test"


def test_missing_token(client):
    res = client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.get_json()["message"]["error"]["code"] == "MISSING_TOKEN"


def test_client_id_header_must_match(client, access_token):
    res = client.get("/api/v1/auth/me", headers={"Authorization": access_token, "Client-Id": "someone-else"})
    assert res.status_code == 401
    assert res.get_json()["message"]["error"]["code"] == "CLIENT_MISMATCH"


def test_refresh_token_cannot_be_used_as_access(client, api_client):
    tenant, secret = api_client
    tokens = client.post("/api/v1/auth/session", json={"clientId": tenant.client_id, "clientSecret": secret}).get_json()["data"]
    res = client.get("/api/v1/auth/me", headers={"Authorization": tokens["refreshToken"]})
    assert res.status_code == 401

    res = client.post("/api/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]})
    assert res.status_code == 200
    assert res.get_json()["data"]["accessToken"]


def test_rotate_revokes_old_tokens(client, api_client, access_token):
    tenant, _ = api_client
    tenant.rotate_secret()
    db.session.commit()
    res = client.get("/api/v1/auth/me", headers={"Authorization": access_token})
    assert res.status_code == 401
    assert res.get_json()["message"]["error"]["code"] == "TOKEN_REVOKED"


def test_disabled_client_is_forbidden(client, api_client, access_token):
    tenant, secret = api_client
    tenant.is_active = False
    db.session.commit()
    res = client.post("/api/v1/auth/session", json={"clientId": tenant.client_id, "clientSecret": secret})
    assert res.status_code == 403
    assert res.get_json()["message"]["error"]["code"] == "CLIENT_DISABLED"


def test_ip_allowlist(client, api_client, access_token):
    tenant, _ = api_client
    tenant.allowed_ips = "10.0.0.1"
    db.session.commit()
    res = client.get("/api/v1/auth/me", headers={"Authorization": access_token})
    assert res.status_code == 403
    assert res.get_json()["message"]["error"]["code"] == "IP_NOT_ALLOWED"


def test_audit_log_is_written_and_masked(client, api_client):
    tenant, secret = api_client
    client.post("/api/v1/auth/session", json={"clientId": tenant.client_id, "clientSecret": secret})
    row = ApiAuditLog.query.filter_by(route="/api/v1/auth/session").first()
    assert row is not None
    assert row.http_status == 200
    assert row.api_client_id == tenant.id
    assert row.request_body["clientSecret"] == "***"


def test_health_is_not_audited(client):
    client.get("/health")
    assert ApiAuditLog.query.count() == 0
