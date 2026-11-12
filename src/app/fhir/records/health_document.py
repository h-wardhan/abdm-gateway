"""HealthDocumentRecord: the uploaded documents as DocumentReferences."""
from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.core import DocumentBuilder
from src.app.fhir.errors import InvalidRecord

CODE = u.coding(c.SNOMED, "419891008", "Record artifact")


def build(payload, hip_id, when=None):
    b = DocumentBuilder(payload, hip_id, when)
    docs = [d for d in u.as_list(payload.get("documentReference")) if isinstance(d, dict)]
    if not docs:
        raise InvalidRecord("The record has no documentReference", details={"field": "documentReference"})
    refs = [b.typed(b.document_reference(d, f"documentReference[{n}]", "Health Document Record", "419891008",
                                         "Record artifact"), "DocumentReference")
            for n, d in enumerate(docs)]
    return b.finish("HealthDocumentRecord", u.concept(CODE, text="Health Document Record"),
                    "Health Document Record", [("Health document record", CODE, refs)])
