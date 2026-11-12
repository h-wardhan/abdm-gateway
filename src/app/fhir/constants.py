"""Profile URLs and code systems of the ABDM FHIR R4 Implementation Guide (NRCeS, package ndhm.in 6.5.0)."""

IG_VERSION = "6.5.0"
BASE = "https://nrces.in/ndhm/fhir/r4"


def profile(name):
    return f"{BASE}/StructureDefinition/{name}"


DOCUMENT_BUNDLE = profile("DocumentBundle")

SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
V2_0203 = "http://terminology.hl7.org/CodeSystem/v2-0203"
V3_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
V3_ROLE_CODE = "http://terminology.hl7.org/CodeSystem/v3-RoleCode"
OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
ALLERGY_VERIFICATION = "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification"
IMM_RECOMMENDATION_STATUS = "http://terminology.hl7.org/CodeSystem/immunization-recommendation-status"
BILLING_CODES = f"{BASE}/CodeSystem/ndhm-billing-codes"
PRICE_COMPONENTS = f"{BASE}/CodeSystem/ndhm-price-components"

COMPOSITION_ID_SYSTEM = "https://ndhm.in/phr"
FACILITY_SYSTEM = "https://facility.ndhm.gov.in"
PRACTITIONER_SYSTEM = "https://doctor.ndhm.gov.in"
PATIENT_SYSTEM = "https://healthid.ndhm.gov.in"
BUNDLE_ID_SYSTEM = "http://hip.in"

IST_OFFSET_MINUTES = 330
LANGUAGE = "en-IN"
