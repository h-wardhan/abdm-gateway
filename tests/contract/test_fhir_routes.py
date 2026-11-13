"""
FHIR routes, Days 12-13: the paths that replace VCDoctor's `api/v2/ABDMVC/Convert*`.

i-medicus keeps its request bodies (what WebhookTrait::FHIRApi builds) and reads the answer as a Bundle at the top
level, because ConvertRecordToFHIR json_encodes the whole response and hands it to Fidelius as `plainTextData`.
"""
import json

import pytest

from src.app.extensions import db
from src.app.models import ApiClient
from tests.fhir.samples import HIP_ID, sample

# i-medicus endpoint it used to call -> (gateway path, hiType)
ROUTES = {
    "ConvertPrescription": ("prescription", "Prescription"),
    "ConvertOPConsultation": ("op-consultation", "OPConsultation"),
    "ConvertDiagnosticReportRecordToFHIR": ("diagnostic-report", "DiagnosticReport"),
    "ConvertDischargeSummary": ("discharge-summary", "DischargeSummary"),
    "ConvertImmunizationRecordToFHIR": ("immunization", "ImmunizationRecord"),
    "ConvertHealthDocumentRecordToFHIR": ("health-document", "HealthDocumentRecord"),
    "ConvertWellnessRecordToFHIR": ("wellness", "WellnessRecord"),
    "ConvertInvoiceRecordToFHIR": ("invoice", "Invoice"),
}


@pytest.fixture
def hip_auth(client, app):
    """Headers of a client registered for a facility, the way i-medicus sends its credentials."""
    tenant, secret = ApiClient.create(name="clinic", hip_id=HIP_ID)
    db.session.add(tenant)
    db.session.commit()
    return {"Client-Id": tenant.client_id, "Client-Secret": secret}


def post(client, headers, slug, payload):
    return client.post(f"/api/v1/fhir/{slug}", json=payload, headers=headers)


@pytest.mark.parametrize("old,route", ROUTES.items())
def test_each_old_endpoint_has_a_route_answering_a_bundle_at_the_top_level(client, hip_auth, old, route):
    slug, hi_type = route
    res = post(client, hip_auth, slug, sample(hi_type))

    assert res.status_code == 200, res.get_json()
    bundle = res.get_json()
    # raw Bundle: no {success, data} envelope, because i-medicus encrypts the whole body
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "document"
    assert "success" not in bundle and "data" not in bundle
    assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"
    assert json.loads(res.get_data()) == bundle


def test_the_route_slugs_cover_every_endpoint_of_ConvertRecordToFHIR():
    assert len(ROUTES) == 8 and len({s for s, _ in ROUTES.values()}) == 8


def test_the_organization_is_the_clients_hip(client, hip_auth):
    bundle = post(client, hip_auth, "prescription", sample("Prescription")).get_json()
    organization = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Organization")
    assert organization["identifier"][0]["value"] == HIP_ID


def test_a_session_token_works_like_the_credentials(client, auth):
    res = client.post("/api/v1/fhir/health-document", json=sample("HealthDocumentRecord"), headers=auth)
    assert res.status_code == 200 and res.get_json()["resourceType"] == "Bundle"


@pytest.mark.parametrize("slug", [s for s, _ in ROUTES.values()] + ["validate"])
def test_every_route_needs_authentication(client, slug):
    res = client.post(f"/api/v1/fhir/{slug}", json={})
    assert res.status_code == 401
    assert res.get_json()["success"] is False


def test_wrong_credentials_are_refused(client, hip_auth):
    res = post(client, {**hip_auth, "Client-Secret": "nope"}, "prescription", sample("Prescription"))
    assert res.status_code == 401


@pytest.mark.parametrize("payload", [
    {}, {"success": False, "data": []}, {"success": False, "message": "No data found"},
])
def test_an_i_medicus_error_body_is_422_with_the_standard_error_envelope(client, hip_auth, payload):
    # FHIRApi posts ['success' => false, 'data' => []] when its builder failed; VCDoctor would have 500'd
    res = post(client, hip_auth, "immunization", payload)
    body = res.get_json()
    assert res.status_code == 422
    assert body["success"] is False and body["message"]["error"]["code"] == "INVALID_RECORD"


def test_a_missing_field_is_named_in_the_422(client, hip_auth):
    payload = sample("Prescription")
    del payload["patient"]["healthId"]
    res = post(client, hip_auth, "prescription", payload)
    assert res.status_code == 422
    assert res.get_json()["message"]["error"]["details"] == {"field": "patient.healthId"}


def test_a_bad_pdf_is_422(client, hip_auth):
    payload = sample("HealthDocumentRecord")
    payload["documentReference"][0]["base64Data"] = None
    res = post(client, hip_auth, "health-document", payload)
    assert res.status_code == 422 and "no content" in json.dumps(res.get_json())


def test_a_non_json_body_is_422_not_500(client, hip_auth):
    res = client.post("/api/v1/fhir/prescription", data="not json", headers=hip_auth)
    assert res.status_code == 422


