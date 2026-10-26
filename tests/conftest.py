import base64
import socket
import time

import jwt as pyjwt
import pytest
import responses as responses_lib
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.app import create_app
from src.app.extensions import db
from src.app.models import ApiClient

GATEWAY = "https://dev.abdm.gov.in"
ABHA = "https://abhasbx.abdm.gov.in"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


# Key pair that plays "ABDM" when it signs callbacks
_JWT_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWT_KID = "test-kid-1"
CALLBACK_URL = "https://client.example.test/api/abdm-webhook"
HIP_ID = "IN0110005723"

# Hostnames the tests use -> what DNS "returns" (no test ever does a real lookup)
FAKE_DNS = {
    "client.example.test": "93.184.216.34",
    "internal.example.test": "10.0.0.5",
}


def jwk_for(key, kid):
    jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def jwks_body(*kids):
    return {"keys": [jwk_for(_JWT_KEY, kid) for kid in (kids or (JWT_KID,))]}


def make_jwt(kid=JWT_KID, key=None, expires_in=300, **claims):
    """A token signed the way ABDM signs callbacks."""
    payload = {"exp": int(time.time()) + expires_in, "iss": "abdm-test", **claims}
    return pyjwt.encode(payload, key or _JWT_KEY, algorithm="RS256", headers={"kid": kid})


def decrypt(value):
    """Decrypt what the gateway encrypted for ABDM (tests hold the matching private key)."""
    return _PRIVATE_KEY.decrypt(
        base64.b64decode(value),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
    ).decode()


@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update(
        ABDM_GATEWAY_URL=GATEWAY,
        ABDM_ABHA_URL=ABHA,
        ABDM_X_CM_ID="sbx",
        ABDM_PUBLIC_KEY=_PUBLIC_PEM,
        ABDM_JWKS_URL=f"{GATEWAY}/api/hiecm/gateway/v3/certs",
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_tenant(name):
    tenant, secret = ApiClient.create(name=name)
    db.session.add(tenant)
    db.session.commit()
    return tenant, secret


@pytest.fixture
def api_client(app):
    """A registered tenant: returns (ApiClient, plain secret)."""
    return _make_tenant("i-medicus test")


@pytest.fixture
def access_token(client, api_client):
    tenant, secret = api_client
    res = client.post("/api/v1/auth/session", json={"clientId": tenant.client_id, "clientSecret": secret})
    return res.get_json()["data"]["accessToken"]


@pytest.fixture
def auth(access_token):
    """Headers the way i-medicus sends them: raw token, no 'Bearer '."""
    return {"Authorization": access_token}


@pytest.fixture
def other_auth(client, app):
    tenant, secret = _make_tenant("other clinic")
    res = client.post("/api/v1/auth/session", json={"clientId": tenant.client_id, "clientSecret": secret})
    return {"Authorization": res.get_json()["data"]["accessToken"]}


@pytest.fixture
def abdm():
    """Mocked ABDM with a working gateway session endpoint."""
    with responses_lib.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(responses_lib.POST, f"{GATEWAY}/api/hiecm/gateway/v3/sessions",
                 json={"accessToken": "gw-token", "expiresIn": 1200}, status=200)
        yield mock


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """Callback bodies and webhook secrets are encrypted at rest; EncryptionUtil reads this env var."""
    monkeypatch.setenv("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """No test resolves real hostnames. Returns the table so a test can add or remove entries."""
    table = dict(FAKE_DNS)

    def fake_getaddrinfo(host, *_args, **_kwargs):
        if host not in table:
            raise socket.gaierror(f"no such host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], 0))]

    monkeypatch.setattr("src.app.callbacks.registry.socket.getaddrinfo", fake_getaddrinfo)
    return table


@pytest.fixture
def http():
    """Mocked outbound HTTP. Anything not mocked raises, so no test can reach a real server."""
    with responses_lib.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def jwks(http, app):
    """`http` with ABDM's JWKS endpoint answering for the test signing key."""
    http.add(responses_lib.GET, app.config["ABDM_JWKS_URL"], json=jwks_body(), status=200)
    return http


@pytest.fixture
def abdm_jwt():
    return make_jwt()


@pytest.fixture
def hip(app, api_client):
    """A registered HIP for the test tenant (https callback URL on a test host)."""
    from src.app.callbacks import registry

    tenant, _ = api_client
    reg, secret = registry.upsert(tenant, HIP_ID, CALLBACK_URL, "Test clinic")
    reg.test_secret = secret
    return reg
