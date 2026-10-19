"""Duplicate-request guard (src/app/middleware/idempotency.py)."""
import responses

from tests.conftest import ABHA

AADHAAR = "999941057058"
TXN = "a1b2c3d4-0000-1111-2222-333344445555"
OTP_URL = f"{ABHA}/abha/api/v3/enrollment/request/otp"
PATH = "/api/v1/abha/enrol/aadhaar/otp"


def _otp_calls(mock):
    return [c for c in mock.calls if "enrollment/request/otp" in c.request.url]


def _code(res):
    return res.get_json()["message"]["error"]["code"]


def _send_duplicate_while_running(client, abdm, headers, body=None):
    """Mock ABDM so that, while the first request waits for ABDM, the same request is sent again."""
    seen = {}

    def callback(request):
        seen["dup"] = client.post(PATH, json=body or {"aadhaarNumber": AADHAAR}, headers=headers)
        return 200, {}, '{"txnId": "%s", "message": "OTP sent"}' % TXN

    abdm.add_callback(responses.POST, OTP_URL, callback=callback, content_type="application/json")
    first = client.post(PATH, json=body or {"aadhaarNumber": AADHAAR}, headers=headers)
    return first, seen["dup"]


def test_same_request_while_first_is_running_is_rejected(client, auth, abdm):
    first, dup = _send_duplicate_while_running(client, abdm, auth)

    assert first.status_code == 200
    assert dup.status_code == 409 and _code(dup) == "DUPLICATE_REQUEST"
    assert len(_otp_calls(abdm)) == 1


def test_same_request_after_first_finished_is_allowed(client, auth, abdm):
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})

    assert client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=auth).status_code == 200
    assert client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=auth).status_code == 200
    assert len(_otp_calls(abdm)) == 2


def test_lock_is_released_when_the_request_fails(client, auth, abdm):
    abdm.add(responses.POST, OTP_URL, status=429, json={"message": "You can request for new OTP after 30 seconds"})
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})

    assert client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=auth).status_code == 429
    assert client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=auth).status_code == 200


def test_different_clients_are_not_blocked_by_each_other(client, auth, other_auth, abdm):
    seen = {}

    def callback(request):
        if "other" not in seen:
            seen["other"] = client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=other_auth)
        return 200, {}, '{"txnId": "%s", "message": "OTP sent"}' % TXN

    abdm.add_callback(responses.POST, OTP_URL, callback=callback, content_type="application/json")
    assert client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=auth).status_code == 200
    assert seen["other"].status_code == 200


def test_idempotency_key_replays_the_stored_response(client, auth, abdm):
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})
    headers = {**auth, "Idempotency-Key": "otp-req-0001"}

    first = client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=headers)
    again = client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=headers)

    assert first.status_code == again.status_code == 200
    assert again.get_json() == first.get_json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert len(_otp_calls(abdm)) == 1


def test_idempotency_key_while_first_is_running_is_rejected(client, auth, abdm):
    first, dup = _send_duplicate_while_running(client, abdm, {**auth, "Idempotency-Key": "otp-req-0002"})

    assert first.status_code == 200
    assert dup.status_code == 409 and _code(dup) == "DUPLICATE_REQUEST"


def test_idempotency_key_reused_for_a_different_request(client, auth, abdm):
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})
    headers = {**auth, "Idempotency-Key": "otp-req-0003"}

    assert client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=headers).status_code == 200
    res = client.post(PATH, json={"aadhaarNumber": "999941057066"}, headers=headers)
    assert res.status_code == 422 and _code(res) == "IDEMPOTENCY_KEY_REUSED"


def test_idempotency_key_is_not_stored_for_failed_requests(client, auth, abdm):
    abdm.add(responses.POST, OTP_URL, status=429, json={"message": "You can request for new OTP after 30 seconds"})
    abdm.add(responses.POST, OTP_URL, json={"txnId": TXN, "message": "OTP sent"})
    headers = {**auth, "Idempotency-Key": "otp-req-0004"}

    assert client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=headers).status_code == 429
    retry = client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers=headers)
    assert retry.status_code == 200 and "Idempotent-Replayed" not in retry.headers


def test_invalid_idempotency_key(client, auth, abdm):
    res = client.post(PATH, json={"aadhaarNumber": AADHAAR}, headers={**auth, "Idempotency-Key": "bad key!"})
    assert res.status_code == 400 and _code(res) == "INVALID_IDEMPOTENCY_KEY"
