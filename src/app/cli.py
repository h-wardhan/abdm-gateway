"""
Management commands.

  flask clients create --name "i-medicus" [--hip-id X] [--rate-limit "300 per minute"] [--allowed-ips "1.2.3.4,5.6.7.8"]
  flask clients list
  flask clients rotate <client_id>
  flask clients disable <client_id>
  flask clients enable <client_id>
  flask audit purge [--days 90]
  flask hips ... / flask webhook ... / flask abdm ... (see callbacks/cli.py), flask worker run (see outbox/cli.py)
"""
from datetime import datetime, timedelta, timezone

import click
from flask import current_app
from flask.cli import AppGroup

from src.app.callbacks.cli import abdm_cli, callbacks_cli, hips_cli, webhook_cli
from src.app.extensions import db
from src.app.models import AbdmErrorLog, ApiAuditLog, ApiClient, CallbackEvent, OutboxJob
from src.app.outbox.cli import outbox_cli, worker_cli

clients_cli = AppGroup("clients", help="Manage client apps (tenants) allowed to call the gateway.")
audit_cli = AppGroup("audit", help="Audit log maintenance.")


def _get_client(client_id):
    client = ApiClient.query.filter_by(client_id=client_id).first()
    if client is None:
        raise click.ClickException(f"No client with client_id {client_id!r}")
    return client


def _print_credentials(client, secret):
    click.echo("")
    click.echo(f"  Name          : {client.name}")
    click.echo(f"  Client-Id     : {client.client_id}")
    click.echo(f"  Client-Secret : {secret}")
    click.echo("")
    click.secho("  Save the secret now. It is stored hashed and cannot be shown again.", fg="yellow")


@clients_cli.command("create")
@click.option("--name", required=True, help="Client app name, e.g. i-medicus")
@click.option("--hip-id", default=None, help="ABDM HIP / facility id of this client (used from M2 onwards)")
@click.option("--rate-limit", default=None, help='e.g. "300 per minute"; default DEFAULT_RATE_LIMIT')
@click.option("--allowed-ips", default=None, help="Comma-separated IP allowlist; default any IP")
def create_client(name, hip_id, rate_limit, allowed_ips):
    client, secret = ApiClient.create(name=name, hip_id=hip_id, rate_limit=rate_limit, allowed_ips=allowed_ips)
    db.session.add(client)
    db.session.commit()
    click.secho("Client created.", fg="green")
    _print_credentials(client, secret)


@clients_cli.command("list")
def list_clients():
    clients = ApiClient.query.order_by(ApiClient.id).all()
    if not clients:
        click.echo("No clients yet. Create one with: flask clients create --name <name>")
        return
    click.echo(f"{'ID':<4} {'CLIENT-ID':<34} {'NAME':<20} {'ACTIVE':<7} {'RATE LIMIT':<18} LAST USED")
    for c in clients:
        click.echo(
            f"{c.id:<4} {c.client_id:<34} {c.name[:20]:<20} {('yes' if c.is_active else 'no'):<7} "
            f"{(c.rate_limit or 'default'):<18} {c.last_used_at or '-'}"
        )


@clients_cli.command("rotate")
@click.argument("client_id")
def rotate_client(client_id):
    """Issue a new secret. Tokens issued before this stop working immediately."""
    client = _get_client(client_id)
    secret = client.rotate_secret()
    db.session.commit()
    click.secho("Secret rotated; existing tokens are revoked.", fg="green")
    _print_credentials(client, secret)


@clients_cli.command("disable")
@click.argument("client_id")
def disable_client(client_id):
    client = _get_client(client_id)
    client.is_active = False
    client.token_version += 1
    db.session.commit()
    click.secho(f"{client_id} disabled; existing tokens are revoked.", fg="green")


@clients_cli.command("enable")
@click.argument("client_id")
def enable_client(client_id):
    client = _get_client(client_id)
    client.is_active = True
    db.session.commit()
    click.secho(f"{client_id} enabled.", fg="green")


@audit_cli.command("purge")
@click.option("--days", type=int, default=None, help="Keep this many days (default AUDIT_LOG_RETENTION_DAYS)")
def purge_audit(days):
    days = days or current_app.config["AUDIT_LOG_RETENTION_DAYS"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    audit = ApiAuditLog.query.filter(ApiAuditLog.created_at < cutoff).delete(synchronize_session=False)
    errors = AbdmErrorLog.query.filter(AbdmErrorLog.created_at < cutoff).delete(synchronize_session=False)
    # Callbacks hold health data: keep them only CALLBACK_RETENTION_DAYS, and never delete one still being delivered
    callback_cutoff = datetime.now(timezone.utc) - timedelta(days=current_app.config["CALLBACK_RETENTION_DAYS"])
    jobs = OutboxJob.query.filter(OutboxJob.created_at < callback_cutoff, OutboxJob.status.in_(("done", "dead")))         .delete(synchronize_session=False)
    events = CallbackEvent.query.filter(CallbackEvent.created_at < callback_cutoff,
                                        CallbackEvent.status != "received").delete(synchronize_session=False)
    db.session.commit()
    click.echo(f"Removed {audit} audit rows and {errors} ABDM error rows older than {days} days, "
               f"{events} callbacks and {jobs} finished jobs older than "
               f"{current_app.config['CALLBACK_RETENTION_DAYS']} days.")


def register_cli(app):
    app.cli.add_command(clients_cli)
    app.cli.add_command(audit_cli)
    app.cli.add_command(hips_cli)
    app.cli.add_command(webhook_cli)
    app.cli.add_command(callbacks_cli)
    app.cli.add_command(outbox_cli)
    app.cli.add_command(abdm_cli)
    app.cli.add_command(worker_cli)
