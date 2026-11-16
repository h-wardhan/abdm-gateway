"""
M2 Day 14: encrypt, push to the HIU, tell ABDM how the transfer went.

The HIP side as i-medicus drives it (keys -> FHIR -> encrypt -> push -> notify), with ABDM and the HIU mocked. The
mock HIU decrypts what it receives with its own key: that is what proves the key exchange works.
"""
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests
import responses

from src.app.abdm import fidelius
from src.app.extensions import db
from src.app.models import HipConsent, HipTransfer, OutboxJob
from src.app.models.base import utcnow
from src.app.outbox import engine
from src.app.services import crypto_service
from tests.conftest import GATEWAY, HIP_ID, make_jwt

HIECM = f"{GATEWAY}/api/hiecm"
SESSION = f"{HIECM}/gateway/v3/sessions"
HI_ACK = f"{HIECM}/data-flow/v3/health-information/hip/on-request"
HI_NOTIFY = f"{HIECM}/data-flow/v3/health-information/notify"
HIU_URL = "https://hiu.example.test/api/abdm-webhook"
TXN = "a1b2c3d4-0000-1111-2222-333344445555"
CONSENT_ID = "ed486d33-3efa-4b8e-9a5c-47359467c20d"
REQ_HI = "req-hi-0001"
BUNDLE = json.dumps({"resourceType": "Bundle", "type": "document", "entry": [{"note": "ünïcode ✓"}]})


@pytest.fixture(autouse=True)
def hiu_dns(fake_dns):
    fake_dns["hiu.example.test"] = "93.184.216.35"


def consent_body(contexts=("CC-1", "CC-2"), status="GRANTED"):
    return {"notification": {"status": status, "consentId": CONSENT_ID, "consentDetail": {
        "consentId": CONSENT_ID, "patient": {"id": "ravi@sbx"}, "hip": {"id": HIP_ID},
        "careContexts": [{"patientReference": "P-1", "careContextReference": c} for c in contexts],
        "hiTypes": ["Prescription"],
        "permission": {"accessMode": "VIEW", "dateRange": {"from": "2026-10-01T00:00:00.000Z",
                                                          "to": "2026-11-30T00:00:00.000Z"},
                       "dataEraseAt": "2126-11-30T00:00:00.000Z"}}}}


