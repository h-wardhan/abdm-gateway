"""
The outbox worker: runs due jobs in a loop. Start it next to the web app:

    flask worker run          # loops until stopped (SIGTERM / Ctrl+C finish the current batch first)
    flask worker run --once   # one pass, for cron and tests

Several workers can run at once; each job is claimed atomically (see engine.py).
"""
import logging
import signal
import time

from flask import current_app

from src.app.extensions import db
from src.app.outbox import engine

logger = logging.getLogger("abdm_gateway.worker")


class Stopper:
    """Flips to stopped on SIGTERM / SIGINT so the loop ends after the current batch."""

    def __init__(self):
        self.stopped = False

    def stop(self, *_args):
        self.stopped = True

    def install(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)


def tick():
    """One pass. Returns the number of jobs run; a failing pass is logged, never raised."""
    try:
        return engine.process_due()
    except Exception:  # noqa: BLE001  (database hiccup: keep the worker alive)
        db.session.rollback()
        logger.exception("Worker pass failed")
        return 0


def run(once=False, stopper=None, sleep=time.sleep):
    stopper = stopper or Stopper()
    poll = current_app.config["OUTBOX_POLL_SECONDS"]
    logger.info("Outbox worker started", extra={"once": once})
    total = 0
    while not stopper.stopped:
        done = tick()
        total += done
        if once:
            break
        if done == 0 and not stopper.stopped:
            sleep(poll)
    logger.info("Outbox worker stopped", extra={"jobs_run": total})
    return total
