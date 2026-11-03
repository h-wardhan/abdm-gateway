"""
M2 (HIP) state the gateway keeps itself.

HipReply   one row per answer sent to ABDM for a callback (kind + the callback's REQUEST-ID). A second answer for
           the same callback is a duplicate and is not sent again.
HipConsent one row per consent ABDM notified us about (consent/request/hip/notify). It is what a later
           health-information request is checked against: still GRANTED, not expired, inside the granted range.
"""
from src.app.extensions import db
from src.app.models.base import TimestampMixin

REPLY_SENT = "sent"
REPLY_QUEUED = "queued"


class HipReply(TimestampMixin, db.Model):
    __tablename__ = "hip_replies"
    __table_args__ = (db.UniqueConstraint("request_id", "kind", name="uq_hip_reply_request_kind"),)

    id = db.Column(db.Integer, primary_key=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    request_id = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=REPLY_SENT)


class HipConsent(TimestampMixin, db.Model):
    __tablename__ = "hip_consents"

    id = db.Column(db.Integer, primary_key=True)
    consent_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    hip_id = db.Column(db.String(64), nullable=True, index=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="SET NULL"), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False)  # GRANTED | REVOKED | EXPIRED | DENIED ... as ABDM says
    hi_types = db.Column(db.JSON, nullable=True)
    care_contexts = db.Column(db.JSON, nullable=True)  # [{patientReference, careContextReference}]
    date_from = db.Column(db.DateTime(timezone=True), nullable=True)
    date_to = db.Column(db.DateTime(timezone=True), nullable=True)
    erase_at = db.Column(db.DateTime(timezone=True), nullable=True)  # permission.dataEraseAt: consent ends here
