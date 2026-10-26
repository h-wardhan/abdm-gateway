import pytest

from src.app.callbacks import vault
from src.app.models import HipRegistration
from tests.conftest import CALLBACK_URL, HIP_ID

URL = f"/api/v1/hips/{HIP_ID}"


def test_requires_a_token(client):
    assert client.get("/api/v1/hips").status_code == 401
    assert client.put(URL, json={"callbackUrl": CALLBACK_URL}).status_code == 401
    assert client.delete(URL).status_code == 401


def test_register_returns_the_secret_once(client, auth):
    res = client.put(URL, json={"callbackUrl": CALLBACK_URL, "name": "City Clinic"}, headers=auth)
    assert res.status_code == 201
    data = res.get_json()["data"]
    assert data["hipId"] == HIP_ID and data["callbackUrl"] == CALLBACK_URL
    assert data["name"] == "City Clinic" and data["isActive"] is True
    secret = data["webhookSecret"]
    assert vault.open_secret(HipRegistration.query.one().webhook_secret_enc) == secret

    again = client.put(URL, json={"callbackUrl": CALLBACK_URL + "/v2"}, headers=auth)
    assert again.status_code == 200
    assert "webhookSecret" not in again.get_json()["data"]
    assert again.get_json()["data"]["callbackUrl"] == CALLBACK_URL + "/v2"
    assert HipRegistration.query.count() == 1


def test_snake_case_alias(client, auth):
    res = client.put(URL, json={"callback_url": CALLBACK_URL}, headers=auth)
    assert res.status_code == 201


def test_callback_url_is_required(client, auth):
    res = client.put(URL, json={}, headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["details"] == {"field": "callbackUrl"}


@pytest.mark.parametrize("url,code", [
    ("http://client.example.test/hook", "UNSAFE_CALLBACK_URL"),
    ("https://app.i-medicus.in/api/abdm-webhook", "UNSAFE_CALLBACK_URL"),
    ("https://10.0.0.5/hook", "UNSAFE_CALLBACK_URL"),
    ("https://missing.example.test/hook", "CALLBACK_HOST_UNRESOLVABLE"),
])
def test_unsafe_callback_urls_are_rejected(client, auth, url, code):
    res = client.put(URL, json={"callbackUrl": url}, headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == code
    assert HipRegistration.query.count() == 0


def test_invalid_hip_id(client, auth):
    res = client.put("/api/v1/hips/x", json={"callbackUrl": CALLBACK_URL}, headers=auth)
    assert res.status_code == 400
    assert res.get_json()["message"]["error"]["code"] == "INVALID_HIP_ID"


def test_hip_of_another_client_cannot_be_taken_or_seen(client, auth, other_auth):
    assert client.put(URL, json={"callbackUrl": CALLBACK_URL}, headers=auth).status_code == 201

    taken = client.put(URL, json={"callbackUrl": CALLBACK_URL}, headers=other_auth)
    assert taken.status_code == 409
    assert taken.get_json()["message"]["error"]["code"] == "HIP_TAKEN"
    assert client.get(URL, headers=other_auth).status_code == 404
    assert client.delete(URL, headers=other_auth).status_code == 404
    assert client.get("/api/v1/hips", headers=other_auth).get_json()["data"] == []


def test_list_and_get(client, auth):
    client.put(URL, json={"callbackUrl": CALLBACK_URL}, headers=auth)
    client.put("/api/v1/hips/IN0000000002", json={"callbackUrl": CALLBACK_URL}, headers=auth)
    listed = client.get("/api/v1/hips", headers=auth).get_json()["data"]
    assert [h["hipId"] for h in listed] == [HIP_ID, "IN0000000002"]
    assert all("webhookSecret" not in h for h in listed)

    one = client.get(URL, headers=auth).get_json()["data"]
    assert one["hipId"] == HIP_ID and "webhookSecret" not in one
    assert client.get("/api/v1/hips/IN-NOT-THERE", headers=auth).status_code == 404


def test_delete_disables_and_put_reactivates(client, auth):
    client.put(URL, json={"callbackUrl": CALLBACK_URL}, headers=auth)
    res = client.delete(URL, headers=auth)
    assert res.status_code == 200 and res.get_json()["data"]["isActive"] is False
    assert HipRegistration.query.one().is_active is False

    again = client.put(URL, json={"callbackUrl": CALLBACK_URL}, headers=auth)
    assert again.get_json()["data"]["isActive"] is True


def test_hips_appear_in_swagger(client):
    paths = client.get("/swagger.json").get_json()["paths"]
    assert "/api/v1/hips/{hip_id}" in paths
