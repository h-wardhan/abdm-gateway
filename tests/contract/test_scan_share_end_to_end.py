"""
Scan & Share the way i-medicus drives it, start to finish, with every request body i-medicus really sends:
desk QR -> patient scans -> ABDM callback -> gateway -> i-medicus webhook -> token -> share/on-share -> ABDM.
"""
import json

import responses

from src.app.outbox import worker
from tests.conftest import CALLBACK_URL, GATEWAY, HIP_ID, make_jwt

REQUEST_ID = "5f7a535d-a3fd-416b-b069-e6a0fc9b6f9d"
SESSION = f"{GATEWAY}/api/hiecm/gateway/v3/sessions"
ABDM_ON_SHARE = f"{GATEWAY}/api/hiecm/patient-share/v3/on-share"
SCAN = {
    "metaData": {"hipId": HIP_ID, "context": "hip"},
    "profile": {"patient": {"abhaNumber": "91-1234-5678-9012", "abhaAddress": "ravi@sbx", "name": "Ravi Kumar",
                            "gender": "M", "yearOfBirth": 1990, "monthOfBirth": 5, "dayOfBirth": 4,
                            "phoneNumber": "9876543210", "address": {"line": "Street 1", "district": "Delhi",
                                                                      "state": "DELHI", "pincode": "110001"}}},
}


def test_qr_scan_token_and_patient_link(client, jwks, http, hip, auth):
    # 1. i-medicus ABDMService::generateQrCode: {hipId, callbackUrl}; the dashboard reads type/data
    qr = client.post("/api/v1/share/qr", json={"hipId": HIP_ID, "callbackUrl": "https://x.test/hook"}, headers=auth)
    assert qr.status_code == 200
    qr = qr.get_json()
    assert qr["type"] == "Success" and qr["data"].startswith("data:image/png;base64,")
    assert qr["payload"]["hipId"] == HIP_ID

    # 2. the patient scans: ABDM calls the gateway, which hands it to the i-medicus webhook
    http.add(responses.POST, f"{CALLBACK_URL}/api/v3/hip/patient/share", json={"status": "success"})
    res = client.post("/api/v3/hip/patient/share", data=json.dumps(SCAN), headers={
        "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": REQUEST_ID, "Content-Type": "application/json"})
    assert res.status_code == 202
    assert worker.run(once=True) == 1
    [hook] = [c for c in http.calls if c.request.url.startswith(CALLBACK_URL)]
    assert hook.request.url.split("/api/abdm-webhook/")[1] == "api/v3/hip/patient/share"
    assert hook.request.headers["request-id"] == REQUEST_ID
    patient = json.loads(hook.request.body)["profile"]["patient"]
    # the fields handlePatientShare saves into abha_scanned_patients (and saveScannedPatient links on)
    assert patient["abhaNumber"] == "91-1234-5678-9012" and patient["abhaAddress"] == "ravi@sbx"
    assert patient["address"]["pincode"] == "110001" and patient["phoneNumber"] == "9876543210"

    # 3. i-medicus ABDMService::sharePatientOnShare: its token is an int cast to string, plus an empty userBody
    http.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    http.add(responses.POST, ABDM_ON_SHARE, json={}, status=202)
    res = client.post("/api/v1/share/on-share", headers=auth, json={
        "abhaAddress": "ravi@sbx", "tokenNumber": "3", "requestId": REQUEST_ID, "userBody": ""})
    assert res.status_code == 200 and res.get_json()["data"] == {"status": "sent", "hipId": HIP_ID}

    [call] = [c for c in http.calls if c.request.url == ABDM_ON_SHARE]
    assert call.request.headers["X-HIP-ID"] == HIP_ID
    assert json.loads(call.request.body) == {
        "acknowledgement": {"status": "SUCCESS", "abhaAddress": "ravi@sbx",
                            "profile": {"tokenNumber": "3", "expiry": "1800", "context": "hip"}},
        "response": {"requestId": REQUEST_ID},
    }


def test_repeat_scan_is_answered_with_the_same_token(client, jwks, http, hip, auth):
    """handlePatientShare re-answers a patient who scans twice with the existing token (new requestId each scan)."""
    http.add(responses.POST, f"{CALLBACK_URL}/api/v3/hip/patient/share", json={"status": "success"})
    http.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
    http.add(responses.POST, ABDM_ON_SHARE, json={}, status=202)
    for request_id in ("a-first-scan-0001", "a-second-scan-0002"):
        client.post("/api/v3/hip/patient/share", data=json.dumps(SCAN), headers={
            "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "Content-Type": "application/json"})
        worker.run(once=True)
        res = client.post("/api/v1/share/on-share", headers=auth, json={
            "abhaAddress": "ravi@sbx", "tokenNumber": "3", "requestId": request_id})
        assert res.status_code == 200
    sent = [json.loads(c.request.body) for c in http.calls if c.request.url == ABDM_ON_SHARE]
    assert [s["response"]["requestId"] for s in sent] == ["a-first-scan-0001", "a-second-scan-0002"]
    assert {s["acknowledgement"]["profile"]["tokenNumber"] for s in sent} == {"3"}
