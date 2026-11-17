import json

from flask import Response, g
from flask_restx import Namespace

from src.app.api.v1 import examples
from src.app.common.validators import body, pick, require
from src.app.middleware.tenant_auth import CredentialsResource
from src.app.services import crypto_service, hip_transfer_service, hiu_consent_service
from src.app.services import fhir_service as svc

ns = Namespace("FHIR (M2)", path="/api/v1/fhir", description=(
    "Health records as ABDM FHIR document Bundles (NRCeS ABDM FHIR R4 IG 6.5.0). The request body is the JSON "
    "i-medicus builds for each record type (the one it used to send VCDoctor) and the answer is the Bundle itself at "
    "the top level (no `data` envelope), which i-medicus then encrypts and pushes. Auth: `Client-Id` + "
    "`Client-Secret` headers or the access token; the Organization in the Bundle is the client's HIP. A payload that "
    "cannot become a valid record is HTTP 422 `INVALID_RECORD`."))

_BODY = ("Body: `patient`, `practitioner`, `organization`, `encounter` and the record's own lists "
         "(see docs/imedicus-migration.md). Answer: a FHIR `Bundle` (type `document`).")


def _bundle(hi_type):
    return svc.convert(hi_type, body(), g.client)


@ns.route("/prescription")
class Prescription(CredentialsResource):
    @ns.doc(description="PrescriptionRecord. " + _BODY + " Keys: `medications[]`, `binaryDocument`.")
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("Prescription")


@ns.route("/legacy-prescription")
class LegacyPrescription(CredentialsResource):
    @ns.doc(description="PrescriptionRecord from the body of i-medicus' video-call flow (VCDoctor's "
                        "`ConvertPrescription`): `prescriptionId`, `date`, `patient{id, name}`, `doctor{id, name}`, "
                        "`medicines[{name, dosage, frequency, duration, instructions}]`. That body has no ABHA address "
                        "or licence number, so the patient's and doctor's id are the identifier values (an optional "
                        "`healthId` / `licenseNumber` wins); the clinic is the client. Answer: the Bundle.")
    @examples.body(ns, "FhirLegacyPrescription")
    def post(self):
        return svc.convert_legacy_prescription(body(), g.client)


@ns.route("/op-consultation")
class OpConsultation(CredentialsResource):
    @ns.doc(description="OPConsultRecord. " + _BODY)
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("OPConsultation")


@ns.route("/diagnostic-report")
class DiagnosticReport(CredentialsResource):
    @ns.doc(description="DiagnosticReportRecord (lab). " + _BODY + " Keys: `diagnosticReports[]`, `documentReference`.")
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("DiagnosticReport")


@ns.route("/discharge-summary")
class DischargeSummary(CredentialsResource):
    @ns.doc(description="DischargeSummaryRecord. " + _BODY)
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("DischargeSummary")


@ns.route("/immunization")
class Immunization(CredentialsResource):
    @ns.doc(description="ImmunizationRecord. " + _BODY + " Keys: `immunizations[]`, `documentReference`.")
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("ImmunizationRecord")


@ns.route("/health-document")
class HealthDocument(CredentialsResource):
    @ns.doc(description="HealthDocumentRecord. " + _BODY + " Key: `documentReference[]`.")
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("HealthDocumentRecord")


@ns.route("/wellness")
class Wellness(CredentialsResource):
    @ns.doc(description="WellnessRecord. " + _BODY)
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("WellnessRecord")


@ns.route("/invoice")
class Invoice(CredentialsResource):
    @ns.doc(description="InvoiceRecord. " + _BODY + " Key: `lineItems[]`.")
    @examples.body(ns, "FhirRecord")
    def post(self):
        return _bundle("Invoice")


@ns.route("/validate")
class Validate(CredentialsResource):
    @ns.doc(description="Check a Bundle against the gateway's ABDM rules. Body: `hiType` (e.g. Prescription) and "
                        "`bundle`. Answer: `{valid, issues[], igVersion}`.")
    @examples.body(ns, "FhirValidate")
    def post(self):
        data = body()
        return svc.check(require(pick(data, "hiType"), "hiType"), data.get("bundle"))


# ---- keys, encryption, decryption (what i-medicus used VCDoctor's Fidelius endpoints for) --------------------------

@ns.route("/keys")
class Keys(CredentialsResource):
    @ns.doc(description="A fresh key pair and nonce for one transfer (as HIP: our sender keys; as HIU: the keys the "
                        "HIP encrypts for). Answer at the top level: `{privateKey, publicKey (raw point, 88 characters), nonce}`. The "
                        "private key is not kept by the gateway.")
    def get(self):
        return crypto_service.new_keys()


@ns.route("/encrypt")
class Encrypt(CredentialsResource):
    @ns.doc(description="Encrypt a record for the HIU (Fidelius, ECDH Curve25519 + AES-GCM). Body: "
                        "`receiverPublicKey`, `receiverNonce` (from the HIU's request), `senderPrivateKey`, "
                        "`senderPublicKey`, `senderNonce` (from `/keys`), `plainTextData` (the Bundle as a JSON "
                        "string). Answer at the top level: `{encryptedData, keyToShare}`; `keyToShare` goes into "
                        "`keyMaterial.dhPublicKey.keyValue` of the push.")
    @examples.body(ns, "FhirEncrypt")
    def post(self):
        data, transfer = hip_transfer_service.fit_encrypt(g.client, body())
        encrypted = crypto_service.encrypt(data)
        if transfer is not None:
            hip_transfer_service.note_sender_key(transfer, encrypted)
        else:
            hip_transfer_service.remember_sender_key(g.client, data, encrypted)
        return encrypted


@ns.route("/decrypt")
class Decrypt(CredentialsResource):
    @ns.doc(description="Decrypt what a HIP pushed to us. Body: `receiverPrivateKey`, `receiverNonce`, `senderPublicKey`, "
                        "`senderNonce` (the pushed `keyMaterial`), `encryptedData`. Answer: a JSON **string** that holds "
                        "`{\"decryptedData\": \"<the plain text>\"}` (as VCDoctor answered: i-medicus decodes it twice and "
                        "its viewer reads `decryptedData`). Optional `transactionId` or `consentId`: when the consent behind it "
                        "has ended (revoked, denied, expired) the call is 403 `CONSENT_INVALID`.")
    @examples.body(ns, "FhirDecrypt")
    def post(self):
        # VCDoctor answered a JSON string holding Fidelius' {"decryptedData": "<plain text>"}: i-medicus decodes it twice and keeps
        # the object in consent_health_data.fhir_json, and its viewer (AbhaController::show, abdmJson.js) reads `decryptedData`
        data = body()
        # optional `transactionId` / `consentId` (i-medicus does not send them yet): records of an ended consent stay shut
        hiu_consent_service.assert_may_view(g.client.id, pick(data, "transactionId"), pick(data, "consentId"))
        plain = crypto_service.decrypt(data)
        return Response(json.dumps(json.dumps({"decryptedData": plain})), mimetype="application/json")
