"""
POST /api/v1/share/qr: the clinic's Scan & Share QR, called the way i-medicus calls it (ABDMService::generateQrCode):
body `hipId` + `callbackUrl`, answer read as `type == "Success"` and `data` = the PNG as `<img src>`.
"""
import base64
import io

import segno
from PIL import Image

from tests.conftest import HIP_ID

PNG_PREFIX = "data:image/png;base64,"
SHARE_URL = "https://phrsbx.abdm.gov.in/share-profile"
IMEDICUS_BODY = {"hipId": HIP_ID, "callbackUrl": "http://13.201.109.37:8081/api/abdm-webhook"}


def _decode(image):
    assert image.startswith(PNG_PREFIX)
    return base64.b64decode(image[len(PNG_PREFIX):])


def test_imedicus_body_gives_the_shape_its_dashboard_reads(client, auth, hip):
    res = client.post("/api/v1/share/qr", json=IMEDICUS_BODY, headers=auth)
    assert res.status_code == 200
    data = res.get_json()
    assert data["type"] == "Success"  # front_patient_doctor_header.blade.php: $qrData['type'] === 'Success'
    assert data["code"] == HIP_ID
    assert _decode(data["data"]).startswith(b"\x89PNG\r\n\x1a\n")  # $qrData['data'] goes into <img src>
    assert data["payload"] == {"hipId": HIP_ID, "counterId": "1", "url": f"{SHARE_URL}?hip-id={HIP_ID}&counter-id=1"}


def test_qr_holds_the_url_the_abha_app_opens(client, auth, hip):
    """ABDM's format (checked in the ABHA sandbox app): a URL with `hip-id` and `counter-id`, not JSON. The app refuses
    one without the counter. The QR is rendered from `payload.url`: same text in, same picture out."""
    data = client.post("/api/v1/share/qr", json={**IMEDICUS_BODY, "counterId": "desk-1"}, headers=auth).get_json()
    assert data["payload"] == {"hipId": HIP_ID, "counterId": "desk-1",
                               "url": f"{SHARE_URL}?hip-id={HIP_ID}&counter-id=desk-1"}
    image = Image.open(io.BytesIO(_decode(data["data"])))
    assert image.format == "PNG" and image.width == image.height and image.width > 100
    expected = segno.make(data["payload"]["url"], error="m")
    assert _decode(data["data"]) == _decode(expected.png_data_uri(scale=8, border=4))


def test_without_a_counter_the_configured_default_is_used(app, client, auth, hip):
    """i-medicus sends no counter, and the ABHA sandbox app answers "Invalid QR code" to a QR without one."""
    app.config["SHARE_QR_DEFAULT_COUNTER"] = "desk9"
    data = client.post("/api/v1/share/qr", json={"hipId": HIP_ID}, headers=auth).get_json()
    assert data["payload"]["url"] == f"{SHARE_URL}?hip-id={HIP_ID}&counter-id=desk9"
    app.config["SHARE_QR_DEFAULT_COUNTER"] = "bad counter!"
    assert client.post("/api/v1/share/qr", json={"hipId": HIP_ID}, headers=auth).status_code == 400


def test_callback_url_is_optional(client, auth, hip):
    assert client.post("/api/v1/share/qr", json={"hipId": HIP_ID}, headers=auth).status_code == 200


def test_qr_needs_hip_id(client, auth, hip):
    res = client.post("/api/v1/share/qr", json={"callbackUrl": "x"}, headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"]["field"] == "hipId"


def test_qr_of_another_clients_clinic_is_not_found(client, other_auth, hip):
    res = client.post("/api/v1/share/qr", json=IMEDICUS_BODY, headers=other_auth)
    assert res.status_code == 404
    assert res.get_json()["message"]["error"]["code"] == "HIP_NOT_FOUND"


def test_qr_of_unknown_clinic_is_not_found(client, auth, hip):
    assert client.post("/api/v1/share/qr", json={"hipId": "IN9999999999"}, headers=auth).status_code == 404


def test_qr_rejects_bad_ids(client, auth, hip):
    assert client.post("/api/v1/share/qr", json={"hipId": "bad id!"}, headers=auth).status_code == 400
    assert client.post("/api/v1/share/qr", json={"hipId": HIP_ID, "counterId": "x" * 33},
                       headers=auth).status_code == 400


def test_a_counter_id_that_would_break_the_url_is_refused(client, auth, hip):
    for bad in ("a b", "a&b=1", "a/b", "ä"):
        res = client.post("/api/v1/share/qr", json={"hipId": HIP_ID, "counterId": bad}, headers=auth)
        assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "INVALID_COUNTER_ID"


def test_the_host_is_configurable_and_a_missing_one_is_a_clear_error(app, client, auth, hip):
    app.config["SHARE_QR_BASE_URL"] = "https://phr.example.test/share-profile"
    data = client.post("/api/v1/share/qr", json={"hipId": HIP_ID}, headers=auth).get_json()
    assert data["payload"]["url"] == f"https://phr.example.test/share-profile?hip-id={HIP_ID}&counter-id=1"
    app.config["SHARE_QR_BASE_URL"] = None
    res = client.post("/api/v1/share/qr", json={"hipId": HIP_ID}, headers=auth)
    assert res.status_code == 503 and res.get_json()["message"]["error"]["code"] == "SHARE_QR_NOT_CONFIGURED"


def test_qr_needs_login(client, hip):
    assert client.post("/api/v1/share/qr", json=IMEDICUS_BODY).status_code == 401
