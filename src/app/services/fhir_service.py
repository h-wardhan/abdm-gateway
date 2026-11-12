"""FHIR conversion for the HIP: i-medicus' record payloads in, ABDM document Bundles out."""
import logging

from src.app import fhir
from src.app.fhir import validate

logger = logging.getLogger(__name__)


def convert(hi_type, payload, client):
    """The Bundle for one record of the client's facility (its HIP id is the Organization identifier)."""
    bundle = fhir.build_record(hi_type, payload, hip_id=getattr(client, "hip_id", None))
    logger.info("fhir: %s bundle built for client %s (%d entries)", hi_type, getattr(client, "id", None),
                len(bundle["entry"]))
    return bundle


def check(hi_type, bundle):
    """Validation result for a Bundle someone else made (used by the validate route)."""
    if hi_type not in validate.RECORDS:
        raise fhir.InvalidRecord(f"Unknown record type '{hi_type}'", code="UNKNOWN_RECORD_TYPE",
                                 details={"supported": list(fhir.RECORD_TYPES)})
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise fhir.InvalidRecord("`bundle` must be a FHIR Bundle", details={"field": "bundle"})
    problems = validate.issues(bundle, hi_type)
    return {"valid": not problems, "issues": problems, "igVersion": fhir.constants.IG_VERSION}
