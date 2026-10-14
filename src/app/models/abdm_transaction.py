"""
Which client started which ABDM transaction (txnId).

Every flow (enrolment, login) returns a txnId from ABDM. We record the owner so a client can
only continue its own transactions, never another tenant's.
"""
from src.app.extensions import db
from src.app.models.base import TimestampMixin


class AbdmTransaction(TimestampMixin, db.Model):
    __tablename__ = "abdm_transactions"

    id = db.Column(db.Integer, primary_key=True)
    txn_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    flow = db.Column(db.String(40), nullable=False)          # e.g. enrol_aadhaar, login_mobile, login_abha_address
    step = db.Column(db.String(40), nullable=False)          # last completed step, e.g. otp_sent, verified
    status = db.Column(db.String(20), nullable=False, default="active")  # active | completed | failed
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
