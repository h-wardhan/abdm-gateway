"""
  flask hips add --client <client_id> --hip-id <id> --callback-url <url> [--name N]
  flask hips list
  flask hips set-url <hip_id> <callback_url>
  flask hips rotate-secret <hip_id>
  flask hips disable <hip_id>
  flask callbacks list [--status unrouted] [--limit 20]
  flask callbacks reroute <event_id> --hip-id <id>
  flask abdm bridge-url set [--url URL] [--confirm]
  flask abdm services add --hip-id <id> --name <name> [--type HIP] [--confirm]

The `abdm` commands change settings on ABDM itself, so they only work against the SANDBOX
(ABDM_ENV=sbx), refuse the production host, and just print what they would send unless --confirm is given.
"""
import json
from urllib.parse import urlsplit

import click
from flask import current_app
from flask.cli import AppGroup

from src.app.abdm.http import abdm_request
from src.app.callbacks import registry
from src.app.errors import AppError
from src.app.extensions import db
from src.app.models import ApiClient, CallbackEvent, HipRegistration
from src.app.models import callback_event as ce
from src.app.models import outbox_job as oj
from src.app.outbox import engine

hips_cli = AppGroup("hips", help="Facilities (HIPs) and where their ABDM callbacks are delivered.")
callbacks_cli = AppGroup("callbacks", help="Callbacks received from ABDM.")
abdm_cli = AppGroup("abdm", help="Settings on the ABDM sandbox (dry run unless --confirm).")
bridge_cli = AppGroup("bridge-url", help="The URL ABDM sends callbacks to.")
services_cli = AppGroup("services", help="Facilities linked to our bridge on ABDM.")
abdm_cli.add_command(bridge_cli)
abdm_cli.add_command(services_cli)

BRIDGE_URL_PATH = "/api/hiecm/gateway/v3/bridge/url"
# Confirm the exact path in the ABDM sandbox docs before the first --confirm
SERVICES_PATH = "/devservice/v1/bridges/addUpdateServices"
PRODUCTION_HOSTS = ("live.abdm.gov.in", "abha.abdm.gov.in")


def _client(client_id):
    client = ApiClient.query.filter_by(client_id=client_id).first()
    if client is None:
        raise click.ClickException(f"No client with client_id {client_id!r}")
    return client


def _hip(hip_id):
    reg = registry.get(hip_id)
    if reg is None:
        raise click.ClickException(f"No HIP {hip_id!r}")
    return reg


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AppError as exc:
        raise click.ClickException(exc.message)


@hips_cli.command("add")
@click.option("--client", "client_id", required=True, help="client_id from `flask clients list`")
@click.option("--hip-id", required=True, help="Facility (HFR) id, e.g. IN0110005723")
@click.option("--callback-url", required=True, help="Client app webhook, e.g. https://dev.example.com/api/abdm-webhook")
@click.option("--name", default=None)
def add_hip(client_id, hip_id, callback_url, name):
    reg, secret = _run(registry.upsert, _client(client_id), hip_id, callback_url, name)
    click.secho(f"HIP {reg.hip_id} saved.", fg="green")
    click.echo(f"  Callback URL   : {reg.callback_url}")
    if secret:
        click.echo(f"  Webhook secret : {secret}")
        click.secho("  Save the secret now; it is only shown once. Use it to verify X-Gateway-Signature.", fg="yellow")


@hips_cli.command("list")
def list_hips():
    rows = HipRegistration.query.order_by(HipRegistration.id).all()
    if not rows:
        click.echo("No HIPs yet. Add one with: flask hips add --client <client_id> --hip-id <id> --callback-url <url>")
        return
    click.echo(f"{'HIP ID':<24} {'CLIENT':<30} {'ACTIVE':<7} CALLBACK URL")
    for reg in rows:
        click.echo(f"{reg.hip_id:<24} {reg.api_client.client_id:<30} {('yes' if reg.is_active else 'no'):<7} {reg.callback_url}")


@hips_cli.command("set-url")
@click.argument("hip_id")
@click.argument("callback_url")
def set_url(hip_id, callback_url):
    reg = _hip(hip_id)
    _run(registry.upsert, reg.api_client, hip_id, callback_url)
    click.secho(f"{hip_id} now delivers to {callback_url.rstrip('/')}", fg="green")


