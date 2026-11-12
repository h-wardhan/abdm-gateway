"""PrescriptionRecord: MedicationRequest per medicine + the prescription PDF as a Binary."""
from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.core import DocumentBuilder
from src.app.fhir.errors import InvalidRecord
from src.app.fhir.records import _common

CODE = u.coding(c.SNOMED, "440545006", "Prescription record")


def build(payload, hip_id, when=None):
    b = DocumentBuilder(payload, hip_id, when)
    refs = [b.typed(r, "MedicationRequest")
            for r in (_common.medication_request(b, m) for m in u.items(payload, "medications")) if r]
    if payload.get("binaryDocument"):
        refs.append(b.typed(b.binary(payload["binaryDocument"], "binaryDocument", "Prescription"), "Binary"))
    if not refs:
        raise InvalidRecord("The prescription has no medicines and no document", details={"field": "medications"})
    return b.finish("PrescriptionRecord", u.concept(CODE, text="Prescription record"), "Prescription record",
                    [("Prescription record", CODE, refs)])
