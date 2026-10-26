"""
One row per callback ABDM sent to the gateway.

The body is health data, so it is stored encrypted (raw text, so it can be forwarded byte for byte).
(request_id, path) is unique: ABDM may deliver the same callback twice and the second one is dropped.

status: received (queued for delivery) | delivered | failed (delivery gave up) | unrouted (no HIP match)
auth_result: ok | skipped (mode off) | failed:<reason> (only kept in observe mode)
"""
from src.app.extensions import db
from src.app.models.base import TimestampMixin

RECEIVED = "received"
DELIVERED = "delivered"
FAILED = "failed"
UNROUTED = "unrouted"


class CallbackEvent(TimestampMixin, db.Model):
    __tablename__ = "callback_events"
    __table_args__ = (db.UniqueConstraint("request_id", "path", name="uq_callback_request_path"),)

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(64), nullable=False)
    path = db.Column(db.String(255), nullable=False)
    hip_id = db.Column(db.String(64), nullable=True, index=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="SET NULL"), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default=RECEIVED, index=True)
    auth_result = db.Column(db.String(120), nullable=True)
    body_enc = db.Column(db.Text, nullable=False)
    headers = db.Column(db.JSON, nullable=True)
    error = db.Column(db.Text, nullable=True)
    delivered_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<CallbackEvent {self.id} {self.path} {self.status}>"
