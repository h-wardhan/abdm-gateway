"""
FHIR document Bundles for ABDM health records (NRCeS ABDM FHIR R4 Implementation Guide, see `constants.IG_VERSION`).

    build_record("Prescription", payload, hip_id)  ->  Bundle (a dict, ready to be JSON-encoded)

`payload` is what i-medicus' WebhookTrait builds for that record type (the contract it had with VCDoctor).
"""
import logging

from src.app.fhir import validate
from src.app.fhir.errors import FhirBuildError, InvalidRecord
from src.app.fhir.records import BUILDERS

logger = logging.getLogger(__name__)

RECORD_TYPES = tuple(BUILDERS)


def build_record(hi_type, payload, hip_id=None, when=None, check=True):
    """Bundle for one record. InvalidRecord (HTTP 422) when the payload cannot become one; FhirBuildError (500) when
    our own output fails validation."""
    if hi_type not in BUILDERS:
        raise InvalidRecord(f"Unknown record type '{hi_type}'", code="UNKNOWN_RECORD_TYPE",
                            details={"supported": list(RECORD_TYPES)})
    bundle = BUILDERS[hi_type](payload, hip_id, when)
    if check:
        problems = validate.issues(bundle, hi_type)
        if problems:
            logger.error("fhir: %s bundle failed validation: %s", hi_type, problems[:10])
            raise FhirBuildError(details={"record": hi_type, "issues": problems[:20]})
    return bundle
