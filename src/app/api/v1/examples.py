"""
Swagger request bodies for the M2/M3 routes.

The routes read the JSON body themselves (the bodies are i-medicus' own, pinned by the contract tests), so nothing here
validates: `@examples.body(ns, "Key")` only gives Swagger UI a body box with a sample to edit. Without it "Try it out"
shows no body at all and the POST routes cannot be tried from /docs.
"""
HIP_ID = "IN0810041408"
HIU_ID = "IN0810041412"
ABHA_ADDRESS = "singh_singh22.2202@sbx"
ABHA_NUMBER = "91408428781756"
REQUEST_ID = "5f3c9b1e-8a52-4b0e-9d34-1c2f7a6e0b11"
TXN_ID = "a1b2c3d4-0000-1111-2222-333344445555"
CONSENT_ID = "ed486d33-3efa-4b8e-9a5c-47359467c20d"
CARE_CONTEXT = "1_carecontext_f71a5ba5-5a0e-4ef2-9037-01f6c36fd0fc"
PATIENT_REF = "1_patient_0c8d3a52-6f1e-4d6b-8a77-2e9b1c4d5f60"

_PATIENT = [{
    "referenceNumber": PATIENT_REF, "display": "Harshwardhan Singh", "hiType": "Prescription", "count": 1,
    "careContexts": [{"referenceNumber": CARE_CONTEXT, "display": "Prescription 1"}],
}]

# the people and places every FHIR record carries; the record's own lists (see each route's description) go next to them
_FHIR_BASE = {
    "patient": {"name": "Harshwardhan Singh", "gender": "male", "birthDate": "2006-02-22", "phone": "9664063981",
                "healthId": ABHA_ADDRESS},
    "practitioner": {"name": "Test Doctor", "licenseNumber": "MH-LOCAL-12345"},
    "organization": {"name": "Cradle Hospital", "facilityId": HIP_ID},
    "encounter": {"encounterId": "1", "classCode": "AMB", "classDisplay": "Ambulatory"},
}

