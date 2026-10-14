import base64

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
