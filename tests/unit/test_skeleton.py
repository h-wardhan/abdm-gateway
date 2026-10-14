from src.app.errors import AbdmUpstreamError, ValidationError
from src.utils.masking import mask_payload, mask_text


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert body["requestId"]
    assert res.headers["X-Request-ID"] == body["requestId"]


def test_ready(client):
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.get_json()["data"]["database"] == "ok"


def test_incoming_request_id_is_reused(client):
    res = client.get("/health", headers={"REQUEST-ID": "a1b2c3d4-0000-1111-2222-333344445555"})
    assert res.get_json()["requestId"] == "a1b2c3d4-0000-1111-2222-333344445555"


def test_unknown_route_uses_error_envelope(client):
    res = client.get("/api/v1/does-not-exist")
    assert res.status_code == 404
    body = res.get_json()
    assert body["success"] is False
    assert body["code"] == "error"
    assert body["message"]["error"]["code"] == "NOT_FOUND"
    assert body["requestId"]


def test_app_errors_map_to_envelope(app, client):
    @app.route("/_boom/validation")
    def _validation():
        raise ValidationError("Aadhaar number is not valid")

    @app.route("/_boom/abdm")
    def _abdm():
        raise AbdmUpstreamError("UIDAI Error code : 111 : no mobile", code="ABDM-1204", status=422)

    @app.route("/_boom/crash")
    def _crash():
        raise RuntimeError("secret internals")

    res = client.get("/_boom/validation")
    assert res.status_code == 400
    assert res.get_json()["message"]["error"] == {"code": "VALIDATION_ERROR", "message": "Aadhaar number is not valid"}

    res = client.get("/_boom/abdm")
    assert res.status_code == 422
    assert res.get_json()["message"]["error"]["code"] == "ABDM-1204"
    assert "UIDAI Error code : 111" in res.get_json()["message"]["error"]["message"]

    res = client.get("/_boom/crash")
    assert res.status_code == 500
    assert "secret internals" not in res.get_data(as_text=True)


def test_swagger_spec_served(client):
    res = client.get("/swagger.json")
    assert res.status_code == 200
    assert "/health" in res.get_json()["paths"]


def test_masking():
    masked = mask_payload({
        "aadhaarNumber": "123412341234",
        "otp": "123456",
        "mobileNumber": "9876543210",
        "nested": {"token": "abc", "name": "Ram"},
    })
    assert masked["aadhaarNumber"] == "********1234"
    assert masked["otp"] == "***"
    assert masked["mobileNumber"] == "******3210"
    assert masked["nested"] == {"token": "***", "name": "Ram"}
    assert mask_text("aadhaar 123412341234 mob 9876543210") == "aadhaar ********1234 mob ******3210"
    assert "***" in mask_text("Authorization: Bearer abc.def.ghi")
