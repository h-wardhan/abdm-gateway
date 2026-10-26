from datetime import datetime, timezone

from src.app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    """SQLite hands timezone-aware columns back as naive datetimes; treat those as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
