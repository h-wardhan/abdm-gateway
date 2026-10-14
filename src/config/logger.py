"""
Logging setup.

- JSON lines on stderr and in LOG_DIR/app.log
- Outbound ABDM calls (logger "abdm.http") also go to LOG_DIR/abdm.log
- Every record carries request_id / client_id and is passed through the masking filter,
  so Aadhaar, OTP, mobile numbers and tokens never land in a log file.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from flask import g, has_request_context

from src.utils.masking import mask_payload, mask_text

ABDM_LOGGER = "abdm.http"

NOISY_LIBRARIES = ("sqlalchemy", "sqlalchemy.engine", "urllib3", "werkzeug", "alembic")

_STANDARD_ATTRS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "request_id", "client_id"}


class RequestContextFilter(logging.Filter):
    """Attach request_id / client_id from flask.g (or '-' outside a request)."""

    def filter(self, record):
        if has_request_context():
            record.request_id = getattr(g, "request_id", "-")
            client = getattr(g, "client", None)
            record.client_id = getattr(client, "client_id", "-") if client else "-"
        else:
            record.request_id = getattr(record, "request_id", "-")
            record.client_id = getattr(record, "client_id", "-")
        return True


class MaskingFilter(logging.Filter):
    """Mask sensitive values in the message and in any `extra=` fields."""

    def filter(self, record):
        record.msg = mask_text(record.getMessage())
        record.args = ()
        for key, value in list(record.__dict__.items()):
            if key not in _STANDARD_ATTRS and isinstance(value, (dict, list, str)):
                setattr(record, key, mask_payload(value))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "client_id": getattr(record, "client_id", "-"),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def _resolve_level(value, fallback=logging.INFO):
    if isinstance(value, int):
        return value
    level = logging.getLevelName(str(value).strip().upper())
    return level if isinstance(level, int) else fallback


def _file_handler(log_dir, filename):
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, filename)
        if os.name == "nt":
            # Windows file locks break RotatingFileHandler rollover
            return logging.FileHandler(path, delay=True, encoding="utf-8")
        return RotatingFileHandler(path, maxBytes=10_000_000, backupCount=10, delay=True, encoding="utf-8")
    except OSError as exc:
        print(f"Could not open log directory {log_dir!r}: {exc}", file=sys.stderr)
        return None


def _prepare(handler, level):
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    handler.addFilter(MaskingFilter())
    return handler


def setup_logging(app):
    level = _resolve_level(app.config.get("LOG_LEVEL", "INFO"))
    lib_level = _resolve_level(app.config.get("LOG_LEVEL_LIBS", "WARNING"), logging.WARNING)
    log_dir = app.config.get("LOG_DIR")

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
        existing.close()
    root.setLevel(level)
    root.addHandler(_prepare(logging.StreamHandler(sys.stderr), level))

    abdm_logger = logging.getLogger(ABDM_LOGGER)
    for existing in abdm_logger.handlers[:]:
        abdm_logger.removeHandler(existing)
        existing.close()

    if log_dir:
        app_file = _file_handler(log_dir, "app.log")
        if app_file:
            root.addHandler(_prepare(app_file, level))
        abdm_file = _file_handler(log_dir, "abdm.log")
        if abdm_file:
            abdm_logger.addHandler(_prepare(abdm_file, level))

    app.logger.handlers.clear()
    app.logger.propagate = True
    app.logger.setLevel(logging.NOTSET)

    for name in NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(max(level, lib_level))

    app.logger.info(
        "Logging configured",
        extra={"level": logging.getLevelName(level), "log_dir": os.path.abspath(log_dir) if log_dir else None},
    )
