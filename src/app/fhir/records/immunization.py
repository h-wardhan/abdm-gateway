"""ImmunizationRecord: Immunization per vaccine, one ImmunizationRecommendation for the next doses, the PDF."""
from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.core import DocumentBuilder
from src.app.fhir.errors import InvalidRecord

CODE = u.coding(c.SNOMED, "41000179103", "Immunization record")
_FORECAST = {"due": "Due", "overdue": "Overdue", "immune": "Immune", "contraindicated": "Contraindicated",
             "complete": "Complete", "notcomplete": "Not Complete"}


def _vaccine_concept(code, system, display):
    code, display = u.text(code), u.text(display)
    if code and display:
        return u.concept(u.coding(u.text(system) or c.SNOMED, code, display), text=display)
    return u.concept(text=display) if display else None


def build(payload, hip_id, when=None):
    b = DocumentBuilder(payload, hip_id, when)
    rows = u.items(payload, "immunizations")
    if not rows:
        raise InvalidRecord("The record has no immunizations", details={"field": "immunizations"})

    immunizations, recommendations = [], []
    for row in rows:
        vaccine = _vaccine_concept(row.get("vaccineCode"), row.get("vaccineSystem"), row.get("vaccineDisplay"))
        if not vaccine:
            continue
        body = {"status": "completed", "vaccineCode": vaccine, "patient": b.patient, "primarySource": True,
                "performer": [{"actor": b.practitioner}]}
        # `occurrenceDate` / `nextDoseDate` are read by name, as the payload documents them
        occurred = u.fhir_datetime(row.get("occurrenceDate"))
        if occurred:
            body["occurrenceDateTime"] = occurred
        else:
            body["occurrenceString"] = "Date not recorded"
        if u.text(row.get("lotNumber")):
            body["lotNumber"] = u.text(row.get("lotNumber"))
        if u.text(row.get("manufacturer")):
            body["manufacturer"] = {"display": u.text(row.get("manufacturer"))}
        dose = u.text(row.get("doseNumber"))
        if dose:
            number = u.number(dose)
            body["protocolApplied"] = [{"doseNumberPositiveInt": int(number)} if isinstance(number, int) and number > 0
                                       else {"doseNumberString": dose}]
        ref = b.add("Immunization", body, display=u.text(row.get("vaccineDisplay")))
        immunizations.append(ref)

        due = u.fhir_datetime(row.get("nextDoseDate"))
        recommended = _vaccine_concept(row.get("recommendationVaccineCode"), row.get("recommendationVaccineSystem"),
                                       row.get("vaccineDisplay")) or vaccine
        if due:
            status = _FORECAST.get((u.text(row.get("forecastStatus")) or "due").lower().replace(" ", ""), "Due")
            recommendations.append({
                "vaccineCode": [recommended],
                "forecastStatus": u.concept(u.coding(c.IMM_RECOMMENDATION_STATUS, status.lower().replace(" ", ""),
                                                     status)),
                "dateCriterion": [{"code": u.concept(u.coding(c.LOINC, "30980-7", "Date vaccine due")), "value": due}],
                "supportingImmunization": [ref],
            })
    if not immunizations:
        raise InvalidRecord("None of the immunizations has a vaccine name", details={"field": "immunizations"})

    refs = [b.typed(r, "Immunization") for r in immunizations]
    if recommendations:
        refs.append(b.typed(b.add("ImmunizationRecommendation", {
            "patient": b.patient, "date": b.timestamp, "authority": b.organization,
            "recommendation": recommendations}, display="Immunization recommendation"),
            "ImmunizationRecommendation"))
    document = payload.get("documentReference")
    if isinstance(document, dict) and document.get("base64Data"):
        refs.append(b.typed(b.document_reference(document, "documentReference", "Immunization Record", "41000179103",
                                                 "Immunization record"), "DocumentReference"))
    return b.finish("ImmunizationRecord", u.concept(CODE, text="Immunization record"), "Immunization record",
                    [("Immunization record", CODE, refs)])