class Flow:
    """One transfer: ABDM's callbacks, the clinic's i-medicus and a HIU with a key of its own."""

    def __init__(self, client, http, auth, push_url=HIU_URL, key_expires_in=timedelta(hours=24)):
        self.client, self.http, self.auth = client, http, auth
        self.hiu_keys = crypto_service.new_keys()
        self.key_block = fidelius.key_material_block(
            SimpleNamespace(x509_public_key_b64=self.hiu_keys["publicKey"], nonce_b64=self.hiu_keys["nonce"]),
            expires_in=key_expires_in)
        http.add(responses.POST, SESSION, json={"accessToken": "gw-token", "expiresIn": 1200})
        http.add(responses.POST, HI_ACK, json={}, status=202)
        self.push_url = push_url

    def callback(self, path, body, request_id):
        res = self.client.post(f"/api/{path}", data=json.dumps(body), headers={
            "Authorization": f"Bearer {make_jwt()}", "REQUEST-ID": request_id, "X-HIP-ID": HIP_ID,
            "Content-Type": "application/json"})
        assert res.status_code == 202
        # the receiver queues each callback for i-medicus' webhook; this test is about the replies, not that delivery
        OutboxJob.query.filter_by(kind="forward_callback").delete()
        db.session.commit()

    def consent(self, **kwargs):
        self.callback("v3/consent/request/hip/notify", consent_body(**kwargs), "req-consent-1")

    def hi_request(self):
        self.callback("v3/hip/health-information/request", {
            "transactionId": TXN, "hiRequest": {
                "consent": {"id": CONSENT_ID}, "dateRange": {"from": "2026-10-10T00:00:00.000Z",
                                                            "to": "2026-11-01T00:00:00.000Z"},
                "dataPushUrl": self.push_url, "keyMaterial": self.key_block}}, REQ_HI)

    def ack(self):
        res = self.client.post("/api/v1/hip/health-information/on-request", headers=self.auth,
                               json={"transactionId": TXN, "requestId": REQ_HI})
        assert res.status_code == 200, res.get_json()

    def acknowledged(self, **kwargs):
        self.consent(**kwargs)
        self.hi_request()
        self.ack()
        return self

    def encrypt(self, plain=BUNDLE):
        """What i-medicus does: new sender keys, then the Encrypt call. Returns (sender keys, answer)."""
        sender = self.client.get("/api/v1/fhir/keys", headers=self.auth).get_json()
        res = self.client.post("/api/v1/fhir/encrypt", headers=self.auth, json={
            "receiverPublicKey": self.key_block["dhPublicKey"]["keyValue"], "receiverNonce": self.key_block["nonce"],
            "senderPrivateKey": sender["privateKey"], "senderPublicKey": sender["publicKey"],
            "senderNonce": sender["nonce"], "plainTextData": plain})
        assert res.status_code == 200, res.get_json()
        return sender, res.get_json()

    def push_body(self, sender, encrypted, references=("CC-1",), **extra):
        return {"transactionId": TXN,
                "entries": [{"content": encrypted["encryptedData"], "checksum": "AbC123", "media": "application/fhir+json",
                             "careContextReference": r} for r in references],
                "expiry": self.key_block["dhPublicKey"]["expiry"],
                "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "dhPublicKey": {
                    "expiry": self.key_block["dhPublicKey"]["expiry"], "parameters": "Curve25519/32byte random key",
                    "keyValue": encrypted["keyToShare"]}, "nonce": sender["nonce"]},
                "pushUrl": self.push_url, **extra}

    def push(self, body):
        return self.client.post("/api/v1/hip/health-information/push", headers=self.auth, json=body)

    def notify_body(self, session="TRANSFERRED", responses_=None, consent_id=CONSENT_ID):
        return {"notification": {"consentId": consent_id, "transactionId": TXN, "doneAt": "2026-11-04T10:00:00.000Z",
                                 "notifier": {"type": "HIP", "id": "whatever"},
                                 "statusNotification": {"sessionStatus": session, "hipId": "whatever",
                                                        "statusResponses": responses_ if responses_ is not None else [
                                                            {"careContextReference": "CC-1", "hiStatus": "DELIVERED",
                                                             "description": "Data Pushed"}]}}}

    def notify(self, **kwargs):
        return self.client.post("/api/v1/hip/health-information/notify", headers=self.auth,
                                json=self.notify_body(**kwargs))

    def transfer(self):
        db.session.expire_all()
        return HipTransfer.query.filter_by(transaction_id=TXN).one()

    def calls(self, url):
        return [c for c in self.http.calls if c.request.url == url]

    def abdm_notifications(self):
        return [json.loads(c.request.body)["notification"] for c in self.calls(HI_NOTIFY)]

    def received_by_hiu(self):
        return [json.loads(c.request.body) for c in self.calls(HIU_URL)]


@pytest.fixture
def flow(client, jwks, hip, auth):
    jwks.add(responses.POST, HI_NOTIFY, json={}, status=202)
    return Flow(client, jwks, auth)


def run_due():
    OutboxJob.query.update({"next_attempt_at": utcnow() - timedelta(seconds=1)})
    db.session.commit()
    return engine.process_due()


# ---- keys and encryption ------------------------------------------------------------------------------------------

def test_keys_are_fresh_each_time_and_in_the_shape_i_medicus_stores(client, auth):
    first = client.get("/api/v1/fhir/keys", headers=auth).get_json()
    second = client.get("/api/v1/fhir/keys", headers=auth).get_json()
    assert set(first) == {"privateKey", "publicKey", "nonce"} and first != second
    fidelius.parse_public_key(first["publicKey"])  # the parser the HIU uses accepts it
    assert len(first["publicKey"]) == 88, "raw uncompressed point: fits i-medicus' varchar(255) key columns"


