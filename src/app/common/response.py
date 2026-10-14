"""
The single response envelope used by every endpoint.

success: {"success": true,  "data": {...}, "requestId": "..."}
error:   {"success": false, "code": "error", "message": {"error": {"code": "...", "message": "..."}}, "requestId": "..."}

The error shape is what existing clients (i-medicus) already parse: they check `code == "error"`
and read `message.error.message` (see AbdmErrorTranslator::parse in i-medicus).
"""
from flask import g, has_request_context


def current_request_id():
    return getattr(g, "request_id", None) if has_request_context() else None


def success(data=None, status=200, message=None):
    body = {"success": True, "data": data if data is not None else {}}
    if message:
        body["message"] = message
    body["requestId"] = current_request_id()
    return body, status


def from_abdm(result):
    """Envelope for an ABDM result.

    - ABDM's `message` is also copied to the top level (clients show it in toasts).
    - A 200 with `authResult: failed` (wrong OTP on login verify) becomes success=false, HTTP 200,
      data kept as-is, which is how i-medicus detects it.
    """
    message = result.get("message") if isinstance(result, dict) else None
    if isinstance(result, dict) and str(result.get("authResult", "")).lower() == "failed":
        body = {"success": False, "data": result, "message": message or "OTP verification failed",
                "requestId": current_request_id()}
        return body, 200
    return success(result, message=message)


def error_body(code, message, details=None):
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {
        "success": False,
        "code": "error",
        "message": {"error": error},
        "requestId": current_request_id(),
    }


def error(code, message, status=400, details=None):
    return error_body(code, message, details), status
