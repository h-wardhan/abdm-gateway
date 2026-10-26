import signal

import responses

from src.app.extensions import db
from src.app.models import CallbackEvent, OutboxJob
from src.app.outbox import engine, worker
from tests.unit.test_outbox_handlers import TARGET, _queue


def test_run_once_processes_due_jobs_and_returns(app, http, hip):
    http.add(responses.POST, TARGET, status=200)
    event, job = _queue(hip)
    assert worker.run(once=True) == 1
    assert db.session.get(OutboxJob, job.id).status == "done"
    assert db.session.get(CallbackEvent, event.id).status == "delivered"


def test_loop_sleeps_when_idle_and_stops_on_request(app, monkeypatch):
    stopper = worker.Stopper()
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            stopper.stop()

    assert worker.run(stopper=stopper, sleep=fake_sleep) == 0
    assert sleeps == [app.config["OUTBOX_POLL_SECONDS"]] * 3


def test_loop_does_not_sleep_while_there_is_work(app, monkeypatch):
    stopper = worker.Stopper()
    sleeps = []
    passes = iter([2, 1, 0, 0])

    def fake_tick():
        done = next(passes)
        if done == 0:
            stopper.stop()
        return done

    monkeypatch.setattr(worker, "tick", fake_tick)
    assert worker.run(stopper=stopper, sleep=sleeps.append) == 3
    assert sleeps == []  # busy passes never sleep, and a stop request skips the last sleep


def test_a_failing_pass_is_logged_and_the_worker_survives(app, monkeypatch):
    def broken():
        raise RuntimeError("database went away")

    monkeypatch.setattr(engine, "process_due", broken)
    assert worker.tick() == 0


def test_stopper_installs_signal_handlers(monkeypatch):
    registered = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: registered.setdefault(sig, fn))
    stopper = worker.Stopper()
    stopper.install()
    assert set(registered) == {signal.SIGTERM, signal.SIGINT}
    assert stopper.stopped is False
    registered[signal.SIGTERM](signal.SIGTERM, None)
    assert stopper.stopped is True
