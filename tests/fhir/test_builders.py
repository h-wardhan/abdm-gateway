"""Unit tests: every record type from a sample i-medicus payload to its FHIR Bundle."""
import json

import pytest

from src.app import fhir
from src.app.fhir import constants as c
from src.app.fhir import validate
from tests.fhir import samples
from tests.fhir.samples import HIP_ID, sample

PROFILE = c.profile

# hiType -> (Composition profile, Composition.type code, resource types that must be in the Bundle)
EXPECTED = {
    "Prescription": ("PrescriptionRecord", "440545006", {"MedicationRequest", "Binary"}),
    "OPConsultation": ("OPConsultRecord", "371530004", {"Condition", "Observation", "AllergyIntolerance",
                                                        "FamilyMemberHistory", "MedicationRequest", "Procedure",
                                                        "ServiceRequest", "Appointment", "DocumentReference"}),
    "DiagnosticReport": ("DiagnosticReportRecord", "721981007", {"DiagnosticReport", "Observation",
                                                                 "DocumentReference"}),
    "DischargeSummary": ("DischargeSummaryRecord", "373942005", {"Condition", "Observation", "DiagnosticReport",
                                                                 "CarePlan", "MedicationRequest", "Procedure",
                                                                 "DocumentReference"}),
    "ImmunizationRecord": ("ImmunizationRecord", "41000179103", {"Immunization", "ImmunizationRecommendation",
                                                                 "DocumentReference"}),
    "HealthDocumentRecord": ("HealthDocumentRecord", "419891008", {"DocumentReference"}),
    "WellnessRecord": ("WellnessRecord", None, {"Observation", "DocumentReference"}),
    "Invoice": ("InvoiceRecord", None, {"ChargeItem", "Invoice"}),
}
SHARED = {"Composition", "Patient", "Practitioner", "Organization", "Encounter"}


def build(hi_type, payload=None, hip_id=HIP_ID):
    return fhir.build_record(hi_type, sample(hi_type) if payload is None else payload, hip_id)


def by_type(bundle, resource_type):
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == resource_type]


def by_type_url(bundle, resource_type):
    return next(e["fullUrl"] for e in bundle["entry"] if e["resource"]["resourceType"] == resource_type)


@pytest.mark.parametrize("hi_type", list(EXPECTED))
def test_record_type_makes_a_document_bundle_with_its_profile(hi_type):
    profile_name, type_code, must_have = EXPECTED[hi_type]
    bundle = build(hi_type)

    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "document"
    assert bundle["meta"]["profile"] == [c.DOCUMENT_BUNDLE] and bundle["meta"]["versionId"] == "1"
    assert bundle["identifier"]["system"] and bundle["identifier"]["value"] and bundle["timestamp"]

    composition = bundle["entry"][0]["resource"]
    assert composition["resourceType"] == "Composition"
    assert composition["meta"]["profile"] == [PROFILE(profile_name)]
    if type_code:
        assert composition["type"]["coding"] == [{"system": c.SNOMED, "code": type_code,
                                                  "display": composition["type"]["coding"][0]["display"]}]
    present = {e["resource"]["resourceType"] for e in bundle["entry"]}
    assert SHARED <= present and must_have <= present
    assert validate.issues(bundle, hi_type) == []


@pytest.mark.parametrize("hi_type", list(EXPECTED))
def test_every_resource_has_a_profile_a_uuid_url_and_every_reference_resolves(hi_type):
    bundle = build(hi_type)
    urls = {e["fullUrl"] for e in bundle["entry"]}
    for e in bundle["entry"]:
        assert e["fullUrl"] == f"urn:uuid:{e['resource']['id']}"
        assert e["resource"]["meta"]["profile"]
    refs = [r for r in _refs(bundle["entry"]) if r.startswith("urn:uuid:")]
    assert refs and set(refs) <= urls


def _refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("reference"), str):
            yield node["reference"]
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


@pytest.mark.parametrize("hi_type", list(EXPECTED))
def test_the_bundle_is_plain_json(hi_type):
    bundle = build(hi_type)
    assert json.loads(json.dumps(bundle)) == bundle


# ---- shared resources ---------------------------------------------------------------------------------------------

