from src.app.fhir.records import (consult, diagnostic, health_document, immunization, invoice, prescription,
                                  wellness)

# hiType (what i-medicus' FHIRApi switches on) -> builder(payload, hip_id, when) -> Bundle dict
BUILDERS = {
    "Prescription": prescription.build,
    "OPConsultation": consult.build_op_consultation,
    "DiagnosticReport": diagnostic.build,
    "DischargeSummary": consult.build_discharge_summary,
    "ImmunizationRecord": immunization.build,
    "HealthDocumentRecord": health_document.build,
    "WellnessRecord": wellness.build,
    "Invoice": invoice.build,
}
