"""
The video-call (legacy v1) flow of i-medicus after its calls moved to the gateway (ABDMService::sendPrescription /
sendWellnessRecord): the bodies it sends differ from the appointment flow in small ways (no `media`, a placeholder
checksum, a top-level `expiry`, a notify with its own clinic id). The gateway must accept them and the HIU must still
be able to read what arrives.
"""
import hashlib
import json

import responses

from src.app.abdm import fidelius
from tests.contract.test_m2_transfer import (  # noqa: F401  (fixtures and helpers of the appointment flow)
    BUNDLE, CONSENT_ID, HI_NOTIFY, HIU_URL, TXN, flow, hiu_dns)
from tests.conftest import HIP_ID
from tests.contract.test_fhir_routes import hip_auth  # noqa: F401
from tests.fhir.samples import HIP_ID as FACILITY


def legacy_push_body(flow, sender, encrypted, reference="CC-1"):
    """ABDMService::sendPrescription's push body after the move: one entry, placeholder checksum, no media."""
    return {"transactionId": TXN,
            "entries": [{"content": encrypted["encryptedData"], "checksum": "string", "careContextReference": reference}],
            "expiry": flow.key_block["dhPublicKey"]["expiry"],
            "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "dhPublicKey": {
                "expiry": flow.key_block["dhPublicKey"]["expiry"], "parameters": "Curve25519/32byte random key",
                "keyValue": encrypted["keyToShare"]}, "nonce": sender["nonce"]},
            "pushUrl": flow.push_url}


def legacy_notify_body(reference="CC-1"):
    """What the legacy flow reports: its clinic id comes from the env, which is not always the facility."""
    return {"notification": {"consentId": CONSENT_ID, "transactionId": TXN, "doneAt": "2026-11-12T10:00:00.000Z",
                             "notifier": {"type": "HIP", "id": "IN9999999999"},
                             "statusNotification": {"sessionStatus": "TRANSFERRED", "hipId": "IN9999999999",
                                                    "statusResponses": [{"careContextReference": reference,
                                                                         "hiStatus": "DELIVERED",
                                                                         "description": "Data Pushed"}]}}}


def test_the_video_call_push_and_notify_bodies_work_and_the_hiu_reads_the_record(flow, client, auth):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)

    res = flow.push(legacy_push_body(flow, sender, encrypted))
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent"

    # the placeholder checksum is replaced by the real one and `media` is filled in
    (call,) = flow.calls(HIU_URL)
    got = json.loads(call.request.body)
    entry = got["entries"][0]
    assert entry["media"] == "application/fhir+json"
    assert entry["checksum"] == hashlib.md5(entry["content"].encode()).hexdigest()
    plain = fidelius.decrypt(entry["content"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                             got["keyMaterial"]["dhPublicKey"]["keyValue"], got["keyMaterial"]["nonce"])
    assert json.loads(plain) == json.loads(BUNDLE)

    res = client.post("/api/v1/hip/health-information/notify", headers=auth, json=legacy_notify_body())
    assert res.status_code == 200 and res.get_json()["data"]["status"] == "sent"
    (n,) = flow.abdm_notifications()
    assert n["notifier"] == {"type": "HIP", "id": HIP_ID}                     # the facility, never the body's id
    assert n["statusNotification"]["hipId"] == HIP_ID
    assert flow.transfer().status == "NOTIFIED"


def test_the_old_data_push_url_field_name_is_still_accepted(flow):
    """The VCDoctor-era body called the HIU's address `dataPushUrl`; it must match ABDM's URL just the same."""
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    body = legacy_push_body(flow, sender, encrypted)
    body["dataPushUrl"] = body.pop("pushUrl")
    assert flow.push(body).status_code == 200


def old_body_with_receiver_key_material(flow, encrypted):
    """The video-call flow as it is today: the push carries the *receiver's* nonce, and in the wellness variant the
    receiver's own key as the sender key and the key to share as the checksum."""
    return {"transactionId": TXN,
            "entries": [{"content": encrypted["encryptedData"], "checksum": encrypted["keyToShare"],
                         "careContextReference": "CC-1"}],
            "expiry": flow.key_block["dhPublicKey"]["expiry"],
            "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "dhPublicKey": {
                "expiry": flow.key_block["dhPublicKey"]["expiry"], "parameters": "Ephemeral public key",
                "keyValue": flow.key_block["dhPublicKey"]["keyValue"]}, "nonce": flow.key_block["nonce"]},
            "dataPushUrl": flow.push_url}


