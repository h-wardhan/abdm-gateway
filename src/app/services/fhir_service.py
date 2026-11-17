"""FHIR conversion for the HIP: i-medicus' record payloads in, ABDM document Bundles out."""
import logging

from src.app import fhir
from src.app.fhir import validate

logger = logging.getLogger(__name__)


def facility_of(client):
    """The HIP id of the client's facility: the one on the client, else the only facility registered for it (a clinic registers
    itself through ABDM's callbacks, so the client row often has none). Several facilities and none set: None, the payload's
    own `organization.facilityId` is then used."""
    hip_id = getattr(client, "hip_id", None)
    if hip_id or getattr(client, "id", None) is None:
        return hip_id
    from src.app.models import HipRegistration  # late: models import services
    rows = HipRegistration.query.filter_by(api_client_id=client.id, is_active=True).limit(2).all()
    return rows[0].hip_id if len(rows) == 1 else None


def convert(hi_type, payload, client):
    """The Bundle for one record of the client's facility (its HIP id is the Organization identifier)."""
    bundle = fhir.build_record(hi_type, payload, hip_id=facility_of(client))
    logger.info("fhir: %s bundle built for client %s (%d entries)", hi_type, getattr(client, "id", None),
                len(bundle["entry"]))
    return bundle


def legacy_prescription_payload(data, client):
    """The payload of the prescription builder from the body i-medicus' video-call flow sent VCDoctor's
    `ConvertPrescription`: `{prescriptionId, date, patient{id, name}, doctor{id, name}, medicines[{name, dosage,
    frequency, duration, instructions}]}`. That body has no ABHA address, licence number or clinic name, so the
    patient's and doctor's own ids stand in for the identifier values (an optional `healthId` / `licenseNumber` in the
    body wins) and the clinic is the client."""
    if not isinstance(data, dict):
        raise fhir.InvalidRecord("The body must be a JSON object")
    patient, doctor = data.get("patient") or {}, data.get("doctor") or {}
    if not isinstance(patient, dict) or not isinstance(doctor, dict):
        raise fhir.InvalidRecord("patient and doctor must be objects", details={"field": "patient"})
    medications = []
    for med in data.get("medicines") or []:
        if not isinstance(med, dict):
            continue
        parts = [f"Qty {med['dosage']}" if med.get("dosage") not in (None, "") else "",
                 f"Frequency {med['frequency']}" if med.get("frequency") not in (None, "") else "",
                 f"for {med['duration']} days" if med.get("duration") not in (None, "") else "",
                 str(med.get("instructions") or "")]
        medications.append({"display": str(med.get("name") or "").strip() or None,
                            "dosageText": " ".join(p for p in parts if p) or None,
                            "routeCode": "26643006", "routeDisplay": "Oral"})
    return {
        "medications": medications,
        "patient": {"name": patient.get("name"), "healthId": patient.get("healthId") or patient.get("id"),
                    "gender": patient.get("gender"), "birthDate": patient.get("birthDate")},
        "practitioner": {"name": doctor.get("name"), "licenseNumber": doctor.get("licenseNumber") or doctor.get("id")},
        "organization": {"name": getattr(client, "name", None)},
        "encounter": {"encounterId": data.get("prescriptionId"), "classCode": "AMB", "classDisplay": "Ambulatory"},
    }


def convert_legacy_prescription(data, client):
    payload = legacy_prescription_payload(data, client)
    when = fhir.util.parse(data.get("date")) if isinstance(data, dict) else None
    bundle = fhir.build_record("Prescription", payload, hip_id=facility_of(client), when=when)
    logger.info("fhir: legacy prescription bundle built for client %s (%d entries)", getattr(client, "id", None),
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
