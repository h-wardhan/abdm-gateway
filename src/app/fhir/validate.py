"""
Checks run on every Bundle before it leaves the gateway.

1. Structure: fhir.resources (FHIR R4B models) parses the whole Bundle: types, cardinalities and value shapes of base
   FHIR.
2. ABDM must-haves: what the NRCeS profiles require of a document that base FHIR does not: the DocumentBundle
   fields, the Composition profile and its fixed type code, resolvable references, identifiers on the shared
   resources, and the entry limits of each record profile.

Terminology and full profile conformance are checked offline with the HAPI validator in the test suite
(tests/fhir/test_hapi_validation.py); this module is the cheap runtime guard.
"""
from src.app.fhir import constants as c

# hiType -> (Composition profile, fixed Composition.type code or None, max entries per section rule)
RECORDS = {
    "Prescription": ("PrescriptionRecord", "440545006"),
    "OPConsultation": ("OPConsultRecord", "371530004"),
    "DiagnosticReport": ("DiagnosticReportRecord", "721981007"),
    "DischargeSummary": ("DischargeSummaryRecord", "373942005"),
    "ImmunizationRecord": ("ImmunizationRecord", "41000179103"),
    "HealthDocumentRecord": ("HealthDocumentRecord", "419891008"),
    "WellnessRecord": ("WellnessRecord", None),
    "Invoice": ("InvoiceRecord", None),
}
_REQUIRED = {
    "Patient": ("identifier", "name"),
    "Practitioner": ("identifier", "name"),
    "Organization": ("identifier", "name"),
    "Encounter": ("status", "class", "subject"),
    "Composition": ("status", "type", "subject", "date", "author", "title", "section"),
}


_warned_unchecked = False


def _structural_check_enabled():
    """True unless a dev machine switched the structural check off (FHIR_STRUCTURAL_CHECK=false; never in production)."""
    global _warned_unchecked
    from flask import current_app, has_app_context
    if not has_app_context() or current_app.config.get("FHIR_STRUCTURAL_CHECK", True):
        return True
    if not _warned_unchecked:
        _warned_unchecked = True
        current_app.logger.warning("FHIR structural check is OFF (FHIR_STRUCTURAL_CHECK=false): Bundles are only checked "
                                   "against the ABDM must-haves")
    return False


def structural_issues(bundle):
    if not _structural_check_enabled():
        return []
    from fhir.resources.R4B.bundle import Bundle
    from pydantic import ValidationError
    try:
        Bundle.model_validate(bundle)
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return []


def _references(node):
    if isinstance(node, dict):
        ref = node.get("reference")
        if isinstance(ref, str):
            yield ref
        for value in node.values():
            yield from _references(value)
    elif isinstance(node, list):
        for value in node:
            yield from _references(value)


def abdm_issues(bundle, hi_type):
    issues = []
    profile_name, type_code = RECORDS[hi_type]

    if c.DOCUMENT_BUNDLE not in bundle.get("meta", {}).get("profile", []):
        issues.append("Bundle.meta.profile must contain DocumentBundle")
    if not bundle.get("meta", {}).get("versionId"):
        issues.append("Bundle.meta.versionId is required")
    identifier = bundle.get("identifier") or {}
    if not (identifier.get("system") and identifier.get("value")):
        issues.append("Bundle.identifier needs system and value")
    if bundle.get("type") != "document":
        issues.append("Bundle.type must be 'document'")
    if not bundle.get("timestamp"):
        issues.append("Bundle.timestamp is required")

    entries = bundle.get("entry") or []
    if not entries or entries[0].get("resource", {}).get("resourceType") != "Composition":
        return issues + ["The first entry must be the Composition"]

    urls = {e.get("fullUrl"): e.get("resource", {}) for e in entries}
    for url, res in urls.items():
        if not (url or "").startswith("urn:uuid:") or url != f"urn:uuid:{res.get('id')}":
            issues.append(f"{res.get('resourceType')}: fullUrl must be urn:uuid:<id>")
        if not res.get("meta", {}).get("profile"):
            issues.append(f"{res.get('resourceType')}/{res.get('id')}: meta.profile is missing")
        for field in _REQUIRED.get(res.get("resourceType"), ()):
            if not res.get(field):
                issues.append(f"{res.get('resourceType')}.{field} is required")

    composition = entries[0]["resource"]
    if c.profile(profile_name) not in composition.get("meta", {}).get("profile", []):
        issues.append(f"Composition.meta.profile must be {profile_name}")
    if type_code:
        codes = [x.get("code") for x in composition.get("type", {}).get("coding", [])]
        if codes != [type_code]:
            issues.append(f"Composition.type must be SNOMED {type_code}, got {codes}")
    if not composition.get("encounter"):
        issues.append("Composition.encounter is required")

    resource_types = {r.get("resourceType") for r in urls.values()}
    for needed in ("Patient", "Practitioner", "Organization", "Encounter"):
        if needed not in resource_types:
            issues.append(f"The Bundle has no {needed}")
    for ref in _references(bundle.get("entry")):
        if ref.startswith("urn:uuid:") and ref not in urls:
            issues.append(f"Reference {ref} does not resolve inside the Bundle")

    sections = composition.get("section") or []
    kinds = [urls.get(e.get("reference"), {}).get("resourceType") for s in sections for e in s.get("entry", [])]
    if hi_type in ("Prescription", "DiagnosticReport", "ImmunizationRecord", "HealthDocumentRecord", "Invoice") \
            and len(sections) != 1:
        issues.append(f"{profile_name} has exactly one section, got {len(sections)}")
    if hi_type == "DiagnosticReport" and (kinds.count("DiagnosticReport") > 1 or kinds.count("DocumentReference") > 1):
        issues.append("DiagnosticReportRecord allows one DiagnosticReport and one DocumentReference")
    if hi_type == "ImmunizationRecord" and kinds.count("ImmunizationRecommendation") > 1:
        issues.append("ImmunizationRecord allows one ImmunizationRecommendation")
    if hi_type == "Prescription" and kinds.count("Binary") > 1:
        issues.append("PrescriptionRecord allows one Binary")
    if hi_type == "Invoice" and kinds.count("Invoice") != 1:
        issues.append("InvoiceRecord needs exactly one Invoice")
    return issues


def issues(bundle, hi_type):
    return structural_issues(bundle) + abdm_issues(bundle, hi_type)
