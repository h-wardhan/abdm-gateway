"""Resource builders used by more than one record type (observations, conditions, medications, ...)."""
import logging

from src.app.fhir import constants as c
from src.app.fhir import util as u

logger = logging.getLogger(__name__)

# i-medicus puts this code on every medication's `reasonCode` whatever the reason is ("Fever"), so on its own it
# says nothing about the real reason (`reasonDisplay`): such a reason is sent as text only.
_PLACEHOLDER_REASON = {"386661006"}

# LOINC codes HL7 FHIR's own vital-signs profiles (bodyweight, bodyheight, heartrate, ...) apply to; an Observation with
# one of them must carry the vital-signs category whichever ABDM section it is in.
_VITAL_SIGNS_LOINC = {"29463-7", "8302-2", "8306-3", "39156-5", "9843-4", "8867-4", "9279-1", "8310-5", "8480-6",
                      "8462-4", "85354-9", "2708-6", "59408-5", "9279-1", "8329-5", "61008-9"}

_PROCEDURE_STATUS = {"preparation", "in-progress", "not-done", "on-hold", "stopped", "completed",
                     "entered-in-error", "unknown"}


def observation(b, loinc, display, value, *, unit=None, category=None, profiles=(), when=None, string_value=None,
                date_value=None, concept_code=None, concept_display=None, value_as="any"):
    """Observation from one i-medicus measurement. Returns None (and logs) when there is no value to report.
    `value_as` is what the section's profile allows: "any" (Quantity or string), "quantity_or_concept" (a string
    becomes a CodeableConcept) or "concept" (everything becomes a CodeableConcept)."""
    loinc, display = u.text(loinc), u.text(display)
    if not category and loinc in _VITAL_SIGNS_LOINC:
        category = "vital-signs"
    body = {"status": "final", "subject": b.patient, "performer": [b.practitioner],
            "effectiveDateTime": u.fhir_datetime(when) or b.timestamp}
    if category:
        body["category"] = [u.concept(u.coding(c.OBSERVATION_CATEGORY, category, "Vital Signs"), text="Vital Signs")]
    if loinc and display:
        body["code"] = u.concept(u.coding(c.LOINC, loinc, display), text=display)
    elif display:
        body["code"] = u.concept(text=display)
    else:
        logger.warning("fhir: observation without a name skipped (loinc=%s)", loinc)
        return None

    number = u.number(value)
    if number is not None and value_as != "concept":
        body["valueQuantity"] = u.quantity(number, unit, loinc)
    elif u.text(value) and value_as != "any":
        shown = " ".join(x for x in (u.text(value), u.text(unit) if number is not None else None) if x)
        body["valueCodeableConcept"] = u.concept(text=shown)
    elif u.text(value):
        body["valueString"] = u.text(value)
    elif u.text(concept_code) and u.text(concept_display):
        body["valueCodeableConcept"] = u.concept(u.coding(c.SNOMED, u.text(concept_code), u.text(concept_display)),
                                                 text=u.text(concept_display))
    elif u.text(string_value):
        body["valueString" if value_as == "any" else "valueCodeableConcept"] = (
            u.text(string_value) if value_as == "any" else u.concept(text=u.text(string_value)))
    elif u.fhir_datetime(date_value):
        body["valueDateTime"] = u.fhir_datetime(date_value)
    else:
        logger.warning("fhir: observation '%s' has no value, skipped", display)
        return None
    return b.add("Observation", body, *(profiles or (c.profile("Observation"),)), display=display)


def condition(b, snomed, display):
    display = u.text(display)
    if not display:
        return None
    code = u.snomed_concept(snomed, display) or u.concept(text=display)
    body = {"clinicalStatus": u.concept(u.coding(c.CONDITION_CLINICAL, "active", "Active")),
            "code": code, "subject": b.patient}
    return b.add("Condition", body, display=display)


def medication_request(b, med):
    """MedicationRequest from a prescription line, or None when it has neither a code nor a name."""
    name = u.text(med.get("display"))
    medication = u.snomed_concept(med.get("snomedCode"), name)
    if not medication:
        logger.warning("fhir: medication without a name skipped")
        return None
    dosage = {"text": u.text(med.get("dosageText")) or u.text(med.get("methodDisplay")) or "Dosage not specified"}
    route = u.snomed_concept(med.get("routeCode"), med.get("routeDisplay"))
    if route:
        dosage["route"] = route
    body = {"status": "active", "intent": "order", "medicationCodeableConcept": medication,
            "subject": b.patient, "authoredOn": b.timestamp, "requester": b.practitioner,
            "dosageInstruction": [dosage]}
    reason = u.text(med.get("reasonDisplay"))
    if reason:
        if u.text(med.get("reasonCode")) in _PLACEHOLDER_REASON or not u.text(med.get("reasonCode")):
            body["reasonCode"] = [u.concept(text=reason)]
        else:
            body["reasonCode"] = [u.snomed_concept(med.get("reasonCode"), reason)]
    return b.add("MedicationRequest", body, display=name)


