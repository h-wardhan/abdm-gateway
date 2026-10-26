"""
  flask worker run [--once]
  flask outbox stats
  flask outbox list [--status dead] [--limit 20]
  flask outbox retry <job_id>
"""
import click
from flask.cli import AppGroup

from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.models import callback_event as ce
from src.app.models import outbox_job as oj
from src.app.models.base import utcnow
from src.app.outbox import engine
from src.app.outbox import worker as worker_module

worker_cli = AppGroup("worker", help="Background worker for callback delivery and retries.")
outbox_cli = AppGroup("outbox", help="Look at and manage the retry queue.")


@worker_cli.command("run")
@click.option("--once", is_flag=True, help="Run one pass and exit (cron, tests).")
def run_worker(once):
    stopper = worker_module.Stopper()
    if not once:
        stopper.install()
    total = worker_module.run(once=once, stopper=stopper)
    if once:
        click.echo(f"Ran {total} job(s).")


@outbox_cli.command("stats")
def stats():
    data = engine.stats()
    unrouted = CallbackEvent.query.filter_by(status=ce.UNROUTED).count()
    click.echo(f"pending   : {data['pending']}"
               + (f" (oldest waiting {data['oldestPendingSeconds']} s)" if data["oldestPendingSeconds"] is not None else ""))
    click.echo(f"processing: {data['processing']}")
    click.echo(f"dead      : {data['dead']}   (see: flask outbox list --status dead)")
    click.echo(f"unrouted callbacks: {unrouted}   (see: flask callbacks list --status unrouted)")


@outbox_cli.command("list")
@click.option("--status", type=click.Choice([oj.PENDING, oj.PROCESSING, oj.DONE, oj.DEAD]), default=None)
@click.option("--limit", default=20, show_default=True)
def list_jobs(status, limit):
    query = OutboxJob.query
    if status:
        query = query.filter_by(status=status)
    rows = query.order_by(OutboxJob.id.desc()).limit(limit).all()
    if not rows:
        click.echo("No jobs.")
        return
    click.echo(f"{'ID':<6} {'KIND':<17} {'STATUS':<11} {'TRIES':<6} {'EVENT':<7} LAST ERROR")
    for job in rows:
        click.echo(f"{job.id:<6} {job.kind:<17} {job.status:<11} {job.attempts}/{job.max_attempts:<4} "
                   f"{job.callback_event_id or '-':<7} {(job.last_error or '')[:60]}")


@outbox_cli.command("retry")
@click.argument("job_id", type=int)
def retry_job(job_id):
    """Give a dead job a fresh set of attempts (e.g. after the client app is fixed)."""
    job = db.session.get(OutboxJob, job_id)
    if job is None:
        raise click.ClickException(f"No job {job_id}")
    if job.status != oj.DEAD:
        raise click.ClickException(f"Job {job_id} is {job.status}; only dead jobs can be retried.")
    job.status = oj.PENDING
    job.attempts = 0
    job.last_error = None
    job.last_status = None
    job.locked_until = None
    job.next_attempt_at = utcnow()
    event = db.session.get(CallbackEvent, job.callback_event_id) if job.callback_event_id else None
    if event is not None:
        event.status = ce.RECEIVED
        event.error = None
    db.session.commit()
    click.secho(f"Job {job_id} will run again.", fg="green")