def test_a_push_with_the_receivers_key_material_is_corrected_so_the_hiu_can_decrypt(flow):
    """i-medicus' video-call flow cannot be changed: the gateway uses the sender key and nonce of the Encrypt call."""
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    assert flow.push(old_body_with_receiver_key_material(flow, encrypted)).status_code == 200
    got = json.loads(flow.calls(HIU_URL)[0].request.body)
    assert got["keyMaterial"]["nonce"] == sender["nonce"]
    assert got["keyMaterial"]["dhPublicKey"]["keyValue"] == encrypted["keyToShare"]
    plain = fidelius.decrypt(got["entries"][0]["content"], flow.hiu_keys["privateKey"], flow.hiu_keys["nonce"],
                             got["keyMaterial"]["dhPublicKey"]["keyValue"], got["keyMaterial"]["nonce"])
    assert json.loads(plain) == json.loads(BUNDLE)


def test_a_correct_push_is_left_as_it_is(flow):
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    assert flow.push(legacy_push_body(flow, sender, encrypted)).status_code == 200
    got = json.loads(flow.calls(HIU_URL)[0].request.body)
    assert got["keyMaterial"]["nonce"] == sender["nonce"]
    assert got["keyMaterial"]["dhPublicKey"]["keyValue"] == encrypted["keyToShare"]


def test_a_push_without_an_encrypt_call_for_this_key_is_sent_as_given(flow):
    """Nothing is remembered for a key that was never used with the Encrypt route: nothing is guessed."""
    flow.acknowledged()
    sender, encrypted = flow.encrypt()
    other = fidelius.generate_key_material()
    flow.http.add(responses.POST, HIU_URL, json={}, status=200)
    body = legacy_push_body(flow, sender, encrypted)
    flow.transfer().sender_nonce = None
    from src.app.extensions import db
    db.session.commit()
    body["keyMaterial"]["nonce"] = other.nonce_b64
    assert flow.push(body).status_code == 200
    assert json.loads(flow.calls(HIU_URL)[0].request.body)["keyMaterial"]["nonce"] == other.nonce_b64


LEGACY_PRESCRIPTION = {"prescriptionId": "45", "date": "2026-11-12T10:00:00.000Z",
                       "patient": {"id": "9", "name": "Harshwardhan Singh"}, "doctor": {"id": "8", "name": "Brij Doctor"},
                       "medicines": [{"name": "Paracetamol 500mg tablet", "dosage": "10", "frequency": "2",
                                      "duration": "5", "instructions": "after food"}]}


def test_the_video_call_prescription_body_becomes_a_bundle(client, hip_auth):
    res = client.post("/api/v1/fhir/legacy-prescription", headers=hip_auth, json=LEGACY_PRESCRIPTION)
    assert res.status_code == 200, res.get_json()
    bundle = res.get_json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "document"
    by_type = {}
    for entry in bundle["entry"]:
        by_type.setdefault(entry["resource"]["resourceType"], []).append(entry["resource"])
    assert by_type["Patient"][0]["identifier"][0]["value"] == "9"           # the id stands in for the ABHA address
    assert by_type["Practitioner"][0]["identifier"][0]["value"] == "8"
    assert by_type["Organization"][0]["identifier"][0]["value"] == FACILITY
    (request,) = by_type["MedicationRequest"]
    assert request["medicationCodeableConcept"]["text"] == "Paracetamol 500mg tablet"
    assert request["dosageInstruction"][0]["text"] == "Qty 10 Frequency 2 for 5 days after food"
    assert by_type["Encounter"][0]["identifier"][0]["value"] == "45"


def test_the_video_call_prescription_body_can_carry_the_real_ids_and_is_checked(client, hip_auth):
    body = {**LEGACY_PRESCRIPTION, "patient": {**LEGACY_PRESCRIPTION["patient"], "healthId": "ravi@sbx"},
            "doctor": {**LEGACY_PRESCRIPTION["doctor"], "licenseNumber": "MH-123"}}
    bundle = client.post("/api/v1/fhir/legacy-prescription", headers=hip_auth, json=body).get_json()
    values = {e["resource"]["resourceType"]: e["resource"]["identifier"][0]["value"]
              for e in bundle["entry"] if e["resource"]["resourceType"] in ("Patient", "Practitioner")}
    assert values == {"Patient": "ravi@sbx", "Practitioner": "MH-123"}
    for bad in ({**LEGACY_PRESCRIPTION, "medicines": []}, {**LEGACY_PRESCRIPTION, "patient": {"id": "9"}}):
        assert client.post("/api/v1/fhir/legacy-prescription", headers=hip_auth, json=bad).status_code == 422
