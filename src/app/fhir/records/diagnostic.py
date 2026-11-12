"""DiagnosticReportRecord: one DiagnosticReport (Lab) with its Observations, plus the report PDF."""
from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.core import DocumentBuilder
from src.app.fhir.errors import InvalidRecord
from src.app.fhir.records import _common

CODE = u.coding(c.SNOMED, "721981007", "Diagnostic studies report")


def build(payload, hip_id, when=None):
    if isinstance(payload, list) and not payload:
        raise InvalidRecord("No diagnostic results to send (nothing marked ready in i-medicus)")
    b = DocumentBuilder(payload, hip_id, when)
    reports = u.items(payload, "diagnosticReports")
    if not reports:
        raise InvalidRecord("The record has no diagnosticReports", details={"field": "diagnosticReports"})

    presented = None
    document = payload.get("documentReference")
    if isinstance(document, dict) and document.get("base64Data"):
        content_type, data, title = u.attachment(document, "documentReference", "Diagnostic Record")
        presented = [{"contentType": content_type, "language": c.LANGUAGE, "data": data, "title": title}]

    report = _common.diagnostic_report_lab(b, reports, presented=presented)
    if not report:
        raise InvalidRecord("None of the diagnostic results has a name and a value",
                            details={"field": "diagnosticReports"})
    refs = [b.typed(report, "DiagnosticReport")]
    if presented:
        refs.append(b.typed(b.document_reference(document, "documentReference", "Diagnostic Record",
                                                 "4241000179101", "Laboratory report"), "DocumentReference"))
    return b.finish("DiagnosticReportRecord", u.concept(CODE, text="Diagnostic Report- Lab"),
                    "Diagnostic Report", [("Diagnostic studies report", CODE, refs)])
