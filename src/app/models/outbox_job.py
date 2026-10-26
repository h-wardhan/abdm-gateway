"""
Retry queue for work that must survive failures: forwarding a callback to a client app, and later
replies the gateway sends to ABDM.

status: pending (waiting for next_attempt_at) | processing (claimed by a worker until locked_until)
        | done | dead (gave up, see last_error)
A worker claims a job with one atomic UPDATE, so several workers never run the same job.
"""
from src.app.extensions import db
from src.app.models.base import TimestampMixin

PENDING = "pending"
PROCESSING = "processing"
DONE = "done"
DEAD = "dead"

KIND_FORWARD_CALLBACK = "forward_callback"
KIND_ABDM_CALL = "abdm_call"


class OutboxJob(TimestampMixin, db.Model):
    __tablename__ = "outbox_jobs"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(12), nullable=False, default=PENDING, index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=6)
    next_attempt_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    last_status = db.Column(db.Integer, nullable=True)
    payload_enc = db.Column(db.Text, nullable=True)
    callback_event_id = db.Column(db.Integer, db.ForeignKey("callback_events.id", ondelete="CASCADE"), nullable=True, index=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="SET NULL"), nullable=True)

    def __repr__(self):
        return f"<OutboxJob {self.id} {self.kind} {self.status} attempts={self.attempts}>"
