"""One webhook for all of a client's clinics: an unknown clinic is registered for the client on its first callback."""
import json

import pytest

from src.app.callbacks import forwarder, registry, vault
from src.app.errors import ValidationError
from src.app.extensions import db
from src.app.models import ApiClient, CallbackEvent, HipRegistration, OutboxJob
from tests.conftest import CALLBACK_URL, HIP_ID, make_jwt

OTHER_URL = "https://client.example.test/other/webhook"


def _post(client, hip_id, request_id="req-share-0001"):
    headers = {"REQUEST-ID": request_id, "Content-Type": "application/json", "Authorization": f"Bearer {make_jwt()}"}
    data = json.dumps({"metaData": {"hipId": hip_id}, "profile": {"patient": {"abhaAddress": "ravi@sbx"}}})
    return client.post("/api/v3/hip/patient/share", data=data, headers=headers)


@pytest.fixture
def tenant(api_client):
    return api_client[0]


@pytest.fixture
def webhook(tenant):
    """The tenant with a default webhook; returns its plain secret."""
    return registry.set_client_webhook(tenant, CALLBACK_URL)


def _other_client():
    other, _ = ApiClient.create(name="other app")
    db.session.add(other)
    db.session.commit()
    return other


# ---- registry ---------------------------------------------------------------------------------------------------

def test_set_webhook_returns_the_secret_only_once(tenant):
    secret = registry.set_client_webhook(tenant, CALLBACK_URL + "/")
    assert secret and tenant.callback_url == CALLBACK_URL
    assert vault.open_secret(tenant.webhook_secret_enc) == secret
    assert registry.set_client_webhook(tenant, OTHER_URL) is None
    assert tenant.callback_url == OTHER_URL and vault.open_secret(tenant.webhook_secret_enc) == secret


def test_set_webhook_rejects_unsafe_urls(tenant):
    with pytest.raises(registry.UnsafeUrlError):
        registry.set_client_webhook(tenant, "http://client.example.test/hook")
    assert tenant.callback_url is None


def test_rotate_webhook_secret(tenant, webhook):
    new = registry.rotate_client_webhook_secret(tenant)
    assert new != webhook and vault.open_secret(tenant.webhook_secret_enc) == new


def test_auto_registered_hips_follow_the_client_webhook(app, tenant, webhook):
    own, _ = registry.upsert(tenant, "IN-OWN-0001", CALLBACK_URL)
    auto = registry.register_from_default_webhook("IN-AUTO-0001")
    registry.set_client_webhook(tenant, OTHER_URL)
    new_secret = registry.rotate_client_webhook_secret(tenant)
    db.session.refresh(own)
    db.session.refresh(auto)
    assert auto.callback_url == OTHER_URL and vault.open_secret(auto.webhook_secret_enc) == new_secret
    assert own.callback_url == CALLBACK_URL and vault.open_secret(own.webhook_secret_enc) != new_secret


def test_registering_by_hand_stops_following_the_client_webhook(tenant, webhook):
    auto = registry.register_from_default_webhook(HIP_ID)
    assert auto.auto_registered is True
    reg, secret = registry.upsert(tenant, HIP_ID, OTHER_URL)
    assert secret is None and reg.auto_registered is False and reg.callback_url == OTHER_URL


def test_default_client_is_the_only_active_one_with_a_webhook(app, tenant):
    assert registry.default_client() is None
    registry.set_client_webhook(tenant, CALLBACK_URL)
    assert registry.default_client() == tenant

    other = _other_client()
    assert registry.default_client() == tenant  # no webhook: not a candidate

    registry.set_client_webhook(other, OTHER_URL)
    assert registry.default_client() is None  # two candidates: no guessing

    other.is_active = False
    db.session.commit()
    assert registry.default_client() == tenant


def test_register_from_default_webhook_without_a_default_client(app, tenant):
    assert registry.register_from_default_webhook(HIP_ID) is None
    assert HipRegistration.query.count() == 0


