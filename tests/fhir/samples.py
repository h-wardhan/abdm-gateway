"""
Sample i-medicus payloads, one per record type, shaped exactly like what WebhookTrait::FHIRApi builds
(prescriptionRecord, opConsulationRecord, diagnosticRecord, ...) with the real field names and the same quirks
(`binaryDocument` for prescriptions, `documentReference` as a list for health documents, "/min" on every vital, ...).
"""
import base64
import copy

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
PDF_B64 = base64.b64encode(PDF).decode()
PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()

HIP_ID = "IN0110005723_240"


def _doc(title):
    return {"contentType": "application/pdf", "base64Data": PDF_B64, "title": title, "language": "en-IN"}


def _common(encounter_class=("AMB", "Ambulatory")):
    return {
        "patient": {"name": "Ravi Kumar", "gender": "male", "birthDate": "1985-03-14", "phone": "9876543210",
                    "healthId": "ravi.kumar@abdm"},
        "practitioner": {"name": "Dr. Asha Verma", "licenseNumber": "MH-12345"},
        "organization": {"name": "Sunrise Clinic", "facilityId": "Sunrise Clinic12"},
        "encounter": {"encounterId": "4521", "classCode": encounter_class[0], "classDisplay": encounter_class[1]},
    }


MED = {"snomedCode": "387517004", "display": "Paracetamol 500 mg oral tablet", "dosageText": "1 tablet twice a day",
       "routeCode": "26643006", "routeDisplay": "Oral", "methodCode": "421521009", "methodDisplay": "1-0-1",
       "reasonCode": "386661006", "reasonDisplay": "Fever"}
VITALS = [
    {"code": "8867-4", "display": "Heart rate", "value": 98, "unit": "/min", "unitCode": None, "components": None},
    {"code": "8310-5", "display": "Body temperature", "value": 98.6, "unit": "/min", "unitCode": None,
     "components": None},
    {"code": "2708-6", "display": "Oxygen saturation in Arterial blood", "value": "97", "unit": "/min",
     "unitCode": None, "components": None},
]
BODY = [{"code": "29463-7", "display": "Body weight", "value": 72, "unit": "KG", "unitCode": None},
        {"code": "8302-2", "display": "Body height", "value": 170, "unit": "cm", "unitCode": None}]
COMPLAINTS = [{"snomedCode": "386661006", "display": "Fever", "category": "Chief Complaint"},
              {"snomedCode": "49727002", "display": "Cough", "category": "Chief Complaint"}]
ALLERGIES = [{"snomedCode": "91935005", "display": "Peanut allergy", "clinicalStatus": "active",
              "verificationStatus": "confirmed", "recordedDate": "2026-11-01T10:00:00+05:30", "category": "food"}]
PROCEDURES = [{"snomedCode": "80146002", "display": "Appendectomy", "status": "completed"}]
FAMILY = [{"relationship": "Father", "relationshipCode": "FTH", "conditionCode": "73211009",
           "conditionDisplay": "Diabetes mellitus", "date": "2026-11-01T10:00:00+05:30", "sexCode": "248153007",
           "sexDisplay": "male", "contributedToDeath": True}]
LAB_REPORTS = [{"loincCode": "24331-1", "display": "Lipid 1996 panel - Serum or Plasma", "observations": [
    {"loincCode": "2093-3", "display": "Cholesterol [Mass/volume] in Serum or Plasma", "value": "156",
     "unit": "mg/dL", "valueDateTime": "2026-11-01T09:30:00+05:30", "valueCodeableConceptCode": "x",
     "valueCodeableConceptSystem": "y", "valueCodeableConceptDisplay": None},
    {"loincCode": "2571-8", "display": "Triglyceride [Mass/volume] in Serum or Plasma", "value": 140,
     "unit": "mg/dL", "valueDateTime": "2026-11-01T09:30:00+05:30"}]}]
APPOINTMENT = {"description": "Next Visit", "start": "2026-11-15T10:00:00+05:30", "end": "2026-11-15T10:30:00+05:30",
               "serviceType": "Consultation", "appointmentType": "Follow-up visit"}


