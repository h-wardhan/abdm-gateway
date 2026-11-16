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


class HipLinkToken(TimestampMixin, db.Model):
    """The link token ABDM issued for a patient at a facility (callback `hip/token/on-generate-token`). The client app asks
    for it and then uses it; i-medicus asks and uses it at once, before the callback arrived, and asks again when that
    fails (ABDM answers "Duplicate Link token request"). With the token here the gateway uses the one ABDM issued, waits
    for it when it is on its way, and does not ask ABDM twice."""
    __tablename__ = "hip_link_tokens"
    __table_args__ = (db.UniqueConstraint("hip_id", "abha_address", name="uq_hip_link_token"),)

    id = db.Column(db.Integer, primary_key=True)
    hip_id = db.Column(db.String(64), nullable=False, index=True)
    abha_address = db.Column(db.String(255), nullable=False)
    token_enc = db.Column(db.Text, nullable=True)                 # sealed like callback bodies
    issued_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)   # `exp` of the token
    requested_at = db.Column(db.DateTime(timezone=True), nullable=True)  # when we last asked ABDM for one
    callback_event_id = db.Column(db.Integer, db.ForeignKey("callback_events.id", ondelete="SET NULL"), nullable=True)


# HipTransfer.status
TRANSFER_ACKNOWLEDGED = "ACKNOWLEDGED"  # the health-information request was acknowledged; nothing sent yet
TRANSFER_PUSH_QUEUED = "PUSH_QUEUED"    # the HIU could not be reached; the worker retries the push
TRANSFER_PUSHED = "PUSHED"              # the encrypted records reached the HIU
TRANSFER_NOTIFIED = "NOTIFIED"          # ABDM was told the final status (TRANSFERRED)
TRANSFER_FAILED = "FAILED"              # gave up (ABDM was told FAILED)


class HipTransfer(TimestampMixin, db.Model):
    """One health-information transfer (ABDM transactionId) the facility must answer: the trail of the push to the
    HIU and of the status notification to ABDM. Created when the health-information request is acknowledged, so the
    push URL and the HIU's key expiry are the ones ABDM gave for this transaction, not what a client says later."""
    __tablename__ = "hip_transfers"
    __table_args__ = (db.UniqueConstraint("api_client_id", "transaction_id", name="uq_hip_transfer_client_txn"),)

    id = db.Column(db.Integer, primary_key=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    hip_id = db.Column(db.String(64), nullable=False, index=True)
    transaction_id = db.Column(db.String(64), nullable=False, index=True)
    consent_id = db.Column(db.String(64), nullable=True, index=True)
    request_id = db.Column(db.String(64), nullable=True)           # REQUEST-ID of the health-information request callback
    data_push_url = db.Column(db.Text, nullable=True)
    key_expiry = db.Column(db.DateTime(timezone=True), nullable=True)
    hiu_public_key = db.Column(db.Text, nullable=True)             # the HIU's key of this request (canonical X.509)
    hiu_nonce = db.Column(db.String(64), nullable=True)
    sender_public_key = db.Column(db.Text, nullable=True)          # what the Encrypt call for that key answered
    sender_nonce = db.Column(db.String(64), nullable=True)         # (`keyToShare` and the sender nonce used)
    status = db.Column(db.String(20), nullable=False, default=TRANSFER_ACKNOWLEDGED, index=True)
    care_contexts = db.Column(db.JSON, nullable=True)              # care contexts pushed: [careContextReference, ...]
    pending_notify = db.Column(db.JSON, nullable=True)             # notification waiting for a queued push to land
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    pushed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    notified_at = db.Column(db.DateTime(timezone=True), nullable=True)


class HiuConsent(TimestampMixin, db.Model):
    """M3: a consent this facility asked a patient for (as HIU), kept from ABDM's callbacks: `hiu/consent/request/notify`
    (status + artefact ids) and `hiu/consent/on-fetch` (the artefact: granted range, hi types, erase date). It is what a
    health-information request is checked against, so an expired, revoked or denied consent never reaches ABDM."""
    __tablename__ = "hiu_consents"

    id = db.Column(db.Integer, primary_key=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    hiu_id = db.Column(db.String(64), nullable=False, index=True)
    consent_request_id = db.Column(db.String(64), nullable=True, index=True)
    consent_id = db.Column(db.String(64), nullable=True, unique=True, index=True)   # the artefact id (set once granted)
    status = db.Column(db.String(20), nullable=False)  # REQUESTED | GRANTED | DENIED | REVOKED | EXPIRED as ABDM says
    hip_id = db.Column(db.String(64), nullable=True)
    hi_types = db.Column(db.JSON, nullable=True)
    date_from = db.Column(db.DateTime(timezone=True), nullable=True)
    date_to = db.Column(db.DateTime(timezone=True), nullable=True)
    erase_at = db.Column(db.DateTime(timezone=True), nullable=True)                  # permission.dataEraseAt


HIU_TRANSFER_REQUESTED = "REQUESTED"      # our health-information request went to ABDM
HIU_TRANSFER_ACKNOWLEDGED = "ACKNOWLEDGED"  # ABDM answered `on-request` with the transactionId


class HiuTransfer(TimestampMixin, db.Model):
    """M3: one health-information request we made. The HIP pushes the records straight to the data-push URL, so the
    gateway sees only the request and ABDM's `hiu/health-information/on-request` answer (the transactionId)."""
    __tablename__ = "hiu_transfers"

    id = db.Column(db.Integer, primary_key=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    hiu_id = db.Column(db.String(64), nullable=False, index=True)
    request_id = db.Column(db.String(64), nullable=False, unique=True)   # REQUEST-ID of our request: the answer carries it
    consent_id = db.Column(db.String(64), nullable=False, index=True)
    transaction_id = db.Column(db.String(64), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default=HIU_TRANSFER_REQUESTED)
    session_status = db.Column(db.String(20), nullable=True)
