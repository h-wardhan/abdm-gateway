"""
Which client app owns which HIP (facility) and where ABDM callbacks for it must be delivered.

One client can own many HIPs (i-medicus has one per clinic). ABDM callbacks carry the facility id,
the gateway looks it up here and forwards the callback to `callback_url`.
The webhook secret signs what the gateway forwards; it is stored encrypted because it must be
readable again to sign.
"""
from sqlalchemy import false as sa_false

from src.app.extensions import db
from src.app.models.base import TimestampMixin


class HipRegistration(TimestampMixin, db.Model):
    __tablename__ = "hip_registrations"

    id = db.Column(db.Integer, primary_key=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    hip_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    name = db.Column(db.String(120), nullable=True)
    callback_url = db.Column(db.String(500), nullable=False)
    webhook_secret_enc = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # Created by the gateway from the client's default webhook (first callback of a new clinic); it follows
    # that webhook when the client changes it. Registered by hand (False): own URL and secret.
    auto_registered = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())

    api_client = db.relationship("ApiClient", backref=db.backref("hips", lazy="dynamic"))

    def __repr__(self):
        return f"<HipRegistration {self.hip_id} active={self.is_active}>"
