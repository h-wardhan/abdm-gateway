"""
Audit trail. All payloads are masked before they are stored (see src/utils/masking.py).
"""
from src.app.extensions import db
from src.app.models.base import utcnow


class ApiAuditLog(db.Model):
    """One row per API request (excluding health checks and Swagger)."""

    __tablename__ = "api_audit_logs"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    request_id = db.Column(db.String(64), nullable=False, index=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="SET NULL"), nullable=True, index=True)
    method = db.Column(db.String(10), nullable=False)
    route = db.Column(db.String(255), nullable=False)
    http_status = db.Column(db.Integer, nullable=False)
    error_code = db.Column(db.String(64), nullable=True)
    abdm_endpoint = db.Column(db.String(255), nullable=True)
    abdm_status = db.Column(db.Integer, nullable=True)
    latency_ms = db.Column(db.Float, nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    request_body = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class AbdmErrorLog(db.Model):
    """Raw ABDM error per failed upstream call, for support and debugging."""

    __tablename__ = "abdm_error_logs"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    request_id = db.Column(db.String(64), nullable=True, index=True)
    api_client_id = db.Column(db.Integer, db.ForeignKey("api_clients.id", ondelete="SET NULL"), nullable=True, index=True)
    abdm_endpoint = db.Column(db.String(255), nullable=False)
    http_status = db.Column(db.Integer, nullable=True)
    abdm_code = db.Column(db.String(64), nullable=True)
    abdm_message = db.Column(db.Text, nullable=True)
    raw_body = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