def test_patient_practitioner_organization_encounter_from_the_common_blocks():
    bundle = build("Prescription")
    patient, practitioner = by_type(bundle, "Patient")[0], by_type(bundle, "Practitioner")[0]
    organization, encounter = by_type(bundle, "Organization")[0], by_type(bundle, "Encounter")[0]

    assert patient["name"] == [{"text": "Ravi Kumar"}] and patient["gender"] == "male"
    assert patient["birthDate"] == "1985-03-14"
    assert patient["telecom"] == [{"system": "phone", "value": "9876543210", "use": "mobile"}]
    assert patient["identifier"][0]["value"] == "ravi.kumar@abdm"
    assert patient["identifier"][0]["type"]["coding"][0]["code"] == "MR"
    assert practitioner["identifier"][0]["value"] == "MH-12345"
    assert practitioner["identifier"][0]["type"]["coding"][0]["code"] == "MD"
    assert organization["name"] == "Sunrise Clinic"
    assert encounter["identifier"][0]["value"] == "4521" and encounter["class"]["code"] == "AMB"
    assert encounter["status"] == "finished"


def test_organization_identifier_is_the_clients_hip_not_the_payload_facility_id():
    organization = by_type(build("Prescription"), "Organization")[0]
    assert organization["identifier"][0]["value"] == HIP_ID
    # the payload's "Sunrise Clinic12" is clinic_name + id, not an HFR id
    assert "Sunrise" not in organization["identifier"][0]["value"]


def test_organization_falls_back_to_the_payload_facility_id_when_the_client_has_no_hip():
    organization = by_type(build("Prescription", hip_id=None), "Organization")[0]
    assert organization["identifier"][0]["value"] == "Sunrise Clinic12"


def test_composition_points_at_the_shared_resources():
    bundle = build("OPConsultation")
    composition = bundle["entry"][0]["resource"]
    ids = {e["resource"]["resourceType"]: e["fullUrl"] for e in bundle["entry"]}
    assert composition["subject"]["reference"] == ids["Patient"]
    assert composition["author"][0]["reference"] == ids["Practitioner"]
    assert composition["custodian"]["reference"] == ids["Organization"]
    assert composition["encounter"]["reference"] == ids["Encounter"]


def test_unknown_gender_and_missing_optional_patient_fields_are_tolerated():
    payload = sample("Prescription")
    payload["patient"].update(gender=None, birthDate=None, phone="N/A")
    patient = by_type(build("Prescription", payload), "Patient")[0]
    assert patient["gender"] == "unknown" and "birthDate" not in patient and "telecom" not in patient


def test_dates_in_the_formats_i_medicus_uses_are_read():
    payload = sample("OPConsultation")
    payload["appointment"]["start"] = "15-Nov-2026 10:00 AM"
    payload["patient"]["birthDate"] = "14-03-1985"
    bundle = build("OPConsultation", payload)
    assert by_type(bundle, "Appointment")[0]["start"] == "2026-11-15T10:00:00+05:30"
    assert by_type(bundle, "Patient")[0]["birthDate"] == "1985-03-14"


# ---- Prescription -------------------------------------------------------------------------------------------------

def test_prescription_medicines_become_medication_requests():
    bundle = build("Prescription")
    first = by_type(bundle, "MedicationRequest")[0]
    assert len(by_type(bundle, "MedicationRequest")) == 2
    assert first["status"] == "active" and first["intent"] == "order"
    assert first["medicationCodeableConcept"]["coding"][0] == {
        "system": c.SNOMED, "code": "387517004", "display": "Paracetamol 500 mg oral tablet"}
    assert first["dosageInstruction"][0]["text"] == "1 tablet twice a day"
    assert first["dosageInstruction"][0]["route"]["coding"][0]["code"] == "26643006"
    assert first["requester"]["reference"] == by_type_url(bundle, "Practitioner")
    assert first["subject"]["reference"] == by_type_url(bundle, "Patient")
    assert first["authoredOn"] == bundle["timestamp"]


def test_the_placeholder_reason_code_is_not_sent_as_a_coding():
    # i-medicus puts 386661006 (Fever) on every medicine; the real reason is only in reasonDisplay
    payload = sample("Prescription")
    payload["medications"][1]["reasonDisplay"] = "Cough"
    second = by_type(build("Prescription", payload), "MedicationRequest")[1]
    assert second["reasonCode"] == [{"text": "Cough"}]


def test_a_real_reason_code_is_kept_as_snomed():
    payload = sample("Prescription")
    payload["medications"][0].update(reasonCode="11840006", reasonDisplay="Traveler's diarrhea")
    first = by_type(build("Prescription", payload), "MedicationRequest")[0]
    assert first["reasonCode"][0]["coding"][0]["code"] == "11840006"


def test_medicine_without_snomed_code_is_kept_as_text_and_without_a_name_is_dropped():
    payload = sample("Prescription")
    payload["medications"][0]["snomedCode"] = None
    payload["medications"][1].update(snomedCode=None, display=None)
    requests = by_type(build("Prescription", payload), "MedicationRequest")
    assert len(requests) == 1
    assert requests[0]["medicationCodeableConcept"] == {"text": "Paracetamol 500 mg oral tablet"}