def prescription():
    return {"medications": [MED, {**MED, "snomedCode": "372687004", "display": "Amoxicillin 500 mg oral capsule",
                                  "reasonDisplay": "Cough"}],
            "binaryDocument": _doc("Prescription"), **_common()}


def op_consultation():
    return {"chiefComplaints": COMPLAINTS, "vitalSigns": VITALS, "bodyMeasurements": BODY, "allergies": ALLERGIES,
            "investigations": [{"loincCode": "24331-1", "display": "Lipid 1996 panel - Serum or Plasma"}],
            "procedures": PROCEDURES, "medications": [MED], "familyHistory": FAMILY, "appointment": APPOINTMENT,
            "documentReference": _doc("OP Consult Record"), **_common()}


def discharge_summary():
    return {**op_consultation(),
            "medicalHistory": [{"snomedCode": "73211009", "display": "Diabetes mellitus", "category": "Chief Complaint"}],
            "dischargeDiagnosis": [{**COMPLAINTS[0], "category": "Discharge Diagnosis"}],
            "diagnosticReports": LAB_REPORTS,
            "carePlan": {"title": "Discharge care plan", "description": "Discharge on request", "status": "active",
                         "intent": "plan", "start": None, "end": None},
            "documentReference": _doc("Discharge summary record"),
            **_common(("IMP", "inpatient encounter"))}


def diagnostic_report():
    return {"diagnosticReports": LAB_REPORTS, "documentReference": _doc("Diagnostic Record"), **_common()}


def immunization():
    return {"immunizations": [{
        "vaccineCode": "1119305005", "vaccineDisplay": "COVID-19 antigen vaccine",
        "vaccineSystem": "http://snomed.info/sct", "doseNumber": 1, "lotNumber": "AB1234", "manufacturer": "Serum Inst.",
        "occurrenceDate": "2026-10-20T00:00:00+05:30", "recommendationVaccineCode": "1119305005",
        "recommendationVaccineSystem": None, "forecastStatus": "Due", "nextDoseDate": "2026-11-20T00:00:00+05:30"}],
        "documentReference": _doc("Immunization Record"), **_common()}


def health_document():
    return {"documentReference": [_doc("Health Document Record")], **_common()}


def wellness():
    return {"vitalSigns": VITALS, "bodyMeasurements": BODY,
            "physicalActivity": [{"loincCode": "55423-8", "display": "Number of steps in unspecified time Pedometer",
                                  "value": "5000", "unit": "steps", "valueString": None, "valueDateTime": None}],
            "generalAssessment": [{"loincCode": "2339-0", "display": "Glucose [Mass/volume] in Blood", "value": 100,
                                   "unit": "mg/dL"}],
            "womenHealth": [{"loincCode": "42798-9", "display": "Age at menarche", "value": "13", "unit": "a",
                             "valueString": None, "valueDateTime": None}],
            "lifestyle": [{"loincCode": "81663-7", "display": "Diet type", "value": "Vegetarian diet", "unit": None,
                           "valueCodeableConceptCode": "266919005"}],
            "documentReference": _doc("Wellness Record"), **_common()}


def invoice():
    return {"lineItems": [
        {"itemName": "Paracetamol 500 mg Tablet", "snomedCode": "322236009", "billingCode": "05", "quantity": 2,
         "mrp": 20, "discount": 2, "rate": 18, "cgst": 0.45, "sgst": 0.45, "total": 36.9},
        {"itemName": "Amoxicillin 500 mg Capsule", "snomedCode": None, "billingCode": "05", "quantity": 1,
         "mrp": 60, "discount": 0, "rate": 60, "cgst": 1.5, "sgst": 1.5, "total": 63}], **_common()}


SAMPLES = {
    "Prescription": prescription, "OPConsultation": op_consultation, "DiagnosticReport": diagnostic_report,
    "DischargeSummary": discharge_summary, "ImmunizationRecord": immunization, "HealthDocumentRecord": health_document,
    "WellnessRecord": wellness, "Invoice": invoice,
}


def sample(hi_type):
    return copy.deepcopy(SAMPLES[hi_type]())
