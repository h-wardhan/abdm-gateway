"""Payloads that cannot become a valid record are refused with a clear InvalidRecord (HTTP 422), never a broken Bundle."""
import copy

import pytest

from src.app import fhir
from src.app.fhir.errors import FhirBuildError, InvalidRecord
from tests.fhir import samples
from tests.fhir.samples import HIP_ID, sample


def build(hi_type, payload):
    return fhir.build_record(hi_type, payload, HIP_ID)


@pytest.mark.parametrize("hi_type", fhir.RECORD_TYPES)
@pytest.mark.parametrize("payload", [{}, [], None, "x", {"success": False, "data": []},
                                     {"success": False, "message": "No data found"}])
def test_i_medicus_error_or_empty_bodies_are_refused(hi_type, payload):
    with pytest.raises(InvalidRecord) as err:
        build(hi_type, payload)
    assert err.value.status == 422 and err.value.code == "INVALID_RECORD"


def test_the_message_of_i_medicus_error_body_is_passed_on():
    with pytest.raises(InvalidRecord, match="No data found"):
        build("ImmunizationRecord", {"success": False, "message": "No data found"})


@pytest.mark.parametrize("path,message", [
    (("patient",), "patient is missing"),
    (("patient", "name"), "patient.name"),
    (("patient", "healthId"), "healthId"),
    (("practitioner",), "practitioner is missing"),
    (("practitioner", "licenseNumber"), "licenseNumber"),
    (("organization", "name"), "organization.name"),
    (("encounter",), "encounter is missing"),
    (("encounter", "encounterId"), "encounterId"),
])
def test_missing_shared_data_is_named_in_the_error(path, message):
    payload = sample("Prescription")
    target = payload
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    with pytest.raises(InvalidRecord, match=message):
        build("Prescription", payload)


def test_a_missing_facility_id_is_fine_when_the_client_has_a_hip():
    payload = sample("Prescription")
    del payload["organization"]["facilityId"]
    assert fhir.build_record("Prescription", payload, HIP_ID)
    with pytest.raises(InvalidRecord, match="facilityId"):
        fhir.build_record("Prescription", payload, None)


@pytest.mark.parametrize("hi_type,key", [
    ("Prescription", "medications"), ("DiagnosticReport", "diagnosticReports"),
    ("ImmunizationRecord", "immunizations"), ("HealthDocumentRecord", "documentReference"),
    ("Invoice", "lineItems"),
])
def test_a_record_with_nothing_in_it_is_refused(hi_type, key):
    payload = sample(hi_type)
    payload[key] = []
    payload.pop("binaryDocument", None)
    payload.pop("documentReference", None) if key != "documentReference" else None
    with pytest.raises(InvalidRecord):
        build(hi_type, payload)


def test_op_consultation_with_no_content_at_all_is_refused():
    payload = {k: v for k, v in sample("OPConsultation").items()
               if k in ("patient", "practitioner", "organization", "encounter")}
    with pytest.raises(InvalidRecord, match="no data"):
        build("OPConsultation", payload)


def test_diagnostic_results_without_names_or_values_are_refused():
    payload = sample("DiagnosticReport")
    for o in payload["diagnosticReports"][0]["observations"]:
        o["value"] = None
    with pytest.raises(InvalidRecord, match="name and a value"):
        build("DiagnosticReport", payload)


def test_i_medicus_empty_diagnostic_list_is_refused_as_nothing_to_send():
    with pytest.raises(InvalidRecord, match="nothing marked ready"):
        build("DiagnosticReport", [])


@pytest.mark.parametrize("document,message", [
    ({"contentType": "application/pdf", "base64Data": None}, "no content"),
    ({"contentType": "application/pdf", "base64Data": ""}, "no content"),
    ({"contentType": "application/pdf", "base64Data": "not base64 !!"}, "not valid base64"),
    ({"contentType": "application/pdf", "base64Data": "aGVsbG8gd29ybGQ="}, "not a PDF"),
    (None, "no content"),
])
def test_a_bad_document_is_refused_with_the_field_named(document, message):
    payload = sample("HealthDocumentRecord")
    payload["documentReference"] = [document] if document is not None else [{}]
    with pytest.raises(InvalidRecord, match=message):
        build("HealthDocumentRecord", payload)


def test_a_data_uri_prefix_on_the_pdf_is_accepted():
    payload = sample("HealthDocumentRecord")
    payload["documentReference"][0]["base64Data"] = "data:application/pdf;base64," + samples.PDF_B64
    bundle = build("HealthDocumentRecord", payload)
    attachment = bundle["entry"][-1]["resource"]["content"][0]["attachment"]
    assert attachment["data"] == samples.PDF_B64


def test_an_unknown_record_type_lists_the_supported_ones():
    with pytest.raises(InvalidRecord) as err:
        fhir.build_record("Radiology", sample("Prescription"), HIP_ID)
    assert err.value.code == "UNKNOWN_RECORD_TYPE" and "Prescription" in err.value.details["supported"]


def test_the_input_payload_is_not_modified():
    for hi_type in fhir.RECORD_TYPES:
        payload = sample(hi_type)
        before = copy.deepcopy(payload)
        build(hi_type, payload)
        assert payload == before


def test_a_builder_that_makes_an_invalid_bundle_is_a_500_not_a_bad_record(monkeypatch):
    from src.app.fhir import records

    def broken(payload, hip_id, when=None):
        bundle = records.prescription.build(payload, hip_id, when)
        bundle["type"] = "collection"
        return bundle

    monkeypatch.setitem(fhir.BUILDERS, "Prescription", broken)
    with pytest.raises(FhirBuildError) as err:
        build("Prescription", sample("Prescription"))
    assert err.value.status == 500 and any("document" in i for i in err.value.details["issues"])
