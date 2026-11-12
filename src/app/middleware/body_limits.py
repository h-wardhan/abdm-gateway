"""
Per-route request size limits.

The app-wide MAX_CONTENT_LENGTH (1 MB) fits every ABDM call. The FHIR record routes (and the push of the encrypted records) are the exception:
i-medicus sends each record's PDF as base64 inside the JSON, so they get FHIR_MAX_CONTENT_LENGTH instead. A reverse proxy in
front of the gateway needs a limit at least that high (nginx: client_max_body_size).
"""
from flask import request

# the FHIR routes (Bundles with PDFs, to encrypt / decrypt) and the push of the encrypted Bundles to the HIU
BIG_BODY_PREFIXES = ("/api/v1/fhir/", "/api/v1/hip/health-information/push")


def init_body_limits(app):
    @app.before_request
    def _fhir_body_limit():
        if request.path.startswith(BIG_BODY_PREFIXES):
            request.max_content_length = app.config["FHIR_MAX_CONTENT_LENGTH"]
