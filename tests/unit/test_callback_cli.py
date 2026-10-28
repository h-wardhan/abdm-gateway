import json
from datetime import timedelta

import pytest
import responses

from src.app.callbacks import registry
from src.app.extensions import db
from src.app.models import CallbackEvent, HipRegistration, OutboxJob
from src.app.models.base import utcnow
from src.app.outbox import cli as outbox_cli
from src.app.outbox import worker
from tests.conftest import CALLBACK_URL, GATEWAY, HIP_ID

BRIDGE = f"{GATEWAY}/api/hiecm/gateway/v3/bridge/url"
FACILITY = "https://facilitysbx.abdm.gov.in"
SERVICES = f"{FACILITY}/v1/bridges/MutipleHRPAddUpdateServices"
SERVICES_LIST = f"{GATEWAY}/api/hiecm/gateway/v3/bridge-services"
SERVICE = f"{GATEWAY}/api/hiecm/gateway/v3/bridge-service/serviceId/IN0110005723"


@pytest.fixture
def run(app):
    runner = app.test_cli_runner()

    def invoke(*args):
        return runner.invoke(args=list(args))

    return invoke


# ---- hips ---------------------------------------------------------------------------------------------------

def test_hips_add_prints_the_secret_once(run, api_client):
    tenant, _ = api_client
    res = run("hips", "add", "--client", tenant.client_id, "--hip-id", HIP_ID, "--callback-url", CALLBACK_URL,
              "--name", "Clinic")
    assert res.exit_code == 0, res.output
    assert "Webhook secret" in res.output and CALLBACK_URL in res.output
    assert HipRegistration.query.one().name == "Clinic"

    again = run("hips", "add", "--client", tenant.client_id, "--hip-id", HIP_ID, "--callback-url", CALLBACK_URL)
    assert again.exit_code == 0 and "Webhook secret" not in again.output


def test_hips_add_unknown_client(run):
    res = run("hips", "add", "--client", "nope", "--hip-id", HIP_ID, "--callback-url", CALLBACK_URL)
    assert res.exit_code != 0 and "No client" in res.output


def test_hips_add_refuses_the_production_webhook(run, api_client):
    res = run("hips", "add", "--client", api_client[0].client_id, "--hip-id", HIP_ID,
              "--callback-url", "https://app.i-medicus.in/api/abdm-webhook")
    assert res.exit_code != 0 and "blocked host" in res.output
    assert HipRegistration.query.count() == 0


def test_hips_list(run, hip):
    empty_check = run("hips", "list")
    assert HIP_ID in empty_check.output and "yes" in empty_check.output and CALLBACK_URL in empty_check.output
    registry.disable(hip)
    assert "no" in run("hips", "list").output


def test_hips_list_when_empty(run, app):
    assert "No HIPs yet" in run("hips", "list").output


def test_hips_set_url(run, hip):
    res = run("hips", "set-url", HIP_ID, "https://client.example.test/new/")
    assert res.exit_code == 0 and "https://client.example.test/new" in res.output
    assert registry.get(HIP_ID).callback_url == "https://client.example.test/new"


def test_hips_set_url_unknown_hip_and_unsafe_url(run, hip):
    assert "No HIP" in run("hips", "set-url", "IN-NOT-THERE", CALLBACK_URL).output
    res = run("hips", "set-url", HIP_ID, "https://app.i-medicus.in/x")
    assert res.exit_code != 0 and "blocked host" in res.output
    assert registry.get(HIP_ID).callback_url == CALLBACK_URL


def test_hips_rotate_secret(run, hip):
    before = hip.webhook_secret_enc
    res = run("hips", "rotate-secret", HIP_ID)
    assert res.exit_code == 0 and "Webhook secret :" in res.output
    assert registry.get(HIP_ID).webhook_secret_enc != before
    assert run("hips", "rotate-secret", "IN-NOT-THERE").exit_code != 0


def test_hips_disable(run, hip):
    res = run("hips", "disable", HIP_ID)
    assert res.exit_code == 0 and registry.get(HIP_ID).is_active is False
    assert run("hips", "disable", "IN-NOT-THERE").exit_code != 0


# ---- abdm bridge-url / services: sandbox only, dry run by default --------------------------------------------

def test_bridge_url_dry_run_sends_nothing(run, abdm):
    res = run("abdm", "bridge-url", "set", "--url", "https://gw.example.test")
    assert res.exit_code == 0, res.output
    assert "Dry run" in res.output and BRIDGE in res.output and "https://gw.example.test" in res.output
    assert len(abdm.calls) == 0


def test_bridge_url_confirm_sends_to_the_sandbox(run, abdm):
    abdm.add(responses.PATCH, BRIDGE, json={"message": "updated"}, status=200)
    res = run("abdm", "bridge-url", "set", "--url", "https://gw.example.test", "--confirm")
    assert res.exit_code == 0, res.output
    assert "Sent to ABDM sandbox" in res.output and "updated" in res.output
    patch = [c for c in abdm.calls if c.request.method == "PATCH"][0]
    assert json.loads(patch.request.body) == {"url": "https://gw.example.test"}


def test_bridge_url_empty_response(run, abdm):
    abdm.add(responses.PATCH, BRIDGE, status=204)
    assert "(empty response)" in run("abdm", "bridge-url", "set", "--url", "https://gw.example.test", "--confirm").output


def test_bridge_url_defaults_to_the_public_url_setting(run, app, abdm):
    app.config["GATEWAY_PUBLIC_URL"] = "https://public.example.test"
    assert "https://public.example.test" in run("abdm", "bridge-url", "set").output