EXAMPLES = {
    # ---- HIP: linking
    "LinkToken": {"hipid": HIP_ID, "abhaNumber": ABHA_NUMBER, "abhaAddress": ABHA_ADDRESS,
                  "name": "Harshwardhan Singh", "gender": "M", "yearOfBirth": 2006},
    "LinkCareContext": {"hipid": HIP_ID, "singlecarecontext": {
        "abhaNumber": ABHA_NUMBER, "abhaAddress": ABHA_ADDRESS, "patient": _PATIENT}},
    "LinkNotify": {"hipid": HIP_ID, "abhaAddress": ABHA_ADDRESS, "hiType": ["Prescription"],
                   "patientReference": PATIENT_REF, "careContextReference": CARE_CONTEXT},
    "SmsNotify": {"hipId": HIP_ID, "phoneNumber": "9999999999"},
    "OnDiscover": {"transactionId": TXN_ID, "patient": _PATIENT, "matchedBy": ["MR"],
                   "response": {"requestId": REQUEST_ID}},
    "OnInit": {"transactionId": TXN_ID, "response": {"requestId": REQUEST_ID}, "link": {
        "referenceNumber": "0e2b7a50-3c1d-4c6e-9f10-7d5a1b2c3d4e", "authenticationType": "DIRECT",
        "meta": {"communicationMedium": "MOBILE", "communicationHint": "OTP",
                 "communicationExpiry": "2026-11-06T12:00:00.000Z"}}},
    "OnConfirm": {"response": {"requestId": REQUEST_ID}, "patient": [{
        "referenceNumber": PATIENT_REF, "display": "Harshwardhan Singh",
        "careContexts": [{"referenceNumber": CARE_CONTEXT, "display": "Prescription 1"}]}]},
    # ---- HIP: consent, request, transfer
    "HipConsentOnNotify": {"consentId": CONSENT_ID, "requestId": REQUEST_ID, "hipId": HIP_ID},
    "HiOnRequest": {"requestId": REQUEST_ID, "transactionId": TXN_ID, "hipId": HIP_ID},
    "HiPush": {"transactionId": TXN_ID, "entries": [{
        "content": "<encryptedData from /fhir/encrypt>", "media": "application/fhir+json",
        "checksum": "d41d8cd98f00b204e9800998ecf8427e", "careContextReference": CARE_CONTEXT}],
        "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "nonce": "<senderNonce from /fhir/keys>",
                        "dhPublicKey": {"expiry": "2026-11-07T00:00:00.000Z", "parameters": "Curve25519/32byte random key",
                                        "keyValue": "<keyToShare from /fhir/encrypt>"}}},
    "HiNotify": {"notification": {
        "consentId": CONSENT_ID, "transactionId": TXN_ID, "doneAt": "2026-11-06T10:00:00.000Z",
        "notifier": {"type": "HIP", "id": HIP_ID},
        "statusNotification": {"sessionStatus": "TRANSFERRED", "hipId": HIP_ID, "statusResponses": [{
            "careContextReference": CARE_CONTEXT, "hiStatus": "OK", "description": "Delivered"}]}}},
    # ---- HIU
    "HiuConsentInit": {"requestId": REQUEST_ID, "patientId": ABHA_ADDRESS, "requesterName": "Dr Brij",
                       "requesterType": "REGN", "requesterValue": "MH101", "fromDate": "2026-01-01T00:00:00.000Z",
                       "toDate": "2026-11-06T00:00:00.000Z", "dataEraseAt": "2026-12-31T00:00:00.000Z",
                       "hiType": ["Prescription"], "purposeOfRequest": "CAREMGT", "hipId": HIU_ID},
    "HiuConsentOnNotify": {"acknowledgement": [{"status": "OK", "consentId": CONSENT_ID}],
                           "response": {"requestId": REQUEST_ID}, "hipId": HIU_ID},
    "HiuConsentFetch": {"consentId": CONSENT_ID, "hipId": HIU_ID},
    "FhirLegacyPrescription": {
        "prescriptionId": "45", "date": "2026-11-12T10:00:00.000Z",
        "patient": {"id": "9", "name": "Harshwardhan Singh"}, "doctor": {"id": "8", "name": "Brij Doctor"},
        "medicines": [{"name": "Paracetamol 500mg tablet", "dosage": "10", "frequency": "2", "duration": "5",
                       "instructions": "after food"}]},
    "HiuConsentStatus": {"consentRequestId": "2e5f7c1a-8b3d-4c9e-a1f2-6d7e8f9a0b1c", "hipId": HIU_ID},
    "HiuHiRequest": {"consentId": CONSENT_ID, "fromDate": "2026-01-01T00:00:00.000Z", "toDate": "2026-11-06T00:00:00.000Z",
                     "expiryDate": "2026-11-07T00:00:00.000Z", "dataPushUrl": "https://<tunnel-B>/api/abdm-webhook",
                     "publicKey": "<publicKey from GET /fhir/keys>", "nonce": "<nonce from GET /fhir/keys>",
                     "hipId": HIU_ID, "requestId": REQUEST_ID},
    # ---- FHIR + crypto
    "FhirRecord": _FHIR_BASE,
    "FhirEncrypt": {"receiverPublicKey": "<HIU keyMaterial.dhPublicKey.keyValue>", "receiverNonce": "<HIU nonce>",
                    "senderPrivateKey": "<privateKey from GET /fhir/keys>", "senderPublicKey": "<publicKey from GET /fhir/keys>",
                    "senderNonce": "<nonce from GET /fhir/keys>", "plainTextData": "<the Bundle as a JSON string>"},
    "FhirDecrypt": {"receiverPrivateKey": "<our privateKey>", "receiverNonce": "<our nonce>",
                    "senderPublicKey": "<pushed keyMaterial.dhPublicKey.keyValue>", "senderNonce": "<pushed keyMaterial.nonce>",
                    "encryptedData": "<pushed entries[].content>"},
    "FhirValidate": {"hiType": "Prescription", "bundle": {"resourceType": "Bundle", "type": "document"}},
}


def body(ns, key):
    """`@examples.body(ns, "LinkToken")`: a body box prefilled with the sample JSON, no validation.

    A plain `in: body` parameter with a free-form object schema and an `example`: Swagger UI fills the box from the
    example as it is (a model of `fields.Raw` properties made the UI invent invalid JSON for them).
    """
    return ns.doc(params={"payload": {
        "in": "body", "required": True, "description": "JSON body (sample, edit it)",
        "schema": {"type": "object", "example": EXAMPLES[key]},
    }})
