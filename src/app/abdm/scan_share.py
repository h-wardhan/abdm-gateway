"""
Scan & Share, HIP side: the answer ("on-share") to a patient who scanned the facility QR.

ABDM calls us at POST /api/v3/hip/patient/share with the patient's profile; the client app (i-medicus) saves the
patient and gives them a token number; we tell ABDM that token so the patient sees it in their ABHA app.
ABDM matches the answer to the scan by the REQUEST-ID of the callback (`response.requestId`) and shows
`profile.context` (from `metaData.context` of the callback) back to the app.

The facility is the one the scan was for: the `X-HIP-ID` header is that clinic's id, never a global one.
Path and body follow ABDM's Postman collection (Scan and Share, on-share); `expiry` is in seconds (1800 there).
"""
from src.app.outbox.abdm_calls import send_or_queue

ON_SHARE_PATH = "/api/hiecm/patient-share/v3/on-share"
DEFAULT_EXPIRY = "1800"  # seconds the token is valid (the value in ABDM's examples)


def build_on_share_body(abha_address, token_number, request_id, context=None, expiry=DEFAULT_EXPIRY):
    profile = {"tokenNumber": str(token_number), "expiry": str(expiry)}
    if context:
        profile["context"] = context
    return {
        "acknowledgement": {"status": "SUCCESS", "abhaAddress": abha_address, "profile": profile},
        "response": {"requestId": request_id},
    }


def send_on_share(hip_id, body, api_client_id):
    """Send now; if ABDM is down or answers with a temporary error it is queued for the worker.
    Returns {"status": "sent" | "queued", ...} (see outbox.abdm_calls.send_or_queue)."""
    return send_or_queue(ON_SHARE_PATH, body, headers={"X-HIP-ID": hip_id}, endpoint_name="patient-share/on-share",
                         api_client_id=api_client_id)
