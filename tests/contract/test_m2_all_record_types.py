"""
All eight health-record types through the whole HIP pipeline, the way i-medicus runs it for one consent:

    FHIR conversion (/fhir/<type>)  ->  validation  ->  Fidelius encrypt (the same sender keys for every entry)
    ->  one push -> one page per record  ->  a HIU that decrypts every page and gets the Bundle back unchanged
    ->  one status notification for the transaction.

A consent that covers all eight care contexts is the worst case of one transfer.
"""
import hashlib
import json

import pytest
import responses

from src.app.abdm import fidelius
from tests.contract.test_fhir_routes import ROUTES, hip_auth  # noqa: F401  (fixture)
from tests.contract.test_m2_transfer import (CONSENT_ID, HI_NOTIFY, HIU_URL, TXN, Flow, consent_body, flow, hiu_dns)  # noqa: F401
from tests.fhir.samples import sample

TYPES = [hi_type for _slug, hi_type in ROUTES.values()]
SLUGS = {hi_type: slug for slug, hi_type in ROUTES.values()}


@pytest.fixture
def paged(app):
    app.config["HIP_PUSH_ENTRIES_PER_PAGE"] = 1


def build(client, auth, hi_type):
    res = client.post(f"/api/v1/fhir/{SLUGS[hi_type]}", json=sample(hi_type), headers=auth)
    assert res.status_code == 200, (hi_type, res.get_json())
    bundle = res.get_json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "document" and bundle["entry"]
    return bundle


@pytest.mark.parametrize("hi_type", TYPES)
def test_every_record_type_converts_and_validates(client, hip_auth, hi_type):
    bundle = build(client, hip_auth, hi_type)
    check = client.post("/api/v1/fhir/validate", json={"hiType": hi_type, "bundle": bundle}, headers=hip_auth).get_json()
    assert check["valid"] is True and check["issues"] == [], (hi_type, check)
    # the document the patient's app opens: a Composition first, the record's own resources after it
    assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"


@pytest.mark.parametrize("hi_type", TYPES)
def test_every_record_type_survives_encrypt_and_decrypt_byte_for_byte(client, hip_auth, flow, hi_type):
    plain = json.dumps(build(client, hip_auth, hi_type), ensure_ascii=False)
    sender, encrypted = flow.encrypt(plain)
    decrypted = fidelius.decrypt(encrypted["encryptedData"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                                 encrypted["keyToShare"], sender["nonce"])
    assert decrypted == plain


def test_a_consent_for_all_eight_types_is_delivered_one_page_per_record_and_every_page_reads(client, paged, flow):
    contexts = [f"CC-{hi_type}" for hi_type in TYPES]
    flow.consent(contexts=contexts)
    flow.hi_request()
    flow.ack()

    # i-medicus: one sender key pair, every record encrypted with it, one push with eight entries
    sender = client.get("/api/v1/fhir/keys", headers=flow.auth).get_json()
    plain, entries = {}, []
    for hi_type, context in zip(TYPES, contexts):
        plain[context] = json.dumps(build(client, flow.auth, hi_type), ensure_ascii=False)
        res = client.post("/api/v1/fhir/encrypt", headers=flow.auth, json={
            "receiverPublicKey": flow.key_block["dhPublicKey"]["keyValue"], "receiverNonce": flow.key_block["nonce"],
            "senderPrivateKey": sender["privateKey"], "senderPublicKey": sender["publicKey"],
            "senderNonce": sender["nonce"], "plainTextData": plain[context]})
        assert res.status_code == 200, (hi_type, res.get_json())
        encrypted = res.get_json()
        entries.append({"content": encrypted["encryptedData"], "checksum": "x", "media": "application/fhir+json",
                        "careContextReference": context})
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    body = flow.push_body(sender, encrypted, references=("CC-X",))
    body["entries"] = entries
    res = flow.push(body)
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent", res.get_json()

    pages = flow.received_by_hiu()
    assert [p["pageNumber"] for p in pages] == list(range(8)) and {p["pageCount"] for p in pages} == {8}
    assert {p["transactionId"] for p in pages} == {TXN}
    seen = {}
    for page in pages:
        (entry,) = page["entries"]                                        # i-medicus reads entries[0] of every push
        assert entry["checksum"] == hashlib.md5(entry["content"].encode()).hexdigest()
        text = fidelius.decrypt(entry["content"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                                page["keyMaterial"]["dhPublicKey"]["keyValue"], page["keyMaterial"]["nonce"])
        seen[entry["careContextReference"]] = text
    assert seen == plain                                                   # all eight records, each in its page

    assert flow.transfer().status == "PUSHED" and set(flow.transfer().care_contexts) == set(contexts)
    flow.http.add(responses.POST, HI_NOTIFY, json={}, status=202)
    # i-medicus reports the first care context only; the gateway reports all eight to ABDM
    assert flow.notify(responses_=[{"careContextReference": contexts[0], "hiStatus": "DELIVERED",
                                    "description": "Data Pushed"}]).status_code == 200
    (n,) = flow.abdm_notifications()
    assert n["statusNotification"]["sessionStatus"] == "TRANSFERRED"
    assert {r["careContextReference"] for r in n["statusNotification"]["statusResponses"]} == set(contexts)
