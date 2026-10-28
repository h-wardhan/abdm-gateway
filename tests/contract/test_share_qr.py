"""
POST /api/v1/share/qr: the clinic's Scan & Share QR, called the way i-medicus calls it (ABDMService::generateQrCode):
body `hipId` + `callbackUrl`, answer read as `type == "Success"` and `data` = the PNG as `<img src>`.
"""
import base64
import io
import json

import segno
from PIL import Image

from tests.conftest import HIP_ID

PNG_PREFIX = "data:image/png;base64,"
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
    assert data["payload"] == {"hipId": HIP_ID, "hipName": "Test clinic"}


def test_qr_holds_the_payload(client, auth, hip):
    """The QR is rendered from the compact JSON of `payload`: same text in, same picture out."""
    data = client.post("/api/v1/share/qr", json={**IMEDICUS_BODY, "counterId": "desk-1"}, headers=auth).get_json()
    assert data["payload"] == {"hipId": HIP_ID, "hipName": "Test clinic", "counterId": "desk-1"}
    image = Image.open(io.BytesIO(_decode(data["data"])))
    assert image.format == "PNG" and image.width == image.height and image.width > 100
    expected = segno.make(json.dumps(data["payload"], separators=(",", ":")), error="m")
    assert _decode(data["data"]) == _decode(expected.png_data_uri(scale=8, border=4))


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


def test_qr_needs_login(client, hip):
    assert client.post("/api/v1/share/qr", json=IMEDICUS_BODY).status_code == 401
