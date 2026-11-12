"""
M2 end to end (WBS Day 15), two clinics, ABDM mocked:

  clinic A (HIP)   discovery -> linking -> consent -> health-information request -> FHIR -> encrypt -> push -> notify
  clinic B (HIU)   consent request -> consent notification -> consent fetch -> health-information request -> decrypt

ABDM (the consent manager) is played by the test: it relays what clinic B asked for to clinic A as callbacks, using the
bodies the gateway really sent it. Clinic A pushes to clinic B's push URL; clinic B decrypts with its own key and must
read the Bundle clinic A's FHIR route built, which must be valid.
"""
import json

import responses

from src.app.callbacks import registry
from src.app.extensions import db
from src.app.fhir import validate
from src.app.models import ApiAuditLog, ApiClient, HipTransfer, OutboxJob
from tests.conftest import CALLBACK_URL, GATEWAY, HIP_ID, make_jwt
from tests.fhir.samples import sample

HIECM = f"{GATEWAY}/api/hiecm"
SESSION = f"{HIECM}/gateway/v3/sessions"
ON_DISCOVER = f"{HIECM}/user-initiated-linking/v3/patient/care-context/on-discover"
ON_INIT = f"{HIECM}/user-initiated-linking/v3/link/care-context/on-init"
ON_CONFIRM = f"{HIECM}/user-initiated-linking/v3/link/care-context/on-confirm"
HIP_CONSENT_ACK = f"{HIECM}/consent/v3/request/hip/on-notify"
HI_ACK = f"{HIECM}/data-flow/v3/health-information/hip/on-request"
HI_NOTIFY = f"{HIECM}/data-flow/v3/health-information/notify"
HIU_CONSENT_INIT = f"{HIECM}/consent/v3/request/init"
HIU_CONSENT_ACK = f"{HIECM}/consent/v3/request/hiu/on-notify"
HIU_FETCH = f"{HIECM}/consent/v3/fetch"
HIU_HI_REQUEST = f"{HIECM}/data-flow/v3/health-information/request"
ALL_ABDM = [ON_DISCOVER, ON_INIT, ON_CONFIRM, HIP_CONSENT_ACK, HI_ACK, HI_NOTIFY, HIU_CONSENT_INIT, HIU_CONSENT_ACK,
            HIU_FETCH, HIU_HI_REQUEST]

HIU_ID = "IN0110005723_241"
PUSH_URL = "https://hiu.example.test/api/abdm-webhook"
TXN = "a1b2c3d4-0000-1111-2222-333344445555"
CONSENT_ID = "ed486d33-3efa-4b8e-9a5c-47359467c20d"
PATIENT = [{"referenceNumber": "P-1", "display": "Ravi", "hiType": "Prescription", "count": 1,
            "careContexts": [{"referenceNumber": "CC-1", "display": "Visit 1"}]}]


def body_of(mock, url):
    calls = [c for c in mock.calls if c.request.url == url]
    assert len(calls) == 1, f"expected 1 call to {url}, got {len(calls)}"
    return json.loads(calls[0].request.body)