@pytest.mark.parametrize("args", [["--url", "http://insecure.example.test"], []])
def test_bridge_url_needs_an_https_url(run, app, args):
    app.config["GATEWAY_PUBLIC_URL"] = None
    res = run("abdm", "bridge-url", "set", *args)
    assert res.exit_code != 0 and "https URL" in res.output


def test_abdm_commands_refuse_to_run_outside_the_sandbox(run, app, abdm):
    app.config["ABDM_ENV"] = "prod"
    for command in (["bridge-url", "set", "--url", "https://gw.example.test", "--confirm"],
                    ["services", "add", "--hip-id", HIP_ID, "--name", "Clinic", "--confirm"]):
        res = run("abdm", *command)
        assert res.exit_code != 0 and "only works against the ABDM sandbox" in res.output
    assert len(abdm.calls) == 0


def test_abdm_commands_refuse_the_production_host_even_in_sbx_mode(run, app, abdm):
    app.config["ABDM_GATEWAY_URL"] = "https://live.abdm.gov.in"
    res = run("abdm", "bridge-url", "set", "--url", "https://gw.example.test", "--confirm")
    assert res.exit_code != 0 and "production host" in res.output
    assert len(abdm.calls) == 0


def test_services_add_dry_run_and_confirm(run, abdm):
    dry = run("abdm", "services", "add", "--hip-id", HIP_ID, "--name", "Clinic")
    assert dry.exit_code == 0 and "Dry run" in dry.output and SERVICES in dry.output
    assert len(abdm.calls) == 0

    abdm.add(responses.POST, SERVICES, json={"ok": True}, status=200)
    res = run("abdm", "services", "add", "--hip-id", HIP_ID, "--name", "Clinic", "--type", "HIU", "--confirm")
    assert res.exit_code == 0, res.output
    post = [c for c in abdm.calls if c.request.url == SERVICES][0]
    assert post.request.headers["Authorization"] == "Bearer gw-token"
    assert json.loads(post.request.body) == {
        "facilityId": HIP_ID, "facilityName": "Clinic",
        "HRP": [{"bridgeId": "test-client", "hipName": "Clinic", "type": "HIU", "active": True}]}


def test_services_list_and_get_read_from_the_sandbox_gateway(run, abdm):
    abdm.add(responses.GET, SERVICES_LIST, json=[{"id": HIP_ID, "type": "HIP"}], status=200)
    abdm.add(responses.GET, SERVICE, json={"id": HIP_ID, "active": True}, status=200)
    listed = run("abdm", "services", "list")
    assert listed.exit_code == 0 and HIP_ID in listed.output
    got = run("abdm", "services", "get", HIP_ID)
    assert got.exit_code == 0 and '"active": true' in got.output


def test_services_list_empty_and_outside_the_sandbox(run, app, abdm):
    abdm.add(responses.GET, SERVICES_LIST, status=204)
    assert "(empty response)" in run("abdm", "services", "list").output
    app.config["ABDM_ENV"] = "prod"
    res = run("abdm", "services", "list")
    assert res.exit_code != 0 and "only works against the ABDM sandbox" in res.output


def test_services_add_refuses_the_production_facility_registry(run, app, abdm):
    app.config["ABDM_FACILITY_URL"] = "https://facility.abdm.gov.in"
    res = run("abdm", "services", "add", "--hip-id", HIP_ID, "--name", "Clinic", "--confirm")
    assert res.exit_code != 0 and "production host" in res.output
    assert len(abdm.calls) == 0


# ---- worker -------------------------------------------------------------------------------------------------

def test_worker_run_once(run, app, http, hip):
    res = run("worker", "run", "--once")
    assert res.exit_code == 0 and "Ran 0 job(s)." in res.output


def test_worker_run_installs_signal_handlers_and_loops(run, monkeypatch):
    calls = {}
    monkeypatch.setattr(worker.Stopper, "install", lambda self: calls.setdefault("installed", True))
    monkeypatch.setattr(outbox_cli.worker_module, "run", lambda once, stopper: calls.setdefault("once", once) or 0)
    res = run("worker", "run")
    assert res.exit_code == 0 and calls == {"installed": True, "once": False}
    assert "Ran" not in res.output  # the long running worker prints nothing here


# ---- audit purge keeps recent / undelivered callbacks ------------------------------------------------------

def test_purge_removes_old_finished_callbacks_only(run, app):
    old = utcnow() - timedelta(days=app.config["CALLBACK_RETENTION_DAYS"] + 5)

    def event(request_id, status, created):
        row = CallbackEvent(request_id=request_id, path="api/v3/x", status=status, body_enc="x", created_at=created)
        db.session.add(row)
        db.session.flush()
        return row

    old_delivered = event("req-old-delivered", "delivered", old)
    old_unrouted = event("req-old-unrouted", "unrouted", old)
    old_pending = event("req-old-pending", "received", old)  # still being delivered: must stay
    recent = event("req-recent-00001", "delivered", utcnow())
    for row, status in ((old_delivered, "done"), (old_pending, "pending"), (recent, "done")):
        db.session.add(OutboxJob(kind="forward_callback", status=status, next_attempt_at=utcnow(),
                                 callback_event_id=row.id, created_at=old if row is not recent else utcnow()))
    db.session.commit()

    res = run("audit", "purge")
    assert res.exit_code == 0, res.output
    left = {e.request_id for e in CallbackEvent.query.all()}
    assert left == {"req-old-pending", "req-recent-00001"}
    assert {j.status for j in OutboxJob.query.all()} == {"pending", "done"}
    assert "2 callbacks and 1 finished jobs" in res.output
    assert old_unrouted is not None