def test_prescription_pdf_becomes_a_binary_in_the_only_section():
    bundle = build("Prescription")
    binary = by_type(bundle, "Binary")[0]
    assert binary["contentType"] == "application/pdf" and binary["data"] == samples.PDF_B64
    sections = bundle["entry"][0]["resource"]["section"]
    assert len(sections) == 1
    assert sections[0]["code"]["coding"][0]["code"] == "440545006"
    assert [e["type"] for e in sections[0]["entry"]] == ["MedicationRequest", "MedicationRequest", "Binary"]


def test_prescription_without_a_pdf_still_works():
    payload = sample("Prescription")
    del payload["binaryDocument"]
    assert not by_type(build("Prescription", payload), "Binary")


# ---- OP consultation / discharge ----------------------------------------------------------------------------------

def test_op_consultation_sections_and_entry_types():
    bundle = build("OPConsultation")
    sections = {s["title"]: s for s in bundle["entry"][0]["resource"]["section"]}
    assert set(sections) == {"Chief complaints", "Physical examination", "Allergies", "Family history",
                             "Investigation advice", "Medications", "Follow up", "Procedures", "Document"}
    codes = {s["code"]["coding"][0]["code"] for s in sections.values()}
    assert {"422843007", "425044008", "722446000", "422432008", "721963009", "721912009", "390906007", "371525003",
            "371530004"} == codes
    assert len(sections["Chief complaints"]["entry"]) == 2 and len(sections["Physical examination"]["entry"]) == 5


def test_vitals_get_the_right_ucum_unit_even_though_i_medicus_sends_per_minute_for_all():
    vitals = {o["code"]["coding"][0]["code"]: o for o in by_type(build("OPConsultation"), "Observation")}
    assert vitals["8867-4"]["valueQuantity"] == {"value": 98, "unit": "/min", "system": c.UCUM, "code": "/min"}
    assert vitals["8310-5"]["valueQuantity"]["code"] == "[degF]"
    assert vitals["2708-6"]["valueQuantity"] == {"value": 97, "unit": "%", "system": c.UCUM, "code": "%"}
    assert vitals["29463-7"]["valueQuantity"]["code"] == "kg"
    assert vitals["8310-5"]["category"][0]["coding"][0]["code"] == "vital-signs"
    # body weight / height are HL7 vital signs too, so they carry the category whichever section they are in
    assert vitals["29463-7"]["category"][0]["coding"][0]["code"] == "vital-signs"


def test_op_consultation_clinical_resources():
    bundle = build("OPConsultation")
    condition = by_type(bundle, "Condition")[0]
    assert condition["code"]["coding"][0]["code"] == "386661006" and condition["clinicalStatus"]
    allergy = by_type(bundle, "AllergyIntolerance")[0]
    assert allergy["clinicalStatus"]["coding"][0]["code"] == "active" and "category" not in allergy
    procedure = by_type(bundle, "Procedure")[0]
    assert procedure["status"] == "completed" and procedure["code"]["coding"][0]["code"] == "80146002"
    request = by_type(bundle, "ServiceRequest")[0]
    # the ServiceRequest profile fixes the coding system to SNOMED CT, so the LOINC test is sent by name only
    assert request["code"] == {"text": "Lipid 1996 panel - Serum or Plasma"}
    appointment = by_type(bundle, "Appointment")[0]
    assert appointment["start"] == "2026-11-15T10:00:00+05:30"
    assert appointment["appointmentType"]["coding"][0]["code"] == "185389009"


def test_family_history_leaves_out_contributed_to_death():
    history = by_type(build("OPConsultation"), "FamilyMemberHistory")[0]
    assert history["relationship"]["coding"][0]["code"] == "FTH"
    assert history["condition"][0]["code"]["coding"][0]["code"] == "73211009"
    assert "contributedToDeath" not in history["condition"][0]
    assert history["sex"]["coding"][0]["code"] == "248153007"


def test_procedure_with_an_odd_status_becomes_completed():
    payload = sample("OPConsultation")
    payload["procedures"][0]["status"] = 1
    assert by_type(build("OPConsultation", payload), "Procedure")[0]["status"] == "completed"


def test_op_consultation_with_only_a_pdf_is_valid():
    payload = {**sample("Prescription"), "documentReference": samples._doc("OP"), "binaryDocument": None}
    payload.pop("medications")
    bundle = build("OPConsultation", payload)
    assert [s["title"] for s in bundle["entry"][0]["resource"]["section"]] == ["Document"]