def test_register_from_default_webhook_rejects_a_bad_facility_id(tenant, webhook):
    assert registry.register_from_default_webhook("x") is None
    assert HipRegistration.query.count() == 0


def test_register_from_default_webhook_copies_url_and_secret(tenant, webhook):
    reg = registry.register_from_default_webhook(HIP_ID)
    assert (reg.hip_id, reg.api_client_id, reg.callback_url, reg.auto_registered, reg.is_active) == (
        HIP_ID, tenant.id, CALLBACK_URL, True, True)
    assert vault.open_secret(reg.webhook_secret_enc) == webhook


# ---- receiver ---------------------------------------------------------------------------------------------------

def test_first_callback_of_a_new_clinic_is_delivered_and_the_clinic_is_registered(client, jwks, tenant, webhook):
    assert _post(client, "IN-NEW-CLINIC-1").status_code == 202

    reg = HipRegistration.query.one()
    assert (reg.hip_id, reg.api_client_id, reg.auto_registered) == ("IN-NEW-CLINIC-1", tenant.id, True)
    event = CallbackEvent.query.one()
    assert (event.status, event.hip_id, event.api_client_id) == ("received", "IN-NEW-CLINIC-1", tenant.id)
    assert OutboxJob.query.one().callback_event_id == event.id

    # The next callbacks of that clinic use the registration: nothing new is created
    assert _post(client, "IN-NEW-CLINIC-1", request_id="req-share-0002").status_code == 202
    assert HipRegistration.query.count() == 1 and CallbackEvent.query.filter_by(status="received").count() == 2


def test_forwarded_callback_is_signed_with_the_client_webhook_secret(client, jwks, webhook):
    _post(client, "IN-NEW-CLINIC-2")
    event, reg = CallbackEvent.query.one(), HipRegistration.query.one()
    url, headers, body = forwarder.build_request(event, reg)
    assert url == f"{CALLBACK_URL}/api/v3/hip/patient/share"
    timestamp = headers["X-Gateway-Timestamp"]
    assert headers["X-Gateway-Signature"] == forwarder.sign(webhook, timestamp, body)


def test_many_new_clinics_each_get_their_own_registration(client, jwks, tenant, webhook):
    for n in range(5):
        assert _post(client, f"IN-CLINIC-{n:04d}", request_id=f"req-share-100{n}").status_code == 202
    assert sorted(r.hip_id for r in HipRegistration.query.all()) == [f"IN-CLINIC-{n:04d}" for n in range(5)]
    assert {r.api_client_id for r in HipRegistration.query.all()} == {tenant.id}
    assert CallbackEvent.query.filter_by(status="received").count() == 5


def test_registered_clinic_keeps_its_own_url_when_a_default_exists(client, jwks, tenant, webhook):
    own, _ = registry.upsert(tenant, HIP_ID, OTHER_URL)
    _post(client, HIP_ID)
    assert HipRegistration.query.count() == 1 and own.callback_url == OTHER_URL
    assert CallbackEvent.query.one().status == "received"


def test_two_clients_with_a_default_webhook_leave_unknown_clinics_unrouted(client, jwks, tenant, webhook):
    registry.set_client_webhook(_other_client(), OTHER_URL)

    assert _post(client, "IN-NEW-CLINIC-3").status_code == 202
    event = CallbackEvent.query.one()
    assert (event.status, event.error) == ("unrouted", "hip is not registered")
    assert HipRegistration.query.count() == 0 and OutboxJob.query.count() == 0


def test_registered_clinics_are_routed_even_when_two_clients_have_a_default(client, jwks, tenant, webhook):
    other = _other_client()
    registry.set_client_webhook(other, OTHER_URL)
    registry.upsert(other, "IN-OTHER-0001", OTHER_URL)
    registry.upsert(tenant, "IN-MINE-0001", CALLBACK_URL)

    _post(client, "IN-OTHER-0001", request_id="req-share-2001")
    _post(client, "IN-MINE-0001", request_id="req-share-2002")
    by_hip = {e.hip_id: e.api_client_id for e in CallbackEvent.query.all()}
    assert by_hip == {"IN-OTHER-0001": other.id, "IN-MINE-0001": tenant.id}


