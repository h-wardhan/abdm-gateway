"""
Exception hierarchy + global error handlers.

Raise an AppError subclass anywhere (routes, services, ABDM client); the handler turns it into the
standard error envelope. Anything else becomes a generic 500 with the requestId, and the stack trace
goes only to the log.
"""
import logging

from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import HTTPException

from src.app.common.response import error_body

logger = logging.getLogger(__name__)


class AppError(Exception):
    status = 400
    code = "BAD_REQUEST"
    default_message = "Bad request"

    def __init__(self, message=None, code=None, status=None, details=None):
        self.message = message or self.default_message
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details
        super().__init__(self.message)


class ValidationError(AppError):
    status = 400
    code = "VALIDATION_ERROR"
    default_message = "Invalid request"


class AuthError(AppError):
    status = 401
    code = "UNAUTHORIZED"
    default_message = "Authentication required"


class ForbiddenError(AppError):
    status = 403
    code = "FORBIDDEN"
    default_message = "You are not allowed to perform this action"


class NotFoundError(AppError):
    status = 404
    code = "NOT_FOUND"
    default_message = "Resource not found"


class ConflictError(AppError):
    status = 409
    code = "CONFLICT"
    default_message = "The request conflicts with another request"


class RateLimitedError(AppError):
    status = 429
    code = "RATE_LIMITED"
    default_message = "Too many requests. Please try again later."


class AbdmUpstreamError(AppError):
    """ABDM answered with an error. Keeps ABDM's HTTP status, error code and message as-is,
    because client apps match on that text (e.g. 'UIDAI Error code : 111', 'request for new OTP after 30 seconds')."""

    status = 502
    code = "ABDM_ERROR"
    default_message = "ABDM returned an error"


class AbdmUnavailableError(AppError):
    status = 503
    code = "ABDM_UNAVAILABLE"
    default_message = "ABDM is not reachable right now. Please try again after some time."


def _app_error_response(err):
    if err.status >= 500:
        logger.error("Request failed", extra={"error_code": err.code, "status": err.status, "error": err.message})
    else:
        logger.info("Request rejected", extra={"error_code": err.code, "status": err.status, "error": err.message})
    return error_body(err.code, err.message, err.details), err.status


def _http_error_response(err):
    code = (err.name or "HTTP_ERROR").upper().replace(" ", "_")
    return error_body(code, err.description or err.name), err.code or 500


def _rate_limit_response(err):
    return error_body("RATE_LIMITED", f"Too many requests ({err.description}). Please try again later."), 429


def _unhandled_response(err):
    logger.exception("Unhandled error")
    return error_body("INTERNAL_ERROR", "Something went wrong. Please contact support with the requestId."), 500


def register_error_handlers(app, api):
    """Register on both the Flask app (non-restx routes) and the restx Api (namespaced routes)."""
    # restx calls app.log_exception() with a full traceback for every 5xx, even after our handler
    # ran. Our handlers already log (with traceback for real bugs), so that second log is just noise
    # for expected upstream errors like ABDM_AUTH_FAILED.
    app.log_exception = lambda exc_info: None

    for exc, fn in (
        (AppError, _app_error_response),
        (RateLimitExceeded, _rate_limit_response),
        (HTTPException, _http_error_response),
        (Exception, _unhandled_response),
    ):
        app.register_error_handler(exc, fn)
        api.errorhandler(exc)(fn)