def test_discharge_summary_is_inpatient_with_diagnosis_investigations_and_care_plan():
    bundle = build("DischargeSummary")
    assert by_type(bundle, "Encounter")[0]["class"]["code"] == "IMP"
    sections = {s["title"]: s["code"]["coding"][0]["code"] for s in bundle["entry"][0]["resource"]["section"]}
    assert sections["Discharge diagnosis"] == "89100005" and sections["Medical history"] == "1003642006"
    assert sections["Investigations"] == "721981007" and sections["Care plan"] == "734163000"
    plan = by_type(bundle, "CarePlan")[0]
    assert plan["title"] == "Discharge care plan" and plan["intent"] == "plan" and "period" not in plan
    assert len(by_type(bundle, "DiagnosticReport")) == 1


# ---- Diagnostic report --------------------------------------------------------------------------------------------

def test_diagnostic_report_has_results_pdf_interpreter_and_conclusion():
    bundle = build("DiagnosticReport")
    report = by_type(bundle, "DiagnosticReport")[0]
    assert report["meta"]["profile"] == [PROFILE("DiagnosticReportLab")]
    assert report["code"]["coding"][0]["code"] == "24331-1"
    assert len(report["result"]) == 2 and report["status"] == "final"
    assert report["resultsInterpreter"] and report["conclusion"]
    assert report["presentedForm"][0]["contentType"] == "application/pdf"
    observation = by_type(bundle, "Observation")[0]
    assert observation["valueQuantity"] == {"value": 156, "unit": "mg/dL", "system": c.UCUM, "code": "mg/dL"}
    assert observation["effectiveDateTime"] == "2026-11-01T09:30:00+05:30"
    assert report["issued"] == "2026-11-01T09:30:00+05:30"
    section = bundle["entry"][0]["resource"]["section"]
    assert len(section) == 1 and [e["type"] for e in section[0]["entry"]] == ["DiagnosticReport", "DocumentReference"]


def test_several_lab_groups_are_merged_into_the_single_report_the_profile_allows():
    payload = sample("DiagnosticReport")
    payload["diagnosticReports"].append({"loincCode": "718-7", "display": "Hemoglobin", "observations": [
        {"loincCode": "718-7", "display": "Hemoglobin [Mass/volume] in Blood", "value": "13.5", "unit": "g/dL",
         "valueDateTime": "2026-11-02T08:00:00+05:30"}]})
    bundle = build("DiagnosticReport", payload)
    report = by_type(bundle, "DiagnosticReport")[0]
    assert len(by_type(bundle, "DiagnosticReport")) == 1 and len(report["result"]) == 3
    assert report["code"]["text"] == "Laboratory investigations"
    assert report["issued"] == "2026-11-02T08:00:00+05:30"


def test_non_numeric_lab_value_is_a_string():
    payload = sample("DiagnosticReport")
    payload["diagnosticReports"][0]["observations"][0].update(value="Positive", unit=None)
    first = by_type(build("DiagnosticReport", payload), "Observation")[0]
    assert first["valueString"] == "Positive" and "valueQuantity" not in first


def test_lab_observation_without_a_value_is_left_out():
    payload = sample("DiagnosticReport")
    payload["diagnosticReports"][0]["observations"][1]["value"] = None
    assert len(by_type(build("DiagnosticReport", payload), "Observation")) == 1


# ---- Immunization -------------------------------------------------------------------------------------------------

def test_immunization_and_its_recommendation():
    bundle = build("ImmunizationRecord")
    immunization = by_type(bundle, "Immunization")[0]
    assert immunization["status"] == "completed" and immunization["lotNumber"] == "AB1234"
    assert immunization["vaccineCode"]["coding"][0]["code"] == "1119305005"
    assert immunization["occurrenceDateTime"] == "2026-10-20T00:00:00+05:30"
    assert immunization["protocolApplied"] == [{"doseNumberPositiveInt": 1}]
    assert immunization["manufacturer"] == {"display": "Serum Inst."}

    recommendation = by_type(bundle, "ImmunizationRecommendation")[0]["recommendation"][0]
    assert recommendation["forecastStatus"]["coding"][0]["code"] == "due"
    assert recommendation["dateCriterion"][0]["value"] == "2026-11-20T00:00:00+05:30"
    assert recommendation["dateCriterion"][0]["code"]["coding"][0]["code"] == "30980-7"
    assert recommendation["supportingImmunization"][0]["reference"] == bundle["entry"][5]["fullUrl"]