def test_callback_with_a_bad_facility_id_is_unrouted(client, jwks, webhook):
    _post(client, "x")
    assert CallbackEvent.query.one().status == "unrouted" and HipRegistration.query.count() == 0


def test_disabled_auto_registered_clinic_stays_disabled(client, jwks, webhook):
    _post(client, "IN-NEW-CLINIC-4")
    registry.disable(HipRegistration.query.one())
    _post(client, "IN-NEW-CLINIC-4", request_id="req-share-0003")
    assert CallbackEvent.query.order_by(CallbackEvent.id.desc()).first().error == "hip is disabled"
    assert HipRegistration.query.count() == 1


def test_two_callbacks_of_the_same_new_clinic_at_once(client, jwks, tenant, webhook, monkeypatch):
    """The other callback registered the clinic between our lookup and our insert: use its registration."""
    existing, _ = registry.upsert(tenant, "IN-RACE-0001", CALLBACK_URL)
    real_get = registry.get
    calls = []

    def stale_first_lookup(hip_id):
        calls.append(hip_id)
        return None if len(calls) == 1 else real_get(hip_id)

    monkeypatch.setattr(registry, "get", stale_first_lookup)
    assert _post(client, "IN-RACE-0001").status_code == 202

    assert HipRegistration.query.count() == 1
    event = CallbackEvent.query.one()
    assert (event.status, event.hip_id, event.api_client_id) == ("received", "IN-RACE-0001", existing.api_client_id)
    assert OutboxJob.query.count() == 1


# ---- cli --------------------------------------------------------------------------------------------------------

@pytest.fixture
def run(app):
    runner = app.test_cli_runner()
    return lambda *args: runner.invoke(args=list(args))


def test_cli_webhook_set_shows_the_secret_once(run, tenant):
    res = run("webhook", "set", "--client", tenant.client_id, "--url", CALLBACK_URL)
    assert res.exit_code == 0, res.output
    assert "Webhook secret" in res.output and CALLBACK_URL in res.output
    again = run("webhook", "set", "--client", tenant.client_id, "--url", OTHER_URL)
    assert again.exit_code == 0 and "Webhook secret" not in again.output
    assert db.session.get(ApiClient, tenant.id).callback_url == OTHER_URL


def test_cli_webhook_set_errors(run, tenant):
    assert "No client" in run("webhook", "set", "--client", "nope", "--url", CALLBACK_URL).output
    res = run("webhook", "set", "--client", tenant.client_id, "--url", "http://client.example.test/hook")
    assert res.exit_code != 0 and "https" in res.output


def test_cli_webhook_rotate_secret(run, tenant, webhook):
    res = run("webhook", "rotate-secret", "--client", tenant.client_id)
    assert res.exit_code == 0 and "Webhook secret :" in res.output
    assert webhook not in res.output


def test_cli_webhook_rotate_needs_a_webhook(run, tenant):
    res = run("webhook", "rotate-secret", "--client", tenant.client_id)
    assert res.exit_code != 0 and "no default webhook" in res.output


def test_cli_webhook_show(run, tenant):
    assert "No default webhooks" in run("webhook", "show").output
    registry.set_client_webhook(tenant, CALLBACK_URL)
    res = run("webhook", "show")
    assert tenant.client_id in res.output and CALLBACK_URL in res.output and "NOT routed" not in res.output

    registry.set_client_webhook(_other_client(), OTHER_URL)
    assert "NOT routed" in run("webhook", "show").output


def test_cli_hips_list_marks_auto_registered(run, tenant, webhook):
    registry.register_from_default_webhook(HIP_ID)
    assert "(auto)" in run("hips", "list").output


def test_unsafe_url_is_a_validation_error():
    assert issubclass(registry.UnsafeUrlError, ValidationError)