def test_the_hiu_decrypts_what_the_hip_encrypted_with_the_key_to_share(flow):
    sender, encrypted = flow.encrypt()
    assert fidelius.parse_public_key(encrypted["keyToShare"]) == fidelius.parse_public_key(sender["publicKey"])  # the same key (X.509 in the push)
    plain = fidelius.decrypt(encrypted["encryptedData"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                             encrypted["keyToShare"], sender["nonce"])
    assert plain == BUNDLE


@pytest.mark.parametrize("field,value,message", [
    ("plainTextData", "", "plainTextData"), ("plainTextData", {"a": 1}, "plainTextData"),
    ("receiverPublicKey", "not-a-key", "Invalid key material"), ("senderNonce", "AAAA", "Invalid key material"),
    ("senderPrivateKey", "AAAA", "Invalid key material"),
])
def test_encrypt_refuses_bad_input_with_422_or_400(flow, client, auth, field, value, message):
    sender = client.get("/api/v1/fhir/keys", headers=auth).get_json()
    body = {"receiverPublicKey": flow.key_block["dhPublicKey"]["keyValue"], "receiverNonce": flow.key_block["nonce"],
            "senderPrivateKey": sender["privateKey"], "senderPublicKey": sender["publicKey"],
            "senderNonce": sender["nonce"], "plainTextData": "{}"}
    body[field] = value
    res = client.post("/api/v1/fhir/encrypt", headers=auth, json=body)
    assert res.status_code == 400 and message in json.dumps(res.get_json())


def test_encrypt_refuses_a_sender_public_key_that_does_not_belong_to_the_private_key(flow, client, auth):
    sender, other = client.get("/api/v1/fhir/keys", headers=auth).get_json(), client.get("/api/v1/fhir/keys",
                                                                                         headers=auth).get_json()
    res = client.post("/api/v1/fhir/encrypt", headers=auth, json={
        "receiverPublicKey": flow.key_block["dhPublicKey"]["keyValue"], "receiverNonce": flow.key_block["nonce"],
        "senderPrivateKey": sender["privateKey"], "senderPublicKey": other["publicKey"],
        "senderNonce": sender["nonce"], "plainTextData": "{}"})
    assert res.status_code == 400 and res.get_json()["message"]["error"]["details"]["field"] == "senderPublicKey"


def test_decrypt_answers_a_json_string_holding_decryptedData_like_vcdoctor(flow, client, auth):
    sender, encrypted = flow.encrypt()
    res = client.post("/api/v1/fhir/decrypt", headers=auth, json={
        "receiverPrivateKey": flow.hiu_keys["privateKey"], "receiverNonce": flow.hiu_keys["nonce"],
        "senderPublicKey": encrypted["keyToShare"], "senderNonce": sender["nonce"],
        "encryptedData": encrypted["encryptedData"]})
    assert res.status_code == 200 and res.mimetype == "application/json"
    outer = json.loads(res.get_data())            # what i-medicus' first json_decode gives
    assert isinstance(outer, str)
    inner = json.loads(outer)                     # ... and the second: {"decryptedData": "<Bundle text>"}
    assert set(inner) == {"decryptedData"} and json.loads(inner["decryptedData"])["resourceType"] == "Bundle"


def test_decrypt_with_the_wrong_key_is_a_clear_error(flow, client, auth):
    sender, encrypted = flow.encrypt()
    stranger = client.get("/api/v1/fhir/keys", headers=auth).get_json()
    res = client.post("/api/v1/fhir/decrypt", headers=auth, json={
        "receiverPrivateKey": stranger["privateKey"], "receiverNonce": flow.hiu_keys["nonce"],
        "senderPublicKey": encrypted["keyToShare"], "senderNonce": sender["nonce"],
        "encryptedData": encrypted["encryptedData"]})
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "DECRYPTION_FAILED"


@pytest.mark.parametrize("method,path", [("get", "/api/v1/fhir/keys"), ("post", "/api/v1/fhir/encrypt"),
                                         ("post", "/api/v1/fhir/decrypt"),
                                         ("post", "/api/v1/hip/health-information/push"),
                                         ("post", "/api/v1/hip/health-information/notify"),
                                         ("get", "/api/v1/hip/transfers/x")])
def test_every_transfer_route_needs_authentication(client, method, path):
    assert getattr(client, method)(path).status_code == 401


# ---- the transfer record -------------------------------------------------------------------------------------------

def test_acknowledging_a_request_records_the_transfer_with_abdms_push_url(flow):
    flow.acknowledged()
    row = flow.transfer()
    assert (row.status, row.consent_id, row.hip_id, row.data_push_url, row.request_id) == (
        "ACKNOWLEDGED", CONSENT_ID, HIP_ID, HIU_URL, REQ_HI)
    assert row.key_expiry is not None and row.attempts == 0


def test_a_repeated_acknowledgement_does_not_reset_a_transfer_under_way(flow, client, auth):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    assert flow.push(flow.push_body(sender, encrypted)).status_code == 200
    flow.callback("v3/hip/health-information/request", {"transactionId": TXN, "hiRequest": {
        "consent": {"id": CONSENT_ID}, "dataPushUrl": HIU_URL, "keyMaterial": flow.key_block}}, "req-hi-again")
    assert flow.transfer().status == "PUSHED"


# ---- the happy path ------------------------------------------------------------------------------------------------

def test_the_whole_transfer_encrypt_push_notify_and_the_hiu_reads_the_record(flow, client, auth):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)

    res = flow.push(flow.push_body(sender, encrypted, references=("CC-1", "CC-2")))
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent"

    # what the HIU got: ABDM's push body, no ABDM token, a real checksum, only the fields of the contract
    (call,) = flow.calls(HIU_URL)
    assert "Authorization" not in call.request.headers
    got = json.loads(call.request.body)
    assert set(got) == {"pageNumber", "pageCount", "transactionId", "entries", "keyMaterial"}
    assert (got["pageNumber"], got["pageCount"], got["transactionId"]) == (0, 1, TXN)
    entry = got["entries"][0]
    assert entry["media"] == "application/fhir+json" and entry["careContextReference"] == "CC-1"
    assert entry["checksum"] == hashlib.md5(entry["content"].encode()).hexdigest()
    assert got["keyMaterial"]["dhPublicKey"]["keyValue"] == encrypted["keyToShare"]
    assert got["keyMaterial"]["nonce"] == sender["nonce"] and got["keyMaterial"]["curve"] == "Curve25519"
    # ... and it can read it
    plain = fidelius.decrypt(entry["content"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                             got["keyMaterial"]["dhPublicKey"]["keyValue"], got["keyMaterial"]["nonce"])
    assert json.loads(plain) == json.loads(BUNDLE)
    assert flow.transfer().status == "PUSHED" and flow.transfer().care_contexts == ["CC-1", "CC-2"]

    # i-medicus reports TRANSFERRED for the first care context only: ABDM is told about both
    res = flow.notify()
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent"
    (n,) = flow.abdm_notifications()
    assert n["consentId"] == CONSENT_ID and n["transactionId"] == TXN and n["doneAt"].endswith("Z")
    assert n["notifier"] == {"type": "HIP", "id": HIP_ID}       # the facility, not what the client wrote
    assert n["statusNotification"]["sessionStatus"] == "TRANSFERRED" and n["statusNotification"]["hipId"] == HIP_ID
    assert n["statusNotification"]["statusResponses"] == [
        {"careContextReference": "CC-1", "hiStatus": "DELIVERED", "description": "Data Pushed"},
        {"careContextReference": "CC-2", "hiStatus": "DELIVERED", "description": "Data Pushed"}]
    request = flow.calls(HI_NOTIFY)[0].request
    assert request.headers["X-HIP-ID"] == HIP_ID and request.headers["Authorization"] == "Bearer gw-token"
    assert flow.transfer().status == "NOTIFIED" and flow.transfer().notified_at is not None

    state = client.get(f"/api/v1/hip/transfers/{TXN}", headers=auth).get_json()["data"]
    assert state["status"] == "NOTIFIED" and state["careContexts"] == ["CC-1", "CC-2"] and state["attempts"] == 1


def test_a_second_push_and_a_second_notify_are_not_sent_again(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    body = flow.push_body(sender, encrypted)
    assert flow.push(body).status_code == 200
    again = flow.push(body)
    assert again.status_code == 200 and again.get_json()["data"]["status"] == "already_sent"
    assert flow.notify().status_code == 200
    assert flow.notify().get_json()["data"]["status"] == "already_sent"
    assert len(flow.calls(HIU_URL)) == 1 and len(flow.calls(HI_NOTIFY)) == 1


# ---- what the gateway refuses --------------------------------------------------------------------------------------

def test_push_for_an_unknown_transaction_is_404_and_nothing_is_sent(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    body = flow.push_body(sender, encrypted)
    body["transactionId"] = "nope-nope-nope"
    res = flow.push(body)
    assert res.status_code == 404 and res.get_json()["message"]["error"]["code"] == "TRANSFER_NOT_FOUND"
    assert flow.calls(HIU_URL) == []


def test_another_clients_transfer_is_not_visible(flow, other_auth, client):
    flow.acknowledged()
    assert client.get(f"/api/v1/hip/transfers/{TXN}", headers=other_auth).status_code == 404
    res = client.post("/api/v1/hip/health-information/notify", headers=other_auth, json=flow.notify_body())
    assert res.status_code == 404


def test_the_push_url_must_be_the_one_abdm_gave(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    body = flow.push_body(sender, encrypted)
    body["pushUrl"] = "https://evil.example.test/steal"
    res = flow.push(body)
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "PUSH_URL_MISMATCH"
    assert flow.transfer().status == "ACKNOWLEDGED"


@pytest.mark.parametrize("url,host_ip", [
    ("http://hiu.example.test/push", "93.184.216.35"),          # not https
    ("https://internal.example.test/push", "10.0.0.5"),          # private address
    ("https://user:pw@hiu.example.test/push", "93.184.216.35"),  # credentials
    ("https://unknown.example.test/push", None),                  # does not resolve
])
def test_an_unsafe_push_url_is_never_called(client, jwks, hip, auth, url, host_ip):
    jwks.add(responses.POST, HI_NOTIFY, json={}, status=202)
    flow = Flow(client, jwks, auth, push_url=url).acknowledged()
    sender, encrypted = flow.encrypt()
    res = flow.push(flow.push_body(sender, encrypted))
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "UNSAFE_PUSH_URL"
    assert [c for c in flow.http.calls if "example.test/push" in c.request.url] == []


def test_a_care_context_the_consent_does_not_cover_is_refused(flow):
    flow.acknowledged(contexts=("CC-1",))
    sender, encrypted = flow.encrypt()
    res = flow.push(flow.push_body(sender, encrypted, references=("CC-1", "CC-SECRET")))
    assert res.status_code == 403 and res.get_json()["message"]["error"]["code"] == "CARE_CONTEXT_NOT_CONSENTED"
    assert flow.calls(HIU_URL) == []


@pytest.mark.parametrize("change,fragment", [
    (lambda b: b.update(entries=[]), "entries"),
    (lambda b: b["entries"][0].pop("content"), "content"),
    (lambda b: b["entries"][0].pop("careContextReference"), "careContextReference"),
    (lambda b: b.pop("keyMaterial"), "keyMaterial"),
    (lambda b: b["keyMaterial"]["dhPublicKey"].update(expiry="2020-01-01T00:00:00.000Z"), "expired"),
    (lambda b: b["keyMaterial"].update(nonce="short"), "keyMaterial"),
])
def test_a_malformed_push_is_400_and_nothing_is_sent(flow, change, fragment):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    body = flow.push_body(sender, encrypted)
    change(body)
    res = flow.push(body)
    assert res.status_code == 400 and fragment in json.dumps(res.get_json())
    assert flow.calls(HIU_URL) == [] and flow.transfer().status == "ACKNOWLEDGED"


def test_the_consent_is_checked_again_at_push_time_and_abdm_is_told_failed(flow, client):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.callback("v3/consent/request/hip/notify", consent_body(status="REVOKED"), "req-revoke")
    res = flow.push(flow.push_body(sender, encrypted, references=("CC-1", "CC-2")))
    assert res.status_code == 403 and res.get_json()["message"]["error"]["code"] == "CONSENT_INVALID"
    assert flow.calls(HIU_URL) == []
    assert flow.transfer().status == "FAILED"
    (n,) = flow.abdm_notifications()
    assert n["statusNotification"]["sessionStatus"] == "FAILED"
    assert [r["hiStatus"] for r in n["statusNotification"]["statusResponses"]] == ["ERRORED", "ERRORED"]
    # ... and the client is told it cannot be repeated
    assert flow.push(flow.push_body(sender, encrypted)).status_code == 409


def test_an_expired_consent_stops_the_push(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    consent = HipConsent.query.one()
    consent.erase_at = utcnow() - timedelta(minutes=1)
    db.session.commit()
    res = flow.push(flow.push_body(sender, encrypted))
    assert res.status_code == 403 and res.get_json()["message"]["error"]["details"]["reason"] == "EXPIRED"
    assert flow.transfer().status == "FAILED"


def test_notify_transferred_before_any_push_is_refused(flow):
    flow.acknowledged()
    res = flow.notify()
    assert res.status_code == 400 and res.get_json()["message"]["error"]["code"] == "NOT_PUSHED"
    assert flow.calls(HI_NOTIFY) == []


def test_notify_with_another_consent_id_is_sent_with_the_consent_of_the_transaction(flow):
    """i-medicus takes the consent id from its own rows, where a second consent notice overwrites it (sandbox, 16 Nov
    2026: notify was refused after the records had been pushed). ABDM is told the consent of the transaction."""
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    assert flow.push(flow.push_body(sender, encrypted)).status_code == 200
    res = flow.notify(consent_id="the-other-consent-of-the-same-care-context")
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent"
    (n,) = flow.abdm_notifications()
    assert n["consentId"] == CONSENT_ID and flow.transfer().status == "NOTIFIED"


@pytest.mark.parametrize("body,field", [
    ({"notification": {"transactionId": TXN}}, "sessionStatus"),
    ({"notification": {"transactionId": TXN, "statusNotification": {"sessionStatus": "DONE"}}}, "sessionStatus"),
    ({"notification": {"statusNotification": {"sessionStatus": "FAILED"}}}, "transactionId"),
])
def test_notify_needs_a_transaction_and_a_known_status(flow, client, auth, body, field):
    flow.acknowledged()
    res = client.post("/api/v1/hip/health-information/notify", headers=auth, json=body)
    assert res.status_code in (400, 404) and field in json.dumps(res.get_json())


def test_the_client_can_report_failure_itself(flow):
    flow.acknowledged()
    res = flow.notify(session="FAILED", responses_=[{"careContextReference": "CC-1", "hiStatus": "ERRORED",
                                                      "description": "Record could not be built"}])
    assert res.status_code == 200
    (n,) = flow.abdm_notifications()
    assert n["statusNotification"]["sessionStatus"] == "FAILED"
    assert n["statusNotification"]["statusResponses"][0] == {"careContextReference": "CC-1", "hiStatus": "ERRORED",
                                                              "description": "Record could not be built"}
    assert flow.transfer().status == "FAILED"


# ---- failures at the HIU and at ABDM -------------------------------------------------------------------------------

def test_a_hiu_that_says_no_is_502_and_abdm_is_told_the_transfer_failed(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={"error": "bad"}, status=400)
    res = flow.push(flow.push_body(sender, encrypted, references=("CC-1", "CC-2")))
    assert res.status_code == 502 and res.get_json()["message"]["error"]["code"] == "HIU_REJECTED_PUSH"
    assert flow.transfer().status == "FAILED" and "HTTP 400" in flow.transfer().last_error
    (n,) = flow.abdm_notifications()
    assert n["statusNotification"]["sessionStatus"] == "FAILED"
    assert {r["careContextReference"] for r in n["statusNotification"]["statusResponses"]} == {"CC-1", "CC-2"}
    assert OutboxJob.query.count() == 0  # a refusal is not retried


def test_a_hiu_that_is_down_is_queued_and_the_worker_delivers_it_later(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, body=requests.ConnectionError("hiu is down"))
    res = flow.push(flow.push_body(sender, encrypted))
    assert res.status_code == 202 and res.get_json()["data"]["status"] == "queued"
    assert flow.transfer().status == "PUSH_QUEUED" and OutboxJob.query.filter_by(kind="hiu_push").count() == 1

    # i-medicus fires its notify straight away: held until the records are at the HIU
    held = flow.notify()
    assert held.status_code == 202 and held.get_json()["data"]["status"] == "held"
    assert flow.calls(HI_NOTIFY) == []

    flow.http.replace(responses.POST, HIU_URL, json={}, status=200)
    assert run_due() == 1
    assert flow.transfer().status == "NOTIFIED"
    (n,) = flow.abdm_notifications()
    assert n["statusNotification"]["sessionStatus"] == "TRANSFERRED"
    assert n["statusNotification"]["statusResponses"][0]["careContextReference"] == "CC-1"
    got = flow.received_by_hiu()[-1]
    assert fidelius.decrypt(got["entries"][0]["content"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                            got["keyMaterial"]["dhPublicKey"]["keyValue"], got["keyMaterial"]["nonce"]) == BUNDLE


@pytest.mark.parametrize("status", [500, 503, 429])
def test_a_hiu_error_that_can_pass_is_retried_with_backoff(flow, status):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=status)
    assert flow.push(flow.push_body(sender, encrypted)).status_code == 202
    assert run_due() == 1                                   # still failing: back to pending, later
    job = OutboxJob.query.filter_by(kind="hiu_push").one()
    assert job.status == "pending" and job.attempts == 1 and f"HTTP {status}" in job.last_error
    assert flow.transfer().status == "PUSH_QUEUED" and flow.abdm_notifications() == []


def test_giving_up_after_the_last_try_tells_abdm_failed(app, flow):
    app.config["OUTBOX_MAX_ATTEMPTS"] = 2
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=503)
    assert flow.push(flow.push_body(sender, encrypted)).status_code == 202
    run_due()
    run_due()
    job = OutboxJob.query.filter_by(kind="hiu_push").one()
    assert job.status == "dead" and "gave up" in job.last_error
    assert flow.transfer().status == "FAILED"
    (n,) = flow.abdm_notifications()
    assert n["statusNotification"]["sessionStatus"] == "FAILED"


def test_a_consent_revoked_while_the_push_waits_cancels_it(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, status=503, json={})
    assert flow.push(flow.push_body(sender, encrypted)).status_code == 202
    flow.callback("v3/consent/request/hip/notify", consent_body(status="REVOKED"), "req-revoke")
    flow.http.replace(responses.POST, HIU_URL, json={}, status=200)
    run_due()
    assert flow.transfer().status == "FAILED" and len(flow.calls(HIU_URL)) == 1  # the retry never reached the HIU
    assert flow.abdm_notifications()[0]["statusNotification"]["sessionStatus"] == "FAILED"


def test_the_notification_is_queued_when_abdm_is_down(client, jwks, hip, auth):
    jwks.add(responses.POST, HI_NOTIFY, json={}, status=503)
    flow = Flow(client, jwks, auth).acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    flow.push(flow.push_body(sender, encrypted))
    res = flow.notify()
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "queued"
    assert flow.transfer().status == "NOTIFIED"
    jwks.replace(responses.POST, HI_NOTIFY, json={}, status=202)
    run_due()
    assert len([c for c in flow.calls(HI_NOTIFY)]) == 2 and OutboxJob.query.filter_by(kind="abdm_call").one().status == "done"


# ---- the push URL rules --------------------------------------------------------------------------------------------

def test_a_redirect_from_the_hiu_is_not_followed(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, status=302, headers={"Location": "https://elsewhere.example.test/x"})
    res = flow.push(flow.push_body(sender, encrypted))
    assert res.status_code == 502 and "HTTP 302" in flow.transfer().last_error
    assert [c for c in flow.http.calls if "elsewhere" in c.request.url] == []


def test_http_and_private_push_urls_are_allowed_only_when_configured_for_local_runs(app, client, jwks, hip, auth, fake_dns):
    app.config.update(PUSH_ALLOW_HTTP=True, PUSH_ALLOW_PRIVATE_URLS=True)
    fake_dns["localhost.example.test"] = "127.0.0.1"
    url = "http://localhost.example.test:8000/api/abdm-webhook"
    jwks.add(responses.POST, HI_NOTIFY, json={}, status=202)
    flow = Flow(client, jwks, auth, push_url=url).acknowledged()
    sender, encrypted = flow.encrypt()
    jwks.add(responses.POST, url, json={}, status=200)
    assert flow.push(flow.push_body(sender, encrypted)).status_code == 200


def test_a_failure_description_has_no_symbols_abdm_refuses(flow):
    """ABDM answers "Invalid description" to braces and quotes (the HIU's JSON error copied in): the text is cleaned."""
    flow.acknowledged()
    res = flow.notify(session="FAILED", responses_=[{
        "careContextReference": "CC-1", "hiStatus": "ERRORED",
        "description": 'HIU answered HTTP 400: {"code":"ABDM-9999","message":"Invalid Consent artefact id"}'}])
    assert res.status_code == 200
    (n,) = flow.abdm_notifications()
    text = n["statusNotification"]["statusResponses"][0]["description"]
    assert text == "HIU answered HTTP 400 code ABDM-9999 message Invalid Consent artefact id"
    assert not set(text) & set('{}":;[]')
