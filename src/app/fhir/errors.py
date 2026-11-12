from src.app.errors import AppError


class InvalidRecord(AppError):
    """The payload cannot become a valid ABDM record (missing data, bad PDF, i-medicus' own error body, ...)."""
    status = 422
    code = "INVALID_RECORD"
    default_message = "The record cannot be converted to FHIR"


class FhirBuildError(AppError):
    """We built a Bundle that failed our own validation: a bug on our side, not in the request."""
    status = 500
    code = "FHIR_BUILD_ERROR"
    default_message = "The FHIR bundle could not be built"
