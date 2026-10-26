import pytest

from src.app.callbacks import registry, vault
from src.app.errors import ConflictError, NotFoundError, ValidationError
from src.app.extensions import db
from src.app.models import ApiClient, HipRegistration
from tests.conftest import CALLBACK_URL, HIP_ID


def test_upsert_creates_and_returns_secret_once(app, api_client):
    tenant, _ = api_client
    reg, secret = registry.upsert(tenant, HIP_ID, CALLBACK_URL + "/", "Clinic")
    assert secret and reg.callback_url == CALLBACK_URL  # trailing slash removed
    assert reg.is_active and reg.name == "Clinic"
    assert vault.open_secret(reg.webhook_secret_enc) == secret
    assert secret not in reg.webhook_secret_enc  # stored encrypted

    reg2, secret2 = registry.upsert(tenant, HIP_ID, "https://client.example.test/other")
    assert reg2.id == reg.id and secret2 is None
    assert reg2.callback_url == "https://client.example.test/other"
    assert reg2.name == "Clinic"  # name kept when not sent


def test_upsert_reactivates_a_disabled_hip(app, hip, api_client):
    registry.disable(hip)
    reg, _ = registry.upsert(api_client[0], HIP_ID, CALLBACK_URL)
    assert reg.is_active


def test_hip_cannot_be_taken_by_another_client(app, hip):
    other, _ = ApiClient.create(name="other")
    db.session.add(other)
    db.session.commit()
    with pytest.raises(ConflictError) as exc:
        registry.upsert(other, HIP_ID, CALLBACK_URL)
    assert exc.value.code == "HIP_TAKEN"


@pytest.mark.parametrize("hip_id", ["", "ab", "has space", "-lead", "x" * 65, None, "semi;colon"])
def test_invalid_hip_id(app, api_client, hip_id):
    with pytest.raises(ValidationError) as exc:
        registry.upsert(api_client[0], hip_id, CALLBACK_URL)
    assert exc.value.code == "INVALID_HIP_ID"


@pytest.mark.parametrize("hip_id", ["IN0110005723", "IN0110005723_240", "abc", "A.b-c_d"])
def test_valid_hip_ids(hip_id):
    assert registry.validate_hip_id(f" {hip_id} ") == hip_id


@pytest.mark.parametrize("url,message", [
    ("", "https"),
    ("not a url", "https"),
    ("ftp://client.example.test/x", "https"),
    ("http://client.example.test/x", "https"),
    ("https:///nohost", "no host"),
    ("https://user:pass@client.example.test/x", "credentials"),
    ("https://user@client.example.test/x", "credentials"),
    ("https://client.example.test/x?a=1", "query"),
    ("https://client.example.test/x#frag", "query"),
    ("https://client.example.test:99999/x", "not a valid URL"),
    ("https://app.i-medicus.in/api/abdm-webhook", "blocked"),
    ("https://APP.I-MEDICUS.IN/api/abdm-webhook", "blocked"),
])
def test_unsafe_urls_are_rejected(app, url, message):
    with pytest.raises(registry.UnsafeUrlError) as exc:
        registry.validate_callback_url(url)
    assert message in exc.value.message
    assert exc.value.code == "UNSAFE_CALLBACK_URL"


@pytest.mark.parametrize("host", [
    "127.0.0.1", "10.1.2.3", "192.168.0.10", "172.16.5.5", "169.254.169.254", "0.0.0.0", "224.0.0.1",
    "[::1]", "[fe80::1]", "internal.example.test",
])
def test_private_addresses_are_blocked(app, host):
    with pytest.raises(registry.UnsafeUrlError) as exc:
        registry.validate_callback_url(f"https://{host}/hook")
    assert "private" in exc.value.message


def test_private_addresses_allowed_when_configured(app):
    app.config["CALLBACK_ALLOW_PRIVATE_URLS"] = True
    assert registry.validate_callback_url("https://127.0.0.1:5055/hook") == "https://127.0.0.1:5055/hook"


def test_http_allowed_when_configured(app):
    app.config["CALLBACK_ALLOW_HTTP"] = True
    assert registry.validate_callback_url("http://93.184.216.34:8081/api/abdm-webhook") == \
        "http://93.184.216.34:8081/api/abdm-webhook"


def test_blocked_host_stays_blocked_even_when_private_and_http_allowed(app):
    app.config.update(CALLBACK_ALLOW_HTTP=True, CALLBACK_ALLOW_PRIVATE_URLS=True)
    with pytest.raises(registry.UnsafeUrlError):
        registry.validate_callback_url("http://app.i-medicus.in/x")


def test_public_ip_literal_is_fine(app):
    assert registry.validate_callback_url("https://8.8.8.8/hook") == "https://8.8.8.8/hook"


def test_unresolvable_host(app):
    with pytest.raises(registry.UnresolvableHostError) as exc:
        registry.validate_callback_url("https://missing.example.test/hook")
    assert exc.value.code == "CALLBACK_HOST_UNRESOLVABLE"


def test_get_for_client_checks_ownership(app, hip, api_client):
    assert registry.get_for_client(api_client[0], HIP_ID).id == hip.id
    other, _ = ApiClient.create(name="other")
    db.session.add(other)
    db.session.commit()
    with pytest.raises(NotFoundError):
        registry.get_for_client(other, HIP_ID)
    with pytest.raises(NotFoundError):
        registry.get_for_client(api_client[0], "NOPE-123")


def test_rotate_secret(app, hip):
    old = vault.open_secret(hip.webhook_secret_enc)
    new = registry.rotate_secret(hip)
    assert new != old and vault.open_secret(hip.webhook_secret_enc) == new


def test_single_active_registration(app, hip, api_client):
    assert registry.single_active_registration().id == hip.id
    registry.upsert(api_client[0], "IN0000000002", CALLBACK_URL)
    assert registry.single_active_registration() is None  # two active HIPs: no guessing
    registry.disable(registry.get("IN0000000002"))
    assert registry.single_active_registration().id == hip.id
    registry.disable(hip)
    assert registry.single_active_registration() is None
    assert HipRegistration.query.count() == 2
