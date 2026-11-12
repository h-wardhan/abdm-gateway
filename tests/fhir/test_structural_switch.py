"""FHIR_STRUCTURAL_CHECK: the runtime structural check (fhir.resources / pydantic) can be switched off on a dev machine
whose OS blocks pydantic's DLL (Windows Smart App Control); production always keeps it on."""
from src.app.fhir import validate
from src.config.index import ProductionConfig


def test_it_is_on_by_default(app):
    assert app.config["FHIR_STRUCTURAL_CHECK"] is True


def test_production_keeps_it_on_whatever_the_environment_says(monkeypatch):
    monkeypatch.setenv("FHIR_STRUCTURAL_CHECK", "false")
    assert ProductionConfig.FHIR_STRUCTURAL_CHECK is True


def test_switched_off_the_structure_is_not_parsed_but_the_abdm_rules_still_run(app):
    app.config["FHIR_STRUCTURAL_CHECK"] = False
    with app.app_context():
        assert validate.structural_issues({"resourceType": "Bundle"}) == []  # does not even import fhir.resources
        issues = validate.issues({"resourceType": "Bundle", "type": "document", "entry": []}, "Prescription")
    assert issues, "the ABDM must-haves still reject an empty document"