def test_two_clinics_from_discovery_to_a_decrypted_valid_record(client, jwks, hip, auth, fake_dns):
    fake_dns["hiu.example.test"] = "93.184.216.35"
    jwks.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    for url in ALL_ABDM:
        jwks.add(responses.POST, url, json={}, status=202)

    # clinic B: a second client app with its own facility
    tenant_b, secret_b = ApiClient.create(name="clinic B")
    db.session.add(tenant_b)
    db.session.commit()
    registry.upsert(tenant_b, HIU_ID, f"{CALLBACK_URL}/b", "Clinic B")
    auth_b = {"Client-Id": tenant_b.client_id, "Client-Secret": secret_b}

    def abdm_calls(path, body, request_id, facility_header, facility):
        res = client.post(f"/api/{path}", data=json.dumps(body), headers={
            "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, facility_header: facility,
            "Content-Type": "application/json"})
        assert res.status_code == 202, res.get_json()
        OutboxJob.query.filter_by(kind="forward_callback").delete()   # delivery to the apps is not what this test is about
        db.session.commit()

    def ok(res, status=200):
        assert res.status_code == status, res.get_json()
        return res.get_json()["data"]

    # ---- clinic A: discovery and linking (a patient finds the clinic in the ABHA app) ------------------------------
    abdm_calls("v3/hip/patient/care-context/discover", {"transactionId": TXN, "patient": {"id": "ravi@sbx"}},
               "req-discover", "X-HIP-ID", HIP_ID)
    ok(client.post("/api/v1/hip/care-context/on-discover", headers=auth, json={
        "transactionId": TXN, "patient": PATIENT, "matchedBy": ["MR"], "response": {"requestId": "req-discover"}}))
    abdm_calls("v3/hip/link/care-context/init", {"transactionId": TXN, "patient": {"id": "ravi@sbx"}},
               "req-link-init", "X-HIP-ID", HIP_ID)
    ok(client.post("/api/v1/hip/link/on-init", headers=auth, json={
        "transactionId": TXN, "link": {"referenceNumber": "P-1", "authenticationType": "DIRECT",
                                       "meta": {"communicationMedium": "MOBILE"}},
        "response": {"requestId": "req-link-init"}}))
    abdm_calls("v3/hip/link/care-context/confirm", {"confirmation": {"linkRefNumber": "L-1", "token": "123456"}},
               "req-link-confirm", "X-HIP-ID", HIP_ID)
    ok(client.post("/api/v1/hip/link/on-confirm", headers=auth, json={
        "patient": PATIENT, "response": {"requestId": "req-link-confirm"}}))

    # ---- clinic B (HIU): asks for the consent, gets the grant, fetches it, asks for the records --------------------
    ok(client.post("/api/v1/hiu/consent/init", headers=auth_b, json={
        "patientId": "ravi@sbx", "requesterName": "Dr. Bela Shah", "requesterType": "REGN", "requesterValue": "MH202",
        "fromDate": "2026-10-01T00:00:00.000Z", "toDate": "2026-11-01T00:00:00.000Z",
        "dataEraseAt": "2126-11-30T00:00:00.000Z", "hiType": ["Prescription"], "purposeOfRequest": "CAREMGT",
        "hipId": HIU_ID, "requestId": "6f1c2a9e-1111-4222-8333-444455556666"}), 202)
    abdm_calls("v3/hiu/consent/request/notify", {"notification": {
        "status": "GRANTED", "consentRequestId": "creq-1", "consentArtefacts": [{"id": CONSENT_ID}]}},
        "req-hiu-notify", "X-HIU-ID", HIU_ID)
    ok(client.post("/api/v1/hiu/consent/on-notify", headers=auth_b, json={
        "acknowledgement": [{"status": "OK", "consentId": CONSENT_ID}], "response": {"requestId": "req-hiu-notify"},
        "hipId": HIU_ID}))
    ok(client.post("/api/v1/hiu/consent/fetch", headers=auth_b, json={"consentId": CONSENT_ID, "hipId": HIU_ID}))

    keys_b = client.get("/api/v1/fhir/keys", headers=auth_b).get_json()          # clinic B's keys (getEncryptedKeysM3)
    ok(client.post("/api/v1/hiu/health-information/request", headers=auth_b, json={
        "consentId": CONSENT_ID, "fromDate": "2026-10-10T00:00:00.000Z", "toDate": "2026-11-01T00:00:00.000Z",
        "dataPushUrl": PUSH_URL, "expiryDate": "2126-11-30T00:00:00.000Z", "publicKey": keys_b["publicKey"],
        "nonce": keys_b["nonce"], "hipId": HIU_ID, "requestId": "6f1c2a9e-2222-4222-8333-444455556666"}), 202)

    # ---- ABDM relays the consent and the request to clinic A, exactly as clinic B's request was sent ----------------
    hi_request = body_of(jwks, HIU_HI_REQUEST)["hiRequest"]
    abdm_calls("v3/consent/request/hip/notify", {"notification": {
        "status": "GRANTED", "consentId": CONSENT_ID, "consentDetail": {
            "consentId": CONSENT_ID, "patient": {"id": "ravi@sbx"}, "hip": {"id": HIP_ID}, "hiu": {"id": HIU_ID},
            "careContexts": [{"patientReference": "P-1", "careContextReference": "CC-1"}], "hiTypes": ["Prescription"],
            "permission": {"accessMode": "VIEW", "dateRange": {"from": "2026-10-01T00:00:00.000Z",
                                                              "to": "2026-11-01T00:00:00.000Z"},
                           "dataEraseAt": "2126-11-30T00:00:00.000Z"}}}}, "req-consent", "X-HIP-ID", HIP_ID)
    ok(client.post("/api/v1/hip/consent/on-notify", headers=auth, json={"consentId": CONSENT_ID,
                                                                        "requestId": "req-consent", "hipId": HIP_ID}), 201)
    abdm_calls("v3/hip/health-information/request", {"transactionId": TXN, "hiRequest": hi_request},
               "req-hi", "X-HIP-ID", HIP_ID)
    ok(client.post("/api/v1/hip/health-information/on-request", headers=auth,
                   json={"transactionId": TXN, "requestId": "req-hi"}))
    assert HipTransfer.query.one().data_push_url == PUSH_URL

    # ---- clinic A: the record, encrypted for clinic B, pushed ----------------------------------------------------------
    record = client.post("/api/v1/fhir/prescription", headers=auth, json=sample("Prescription"))
    assert record.status_code == 200
    bundle = record.get_json()
    assert validate.issues(bundle, "Prescription") == []

    sender = client.get("/api/v1/fhir/keys", headers=auth).get_json()
    encrypted = client.post("/api/v1/fhir/encrypt", headers=auth, json={
        "receiverPublicKey": hi_request["keyMaterial"]["dhPublicKey"]["keyValue"],
        "receiverNonce": hi_request["keyMaterial"]["nonce"], "senderPrivateKey": sender["privateKey"],
        "senderPublicKey": sender["publicKey"], "senderNonce": sender["nonce"],
        "plainTextData": json.dumps(bundle, separators=(",", ":"))}).get_json()
    jwks.add(responses.POST, PUSH_URL, json={}, status=200)
    pushed = ok(client.post("/api/v1/hip/health-information/push", headers=auth, json={
        "transactionId": TXN, "pushUrl": PUSH_URL, "expiry": hi_request["keyMaterial"]["dhPublicKey"]["expiry"],
        "entries": [{"content": encrypted["encryptedData"], "checksum": "x", "media": "application/fhir+json",
                     "careContextReference": "CC-1"}],
        "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "dhPublicKey": {
            "expiry": hi_request["keyMaterial"]["dhPublicKey"]["expiry"], "parameters": "Ephemeral public key",
            "keyValue": encrypted["keyToShare"]}, "nonce": sender["nonce"]}}))
    assert pushed["status"] == "sent"
    ok(client.post("/api/v1/hip/health-information/notify", headers=auth, json={"notification": {
        "consentId": CONSENT_ID, "transactionId": TXN, "doneAt": "2026-11-04T10:00:00.000Z",
        "notifier": {"type": "HIP", "id": HIP_ID},
        "statusNotification": {"sessionStatus": "TRANSFERRED", "hipId": HIP_ID, "statusResponses": [
            {"careContextReference": "CC-1", "hiStatus": "DELIVERED", "description": "Data Pushed"}]}}}))

    # ---- clinic B: the push arrives at its webhook, it decrypts with its own key ---------------------------------------
    received = json.loads([c for c in jwks.calls if c.request.url == PUSH_URL][0].request.body)
    decrypted = client.post("/api/v1/fhir/decrypt", headers=auth_b, json={
        "receiverPrivateKey": keys_b["privateKey"], "receiverNonce": keys_b["nonce"],
        "senderPublicKey": received["keyMaterial"]["dhPublicKey"]["keyValue"],
        "senderNonce": received["keyMaterial"]["nonce"], "encryptedData": received["entries"][0]["content"]})
    assert decrypted.status_code == 200
    obj = json.loads(json.loads(decrypted.get_data()))                 # what i-medicus' double json_decode yields
    assert json.loads(obj["decryptedData"]) == bundle                  # ... and what its viewer reads

    # ---- the trail --------------------------------------------------------------------------------------------------
    transfer = HipTransfer.query.one()
    assert transfer.status == "NOTIFIED" and transfer.pushed_at and transfer.notified_at and transfer.attempts == 1
    assert body_of(jwks, HI_NOTIFY)["notification"]["statusNotification"]["sessionStatus"] == "TRANSFERRED"
    audited = {row.route for row in ApiAuditLog.query.all()}
    assert {"/api/v1/hip/health-information/push", "/api/v1/hip/health-information/notify",
            "/api/v1/hiu/health-information/request", "/api/v1/fhir/prescription"} <= audited
    assert all(row.http_status < 400 for row in ApiAuditLog.query.filter(ApiAuditLog.route.like("/api/v1/%")).all()
               if row.route != "/api/v1/auth/session")
