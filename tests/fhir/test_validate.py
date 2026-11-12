"""The runtime validator must catch the mistakes that would make ABDM (or a HIU) reject a document."""
import copy

import pytest

from src.app import fhir
from src.app.fhir import validate
from tests.fhir.samples import HIP_ID, sample


def make(hi_type="Prescription"):
    return fhir.build_record(hi_type, sample(hi_type), HIP_ID)


def test_a_good_bundle_has_no_issues_for_every_record_type():
    for hi_type in fhir.RECORD_TYPES:
        assert validate.issues(make(hi_type), hi_type) == []


def mutate(change):
    bundle = copy.deepcopy(make())
    change(bundle)
    return validate.issues(bundle, "Prescription")


@pytest.mark.parametrize("change,fragment", [
    (lambda b: b.update(type="collection"), "Bundle.type"),
    (lambda b: b["meta"].update(profile=[]), "DocumentBundle"),
    (lambda b: b["meta"].pop("versionId"), "versionId"),
    (lambda b: b.pop("timestamp"), "timestamp"),
    (lambda b: b.pop("identifier"), "identifier"),
    (lambda b: b["entry"].reverse(), "first entry must be the Composition"),
    (lambda b: b["entry"][0]["resource"]["meta"].update(profile=["http://x/OPConsultRecord"]), "PrescriptionRecord"),
    (lambda b: b["entry"][0]["resource"]["type"]["coding"][0].update(code="1"), "440545006"),
    (lambda b: b["entry"][0]["resource"].pop("encounter"), "Composition.encounter"),
    (lambda b: b["entry"][0]["resource"].pop("author"), "Composition.author"),
    (lambda b: b["entry"][0]["resource"]["section"][0]["entry"].append({"reference": "urn:uuid:nope"}),
     "does not resolve"),
    (lambda b: b["entry"][1]["resource"]["meta"].pop("profile"), "meta.profile is missing"),
    (lambda b: b["entry"][1]["resource"].pop("identifier"), "Patient.identifier"),
    (lambda b: b["entry"][1].update(fullUrl="http://x/Patient/1"), "fullUrl"),
    (lambda b: b["entry"][0]["resource"]["section"].append(b["entry"][0]["resource"]["section"][0]), "exactly one"),
])
def test_known_mistakes_are_reported(change, fragment):
    assert any(fragment in issue for issue in mutate(change)), mutate(change)


def test_base_fhir_structure_errors_are_reported():
    issues = mutate(lambda b: b["entry"][1]["resource"].update(birthDate="14/03/1985"))
    assert any("birthDate" in i for i in issues)


def test_diagnostic_report_record_allows_one_report_and_one_document():
    bundle = make("DiagnosticReport")
    report = next(e for e in bundle["entry"] if e["resource"]["resourceType"] == "DiagnosticReport")
    section = bundle["entry"][0]["resource"]["section"][0]
    section["entry"].append({"reference": report["fullUrl"], "type": "DiagnosticReport"})
    assert any("one DiagnosticReport" in i for i in validate.issues(bundle, "DiagnosticReport"))


def test_invoice_record_needs_exactly_one_invoice():
    bundle = make("Invoice")
    bundle["entry"] = [e for e in bundle["entry"] if e["resource"]["resourceType"] != "Invoice"]
    assert any("Invoice" in i for i in validate.issues(bundle, "Invoice"))
