"""WellnessRecord: one Observation per measurement, grouped in the sections the profile defines."""
from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.core import DocumentBuilder
from src.app.fhir.records import _common

_OBSERVATION = c.profile("Observation")

# payload key -> (section title, Observation profile, name of the payload's code key, category, value type the
# profile allows: Lifestyle only a CodeableConcept, General Assessment a Quantity or a CodeableConcept, the rest a
# Quantity or a string)
_GROUPS = [
    ("vitalSigns", "Vital Signs", "ObservationVitalSigns", "code", "vital-signs", "any"),
    ("bodyMeasurements", "Body Measurement", "ObservationBodyMeasurement", "code", None, "any"),
    ("physicalActivity", "Physical Activity", "ObservationPhysicalActivity", "loincCode", None, "any"),
    ("generalAssessment", "General Assessment", "ObservationGeneralAssessment", "loincCode", None,
     "quantity_or_concept"),
    ("womenHealth", "Women Health", "ObservationWomenHealth", "loincCode", None, "any"),
    ("lifestyle", "Lifestyle", "ObservationLifestyle", "loincCode", None, "concept"),
]


def build(payload, hip_id, when=None):
    b = DocumentBuilder(payload, hip_id, when)
    sections = []
    for key, title, profile_name, code_key, category, value_as in _GROUPS:
        refs = []
        for i in u.items(payload, key):
            ref = _common.observation(
                b, i.get(code_key), i.get("display"), i.get("value"), unit=i.get("unit"), category=category,
                profiles=(_OBSERVATION, c.profile(profile_name)), string_value=i.get("valueString"),
                date_value=i.get("valueDateTime"), concept_code=i.get("valueCodeableConceptCode"),
                concept_display=i.get("valueCodeableConceptDisplay"), value_as=value_as)
            if ref:
                refs.append(b.typed(ref, "Observation"))
        sections.append((title, None, refs))

    document = payload.get("documentReference")
    if isinstance(document, dict) and document.get("base64Data"):
        ref = b.document_reference(document, "documentReference", "Wellness Record", "419891008", "Record artifact")
        sections.append(("Document Reference", None, [b.typed(ref, "DocumentReference")]))
    return b.finish("WellnessRecord", {"text": "Wellness Record"}, "Wellness Record", sections)