def test_immunization_without_dates_has_no_recommendation_and_a_text_occurrence():
    payload = sample("ImmunizationRecord")
    payload["immunizations"][0].update(occurrenceDate=None, nextDoseDate=None, doseNumber="booster")
    bundle = build("ImmunizationRecord", payload)
    assert not by_type(bundle, "ImmunizationRecommendation")
    immunization = by_type(bundle, "Immunization")[0]
    assert immunization["occurrenceString"] and "occurrenceDateTime" not in immunization
    assert immunization["protocolApplied"] == [{"doseNumberString": "booster"}]


# ---- Health document ----------------------------------------------------------------------------------------------

def test_health_document_is_a_document_reference_with_the_real_content_type():
    payload = sample("HealthDocumentRecord")
    payload["documentReference"].append({"contentType": "application/pdf", "base64Data": samples.PNG_B64,
                                         "title": "Scan"})
    references = by_type(build("HealthDocumentRecord", payload), "DocumentReference")
    assert [r["content"][0]["attachment"]["contentType"] for r in references] == ["application/pdf", "image/png"]
    assert references[0]["content"][0]["attachment"]["language"] == "en-IN"
    assert references[1]["content"][0]["attachment"]["title"] == "Scan"
    assert references[0]["status"] == "current" and references[0]["type"]["coding"][0]["code"] == "419891008"


# ---- Wellness -----------------------------------------------------------------------------------------------------

def test_wellness_groups_observations_into_their_sections_and_profiles():
    bundle = build("WellnessRecord")
    sections = {s["title"]: s for s in bundle["entry"][0]["resource"]["section"]}
    assert list(sections) == ["Vital Signs", "Body Measurement", "Physical Activity", "General Assessment",
                              "Women Health", "Lifestyle", "Document Reference"]
    observations = by_type(bundle, "Observation")
    assert len(observations) == 9
    profiles = {tuple(o["meta"]["profile"]) for o in observations}
    assert (PROFILE("Observation"), PROFILE("ObservationVitalSigns")) in profiles
    assert (PROFILE("Observation"), PROFILE("ObservationLifestyle")) in profiles
    assert bundle["entry"][0]["resource"]["type"] == {"text": "Wellness Record"}


def test_wellness_values_numbers_strings_and_unitless():
    by_code = {o["code"]["coding"][0]["code"]: o for o in by_type(build("WellnessRecord"), "Observation")}
    assert by_code["55423-8"]["valueQuantity"] == {"value": 5000, "unit": "steps"}
    assert by_code["81663-7"]["valueCodeableConcept"] == {"text": "Vegetarian diet"}   # Lifestyle: a concept only
    assert by_code["2339-0"]["valueQuantity"]["unit"] == "mg/dL"
    assert by_code["42798-9"]["valueQuantity"] == {"value": 13, "unit": "a"}   # unit not in our UCUM table: kept as sent


def test_wellness_item_without_any_value_is_skipped():
    payload = sample("WellnessRecord")
    payload["generalAssessment"][0]["value"] = None
    assert len(by_type(build("WellnessRecord", payload), "Observation")) == 8


# ---- Invoice ------------------------------------------------------------------------------------------------------

def test_invoice_has_charge_items_price_components_and_totals():
    bundle = build("Invoice")
    invoice = by_type(bundle, "Invoice")[0]
    assert len(by_type(bundle, "ChargeItem")) == 2 and len(invoice["lineItem"]) == 2
    first = invoice["lineItem"][0]
    components = {p["code"]["coding"][0]["display"]: p["amount"]["value"] for p in first["priceComponent"]}
    assert components == {"Rate": 18, "MRP": 20, "Discount": 2, "CGST": 0.45, "SGST": 0.45}
    assert {p["type"] for p in first["priceComponent"]} == {"base", "informational", "discount", "tax"}
    assert invoice["totalNet"] == {"value": 96, "currency": "INR"}      # 18*2 + 60
    assert invoice["totalGross"] == {"value": 99.9, "currency": "INR"}  # 36.9 + 63
    assert invoice["identifier"][0]["value"] == "INV-4521" and invoice["status"] == "issued"
    charge = by_type(bundle, "ChargeItem")[0]
    assert charge["code"]["coding"][0]["code"] == "05" and charge["quantity"] == {"value": 2}
    assert charge["productCodeableConcept"]["coding"][0]["code"] == "322236009"
    assert by_type(bundle, "ChargeItem")[1]["productCodeableConcept"] == {"text": "Amoxicillin 500 mg Capsule"}


def test_invoice_with_an_unknown_billing_code_is_billed_as_others():
    payload = sample("Invoice")
    payload["lineItems"][0]["billingCode"] = "42"
    assert by_type(build("Invoice", payload), "ChargeItem")[0]["code"]["coding"][0]["code"] == "99"