@hips_cli.command("rotate-secret")
@click.argument("hip_id")
def rotate_secret(hip_id):
    secret = registry.rotate_secret(_hip(hip_id))
    click.secho("Webhook secret rotated.", fg="green")
    click.echo(f"  Webhook secret : {secret}")


@hips_cli.command("disable")
@click.argument("hip_id")
def disable_hip(hip_id):
    registry.disable(_hip(hip_id))
    click.secho(f"{hip_id} disabled; its callbacks are no longer delivered.", fg="green")


@callbacks_cli.command("list")
@click.option("--status", type=click.Choice([ce.RECEIVED, ce.DELIVERED, ce.FAILED, ce.UNROUTED]), default=None)
@click.option("--limit", default=20, show_default=True)
def list_callbacks(status, limit):
    query = CallbackEvent.query
    if status:
        query = query.filter_by(status=status)
    rows = query.order_by(CallbackEvent.id.desc()).limit(limit).all()
    if not rows:
        click.echo("No callbacks.")
        return
    click.echo(f"{'ID':<6} {'STATUS':<10} {'HIP ID':<20} {'PATH':<40} DETAIL")
    for event in rows:
        click.echo(f"{event.id:<6} {event.status:<10} {(event.hip_id or '-'):<20} {event.path[:40]:<40} "
                   f"{(event.error or '')[:50]}")


@callbacks_cli.command("reroute")
@click.argument("event_id", type=int)
@click.option("--hip-id", required=True, help="Registered HIP that should receive this callback")
def reroute(event_id, hip_id):
    """Deliver a callback that was `unrouted` (no matching HIP when it arrived), now that the HIP is registered."""
    event = db.session.get(CallbackEvent, event_id)
    if event is None:
        raise click.ClickException(f"No callback {event_id}")
    if event.status != ce.UNROUTED:
        raise click.ClickException(f"Callback {event_id} is {event.status}; only unrouted callbacks can be re-routed "
                                   "(for failed ones use `flask outbox retry <job_id>`).")
    reg = _hip(hip_id)
    if not reg.is_active:
        raise click.ClickException(f"HIP {hip_id!r} is disabled")
    event.hip_id = reg.hip_id
    event.api_client_id = reg.api_client_id
    event.status = ce.RECEIVED
    event.error = None
    engine.enqueue(oj.KIND_FORWARD_CALLBACK, callback_event_id=event.id, api_client_id=reg.api_client_id)
    db.session.commit()
    click.secho(f"Callback {event_id} queued for {reg.hip_id}.", fg="green")


def _guard_sandbox(target_url):
    if current_app.config["ABDM_ENV"] != "sbx":
        raise click.ClickException("Refusing: this command only works against the ABDM sandbox (ABDM_ENV=sbx).")
    if (urlsplit(target_url).hostname or "") in PRODUCTION_HOSTS:
        raise click.ClickException("Refusing: the ABDM URL is a production host.")


def _send(method, path, payload, confirm):
    target = current_app.config["ABDM_GATEWAY_URL"] + path
    _guard_sandbox(target)
    click.echo(f"{method} {target}")
    click.echo(json.dumps(payload, indent=2))
    if not confirm:
        click.secho("Dry run: nothing was sent. Add --confirm to send it to the ABDM sandbox.", fg="yellow")
        return
    result = abdm_request(method, path, target="gateway", json=payload, endpoint_name=path)
    click.secho("Sent to ABDM sandbox.", fg="green")
    click.echo(json.dumps(result, indent=2) if result else "(empty response)")


@bridge_cli.command("set")
@click.option("--url", default=None, help="Defaults to GATEWAY_PUBLIC_URL")
@click.option("--confirm", is_flag=True)
def set_bridge_url(url, confirm):
    url = (url or current_app.config.get("GATEWAY_PUBLIC_URL") or "").strip()
    if not url.startswith("https://"):
        raise click.ClickException("Give an https URL with --url, or set GATEWAY_PUBLIC_URL.")
    _send("PATCH", BRIDGE_URL_PATH, {"url": url}, confirm)


@services_cli.command("add")
@click.option("--hip-id", required=True)
@click.option("--name", required=True)
@click.option("--type", "service_type", default="HIP", type=click.Choice(["HIP", "HIU"]))
@click.option("--confirm", is_flag=True)
def add_service(hip_id, name, service_type, confirm):
    payload = [{"id": hip_id, "name": name, "type": service_type, "active": True, "alias": [hip_id]}]
    _send("PUT", SERVICES_PATH, payload, confirm)