def allergy(b, item):
    display = u.text(item.get("display"))
    if not display:
        return None
    active = (u.text(item.get("clinicalStatus")) or "active").lower() != "inactive"
    body = {
        "clinicalStatus": u.concept(u.coding(c.ALLERGY_CLINICAL, "active" if active else "inactive",
                                             "Active" if active else "Inactive")),
        "verificationStatus": u.concept(u.coding(c.ALLERGY_VERIFICATION, "confirmed", "Confirmed")),
        "code": u.snomed_concept(item.get("snomedCode"), display),
        "patient": b.patient, "recordedDate": u.fhir_datetime(item.get("recordedDate")) or b.timestamp,
        "recorder": b.practitioner,
    }
    # i-medicus sends category "food" for every allergy, so the category is left out rather than guessed
    return b.add("AllergyIntolerance", body, display=display)


def procedure(b, item):
    display = u.text(item.get("display"))
    if not display:
        return None
    status = (u.text(item.get("status")) or "completed").lower()
    body = {"status": status if status in _PROCEDURE_STATUS else "completed",
            "code": u.snomed_concept(item.get("snomedCode"), display), "subject": b.patient}
    return b.add("Procedure", body, display=display)


def service_request(b, item):
    """An investigation advised (OP consultation). i-medicus sends LOINC codes, but the ServiceRequest profile fixes
    the coding system to SNOMED CT, so the test is sent by name."""
    display = u.text(item.get("display"))
    if not display:
        return None
    code = u.concept(text=display)
    body = {"status": "active", "intent": "order", "code": code, "subject": b.patient,
            "authoredOn": b.timestamp, "requester": b.practitioner}
    return b.add("ServiceRequest", body, display=display)


def family_history(b, item):
    condition_display = u.text(item.get("conditionDisplay"))
    relationship = u.text(item.get("relationship"))
    if not (condition_display and relationship):
        return None
    rel_code = u.text(item.get("relationshipCode"))
    body = {
        "status": "completed", "patient": b.patient, "date": u.fhir_datetime(item.get("date")) or b.timestamp,
        "relationship": u.concept(u.coding(c.V3_ROLE_CODE, rel_code, relationship) if rel_code else None,
                                  text=relationship),
        # `contributedToDeath` is true for every relative in i-medicus' payload; it is not passed on
        "condition": [{"code": u.snomed_concept(item.get("conditionCode"), condition_display)}],
    }
    sex = u.snomed_concept(item.get("sexCode"), u.text(item.get("sexDisplay")))
    if sex and u.text(item.get("sexCode")) not in (None, "UNK"):
        body["sex"] = sex
    return b.add("FamilyMemberHistory", body, display=relationship)


def appointment(b, item):
    start = u.fhir_datetime(item.get("start"))
    if not start:
        return None
    body = {"status": "booked", "description": u.text(item.get("description")) or "Follow-up",
            "start": start,
            "participant": [{"actor": b.patient, "status": "accepted"},
                            {"actor": b.practitioner, "status": "accepted"}]}
    if u.fhir_datetime(item.get("end")):
        body["end"] = u.fhir_datetime(item.get("end"))
    if (u.text(item.get("serviceType")) or "").lower() == "consultation":
        body["serviceType"] = [u.concept(u.coding(c.SNOMED, "11429006", "Consultation"))]
    if (u.text(item.get("appointmentType")) or "").lower() == "follow-up visit":
        body["appointmentType"] = u.concept(u.coding(c.SNOMED, "185389009", "Follow-up visit"))
    return b.add("Appointment", body, display="Follow-up appointment")


def diagnostic_report_lab(b, reports, *, presented=None):
    """One DiagnosticReport (Lab) + its Observations from i-medicus' `diagnosticReports` groups.
    The profile allows a single DiagnosticReport per document, so several lab groups are merged into one report."""
    results, issued = [], []
    for group in reports:
        for o in u.items(group, "observations"):
            ref = observation(b, o.get("loincCode"), o.get("display"), o.get("value"), unit=o.get("unit"),
                              when=o.get("valueDateTime"))
            if ref:
                results.append(ref)
                if u.parse(o.get("valueDateTime")):
                    issued.append(u.parse(o.get("valueDateTime")))
    if not results:
        return None
    named = [g for g in reports if u.text(g.get("display"))]
    if len(named) == 1:
        title = u.text(named[0].get("display"))
        code = (u.concept(u.coding(c.LOINC, u.text(named[0].get("loincCode")), title), text=title)
                if u.text(named[0].get("loincCode")) else u.concept(text=title))
    else:
        title = "Laboratory investigations"
        code = u.concept(u.coding(c.SNOMED, "15220000", "Laboratory test"), text=title)
    body = {
        "status": "final",
        "category": [u.concept(u.coding(c.SNOMED, "15220000", "Laboratory test"))],
        "code": code, "subject": b.patient,
        "issued": (max(issued).isoformat(timespec="seconds") if issued else b.timestamp),
        "performer": [b.organization], "resultsInterpreter": [b.practitioner],
        "result": results,
        "conclusion": f"{title}: {len(results)} result(s) recorded",
    }
    if presented:
        body["presentedForm"] = presented
    return b.add("DiagnosticReport", body, c.profile("DiagnosticReportLab"), display=title)
