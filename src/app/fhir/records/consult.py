"""OPConsultRecord and DischargeSummaryRecord: the same clinical sections, different profile, type and encounter."""
from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.core import DocumentBuilder
from src.app.fhir.records import _common


def _code(code, display):
    return u.coding(c.SNOMED, code, display)


def _refs(b, resource_type, refs):
    return [b.typed(r, resource_type) for r in refs if r]


def _observations(b, payload, key):
    """Vitals (category vital-signs) or body measurements: {code, display, value, unit}."""
    category = "vital-signs" if key == "vitalSigns" else None
    return [_common.observation(b, i.get("code"), i.get("display"), i.get("value"), unit=i.get("unit"),
                                category=category) for i in u.items(payload, key)]


def _shared(b, payload):
    """Sections both documents have, as (title, section code, entries)."""
    complaints = [_common.condition(b, i.get("snomedCode"), i.get("display")) for i in u.items(payload, "chiefComplaints")]
    exam = _observations(b, payload, "vitalSigns") + _observations(b, payload, "bodyMeasurements")
    allergies = [_common.allergy(b, i) for i in u.items(payload, "allergies")]
    family = [_common.family_history(b, i) for i in u.items(payload, "familyHistory")]
    medications = [_common.medication_request(b, i) for i in u.items(payload, "medications")]
    procedures = [_common.procedure(b, i) for i in u.items(payload, "procedures")]
    return {
        "complaints": ("Chief complaints", _code("422843007", "Chief complaint section"), _refs(b, "Condition", complaints)),
        "exam": ("Physical examination", _code("425044008", "Physical exam section"), _refs(b, "Observation", exam)),
        "allergies": ("Allergies", _code("722446000", "Allergy record"), _refs(b, "AllergyIntolerance", allergies)),
        "family": ("Family history", _code("422432008", "Family history section"), _refs(b, "FamilyMemberHistory", family)),
        "medications": ("Medications", None, _refs(b, "MedicationRequest", medications)),
        "procedures": ("Procedures", None, _refs(b, "Procedure", procedures)),
    }


def build_op_consultation(payload, hip_id, when=None):
    b = DocumentBuilder(payload, hip_id, when)
    s = _shared(b, payload)
    investigations = _refs(b, "ServiceRequest", [_common.service_request(b, i) for i in u.items(payload, "investigations")])
    follow_up = payload.get("appointment")
    follow_up_ref = _common.appointment(b, follow_up) if isinstance(follow_up, dict) else None
    follow_up = [b.typed(follow_up_ref, "Appointment")] if follow_up_ref else []
    document = _document(b, payload, "OP consultation", "371530004", "Clinical consultation report")

    type_code = _code("371530004", "Clinical consultation report")
    sections = [
        s["complaints"], s["exam"], s["allergies"], s["family"],
        ("Investigation advice", _code("721963009", "Order document"), investigations),
        ("Medications", _code("721912009", "Medication summary document"), s["medications"][2]),
        ("Follow up", _code("390906007", "Follow-up encounter"), follow_up),
        ("Procedures", _code("371525003", "Clinical procedure report"), s["procedures"][2]),
        ("Document", type_code, document),
    ]
    return b.finish("OPConsultRecord", u.concept(type_code, text="Clinical Consultation report"),
                    "Consultation Report", sections)


def build_discharge_summary(payload, hip_id, when=None):
    b = DocumentBuilder(payload, hip_id, when)
    s = _shared(b, payload)
    # i-medicus fills `medicalHistory` with the medicines and `dischargeDiagnosis` with the chief complaints; both are
    # passed on as they come (history = Conditions, diagnosis = Conditions).
    history = _refs(b, "Condition", [_common.condition(b, i.get("snomedCode"), i.get("display"))
                                     for i in u.items(payload, "medicalHistory")])
    diagnosis = _refs(b, "Condition", [_common.condition(b, i.get("snomedCode"), i.get("display"))
                                       for i in u.items(payload, "dischargeDiagnosis")])
    investigations = _lab_reports(b, payload)
    care_plan = _care_plan(b, payload)
    document = _document(b, payload, "Discharge summary", "373942005", "Discharge summary")

    type_code = _code("373942005", "Discharge summary")
    sections = [
        s["complaints"], ("Discharge diagnosis", _code("89100005", "Final diagnosis"), diagnosis),
        s["exam"], s["allergies"],
        ("Medical history", _code("1003642006", "Past medical history section"), history),
        s["family"],
        ("Investigations", _code("721981007", "Diagnostic studies report"), investigations),
        ("Medications", _code("1003606003", "Medication history section"), s["medications"][2]),
        ("Procedures", _code("1003640003", "History of past procedure section"), s["procedures"][2]),
        ("Care plan", _code("734163000", "Care plan"), care_plan),
        ("Document", type_code, document),
    ]
    return b.finish("DischargeSummaryRecord", u.concept(type_code, text="Discharge summary"),
                    "Discharge Summary", sections)


def _document(b, payload, title, code, display):
    doc = payload.get("documentReference")
    if not isinstance(doc, dict) or not doc.get("base64Data"):
        return []
    return [b.typed(b.document_reference(doc, "documentReference", title, code, display), "DocumentReference")]


def _lab_reports(b, payload):
    reports = u.items(payload, "diagnosticReports")
    ref = _common.diagnostic_report_lab(b, reports) if reports else None
    return [b.typed(ref, "DiagnosticReport")] if ref else []


def _care_plan(b, payload):
    plan = payload.get("carePlan")
    if not isinstance(plan, dict):
        return []
    title = u.text(plan.get("title"))
    if not title:
        return []
    body = {"status": u.text(plan.get("status")) or "active", "intent": u.text(plan.get("intent")) or "plan",
            "title": title, "subject": b.patient}
    if u.text(plan.get("description")):
        body["description"] = u.text(plan.get("description"))
    period = {k: u.fhir_datetime(plan.get(k)) for k in ("start", "end") if u.fhir_datetime(plan.get(k))}
    if period:
        body["period"] = period
    return [b.typed(b.add("CarePlan", body, display=title), "CarePlan")]