def test_a_large_payload_with_several_pdfs_is_accepted(client, hip_auth):
    import base64
    from tests.fhir import samples
    big = base64.b64encode(samples.PDF + b"0" * 2_000_000).decode()
    payload = sample("HealthDocumentRecord")
    payload["documentReference"] = [{"contentType": "application/pdf", "base64Data": big, "title": f"d{i}"}
                                    for i in range(3)]
    res = post(client, hip_auth, "health-document", payload)
    assert res.status_code == 200
    assert len([e for e in res.get_json()["entry"] if e["resource"]["resourceType"] == "DocumentReference"]) == 3


# ---- validate -----------------------------------------------------------------------------------------------------

def test_validate_accepts_a_good_bundle_and_reports_a_bad_one(client, hip_auth):
    bundle = post(client, hip_auth, "prescription", sample("Prescription")).get_json()
    good = client.post("/api/v1/fhir/validate", json={"hiType": "Prescription", "bundle": bundle},
                       headers=hip_auth).get_json()
    assert good == {"valid": True, "issues": [], "igVersion": "6.5.0"}

    bundle["type"] = "collection"
    bad = client.post("/api/v1/fhir/validate", json={"hiType": "Prescription", "bundle": bundle},
                      headers=hip_auth).get_json()
    assert bad["valid"] is False and any("Bundle.type" in i for i in bad["issues"])


def test_validate_refuses_unknown_types_and_non_bundles(client, hip_auth):
    unknown = client.post("/api/v1/fhir/validate", json={"hiType": "Radiology", "bundle": {}}, headers=hip_auth)
    assert unknown.status_code == 422
    not_bundle = client.post("/api/v1/fhir/validate", json={"hiType": "Prescription", "bundle": {"a": 1}},
                             headers=hip_auth)
    assert not_bundle.status_code == 422
    assert client.post("/api/v1/fhir/validate", json={"bundle": {}}, headers=hip_auth).status_code == 400


# ---- request size and audit ---------------------------------------------------------------------------------------

def test_other_routes_keep_the_one_megabyte_limit(client, hip_auth):
    res = client.post("/api/v1/hip/sms/notify", json={"x": "a" * 2_000_000}, headers=hip_auth)
    assert res.status_code == 413


def test_the_fhir_limit_is_configurable(app, client, hip_auth):
    app.config["FHIR_MAX_CONTENT_LENGTH"] = 1000
    assert post(client, hip_auth, "prescription", sample("Prescription")).status_code == 413


def test_the_audit_log_keeps_the_call_but_never_the_health_record(client, hip_auth):
    from src.app.models import ApiAuditLog
    import base64
    from tests.fhir import samples
    payload = sample("Prescription")
    payload["binaryDocument"]["base64Data"] = base64.b64encode(samples.PDF + b"0" * 300_000).decode()
    assert post(client, hip_auth, "prescription", payload).status_code == 200
    row = ApiAuditLog.query.filter_by(route="/api/v1/fhir/prescription").one()
    assert row.http_status == 200
    assert row.request_body == {"note": "not stored: health record or key material"}
    assert "Ravi Kumar" not in json.dumps(row.request_body)


def test_the_audit_log_never_keeps_private_keys_or_the_record_of_encrypt_and_decrypt(client, hip_auth):
    from src.app.models import ApiAuditLog
    hiu = client.get("/api/v1/fhir/keys", headers=hip_auth).get_json()
    sender = client.get("/api/v1/fhir/keys", headers=hip_auth).get_json()
    plain = json.dumps({"resourceType": "Bundle", "patient": "Ravi Kumar"})
    enc = client.post("/api/v1/fhir/encrypt", headers=hip_auth, json={
        "receiverPublicKey": hiu["publicKey"], "receiverNonce": hiu["nonce"], "senderPrivateKey": sender["privateKey"],
        "senderPublicKey": sender["publicKey"], "senderNonce": sender["nonce"], "plainTextData": plain}).get_json()
    assert client.post("/api/v1/fhir/decrypt", headers=hip_auth, json={
        "receiverPrivateKey": hiu["privateKey"], "receiverNonce": hiu["nonce"], "senderPublicKey": enc["keyToShare"],
        "senderNonce": sender["nonce"], "encryptedData": enc["encryptedData"]}).status_code == 200
    stored = json.dumps([r.request_body for r in ApiAuditLog.query.filter(ApiAuditLog.route.like("/api/v1/fhir/%")).all()])
    for secret in (hiu["privateKey"], sender["privateKey"], "Ravi Kumar", enc["encryptedData"][:40]):
        assert secret not in stored


def test_the_audit_log_masks_private_keys_and_records_on_any_route(client, hip_auth):
    from src.app.models import ApiAuditLog
    client.post("/api/v1/hip/sms/notify", headers=hip_auth, json={
        "senderPrivateKey": "PRIVATE-KEY-VALUE", "receiverPrivateKey": "OTHER-PRIVATE-KEY", "plainTextData": "THE RECORD"})
    row = ApiAuditLog.query.filter_by(route="/api/v1/hip/sms/notify").one()
    assert row.request_body["senderPrivateKey"] == "***" and row.request_body["receiverPrivateKey"] == "***"
    assert row.request_body["plainTextData"] == "***"
