"""
DocumentBuilder: collects the resources of one ABDM document Bundle and assembles it.

Every record builder gets one: it already holds the Patient, Practitioner (author), Organization (custodian) and
Encounter made from the payload's common blocks, adds the record-specific resources with `add()`, and ends with
`finish()`, which puts the Composition first and wraps everything in a DocumentBundle.
"""
import uuid

from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.errors import InvalidRecord

_GENDERS = {"male", "female", "other", "unknown"}


class DocumentBuilder:
    def __init__(self, payload, hip_id, when=None):
        if not isinstance(payload, dict) or not payload:
            raise InvalidRecord("Empty record: nothing to convert")
        if payload.get("success") is False:
            raise InvalidRecord(payload.get("message") or "i-medicus could not build this record "
                                                         "(its payload is an error body)", details={"payload": "error"})
        self.payload = payload
        self.hip_id = u.text(hip_id)
        self.when = when or u.now()
        self.timestamp = self.when.isoformat(timespec="seconds")
        self._entries = []
        self.patient = self._patient(self._block("patient"))
        self.practitioner = self._practitioner(self._block("practitioner"))
        self.organization = self._organization(self._block("organization"))
        self.encounter = self._encounter(self._block("encounter"))

    # ---- generic ------------------------------------------------------------------------------------------------

    def _block(self, key):
        block = self.payload.get(key)
        if not isinstance(block, dict) or not block:
            raise InvalidRecord(f"{key} is missing from the record", details={"field": key})
        return block

    def add(self, resource_type, body, *profiles, display=None):
        """Add a resource; returns the Reference to use for it (`urn:uuid:...`)."""
        rid = str(uuid.uuid4())
        resource = {"resourceType": resource_type, "id": rid,
                    "meta": {"profile": list(profiles) or [c.profile(resource_type)]}}
        if resource_type != "Binary":
            resource["text"] = u.narrative(display or resource_type)
        resource.update(body)
        self._entries.append({"fullUrl": f"urn:uuid:{rid}", "resource": resource})
        ref = {"reference": f"urn:uuid:{rid}"}
        if display:
            ref["display"] = display
        return ref

    @staticmethod
    def typed(ref, resource_type):
        """A section-entry Reference: the profiles pin Reference.type for some entries, so always set it."""
        return {**ref, "type": resource_type}

    # ---- the four shared resources ------------------------------------------------------------------------------

    def _patient(self, p):
        name = u.text(p.get("name"))
        health_id = u.text(p.get("healthId"))
        if not name:
            raise InvalidRecord("patient.name is missing", details={"field": "patient.name"})
        if not health_id:
            raise InvalidRecord("patient.healthId (the ABHA address) is missing: the record cannot be tied to a "
                                "patient", details={"field": "patient.healthId"})
        gender = (u.text(p.get("gender")) or "unknown").lower()
        body = {
            "identifier": [{"type": u.concept(u.coding(c.V2_0203, "MR", "Medical record number")),
                            "system": c.PATIENT_SYSTEM, "value": health_id}],
            "name": [{"text": name}],
            "gender": gender if gender in _GENDERS else "unknown",
        }
        if u.text(p.get("phone")):
            body["telecom"] = [{"system": "phone", "value": u.text(p.get("phone")), "use": "mobile"}]
        if u.fhir_date(p.get("birthDate")):
            body["birthDate"] = u.fhir_date(p.get("birthDate"))
        return self.add("Patient", body, display=name)

    def _practitioner(self, p):
        name = u.text(p.get("name"))
        licence = u.text(p.get("licenseNumber"))
        if not name:
            raise InvalidRecord("practitioner.name is missing", details={"field": "practitioner.name"})
        if not licence:
            raise InvalidRecord("practitioner.licenseNumber is missing", details={"field": "practitioner.licenseNumber"})
        body = {"identifier": [{"type": u.concept(u.coding(c.V2_0203, "MD", "Medical License number")),
                                "system": c.PRACTITIONER_SYSTEM, "value": licence}],
                "name": [{"text": name}]}
        return self.add("Practitioner", body, display=name)

    def _organization(self, o):
        name = u.text(o.get("name"))
        # the facility id is the HIP the client is registered for; the payload's `facilityId` is only a fallback
        facility = self.hip_id or u.text(o.get("facilityId"))
        if not name:
            raise InvalidRecord("organization.name is missing", details={"field": "organization.name"})
        if not facility:
            raise InvalidRecord("organization.facilityId is missing and the client has no HIP id",
                                details={"field": "organization.facilityId"})
        body = {"identifier": [{"type": u.concept(u.coding(c.V2_0203, "PRN", "Provider number")),
                                "system": c.FACILITY_SYSTEM, "value": facility}],
                "name": name}
        return self.add("Organization", body, display=name)

    def _encounter(self, e):
        encounter_id = u.text(e.get("encounterId"))
        if not encounter_id:
            raise InvalidRecord("encounter.encounterId is missing", details={"field": "encounter.encounterId"})
        code = u.text(e.get("classCode")) or "AMB"
        display = u.text(e.get("classDisplay")) or {"AMB": "ambulatory", "IMP": "inpatient encounter"}.get(code, code)
        appointment = self.payload.get("appointment") if isinstance(self.payload.get("appointment"), dict) else {}
        period = {"start": u.fhir_datetime(appointment.get("start")) or self.timestamp}
        if u.fhir_datetime(appointment.get("end")):
            period["end"] = u.fhir_datetime(appointment.get("end"))
        body = {"identifier": [{"system": "https://ndhm.in", "value": encounter_id}],
                "status": "finished",
                "class": u.coding(c.V3_ACT_CODE, code, display),
                "subject": self.patient,
                "period": period}
        return self.add("Encounter", body, display="Encounter")

    # ---- documents ----------------------------------------------------------------------------------------------

    def document_reference(self, doc, what, default_title, type_code, type_display):
        content_type, data, title = u.attachment(doc, what, default_title)
        body = {"status": "current", "docStatus": "final",
                "type": u.concept(u.coding(c.SNOMED, type_code, type_display), text=type_display),
                "subject": self.patient,
                "author": [self.practitioner],
                "custodian": self.organization,
                "content": [{"attachment": {"contentType": content_type, "language": c.LANGUAGE, "data": data,
                                            "title": title, "creation": self.timestamp}}]}
        return self.add("DocumentReference", body, display=title)

    def binary(self, doc, what, default_title):
        content_type, data, _ = u.attachment(doc, what, default_title)
        return self.add("Binary", {"contentType": content_type, "data": data})

    # ---- assembly -----------------------------------------------------------------------------------------------

    def finish(self, profile_name, type_concept, title, sections):
        """Composition + Bundle. `sections` = [(title, code_coding_or_None, [refs])]; empty ones are dropped."""
        built = []
        for s_title, s_code, refs in sections:
            if not refs:
                continue
            section = {"title": s_title}
            if s_code:
                section["code"] = u.concept(s_code)
            section["entry"] = refs
            built.append(section)
        if not built:
            raise InvalidRecord("The record has no data to put in a document", details={"record": profile_name})

        comp_id = str(uuid.uuid4())
        composition = {
            "resourceType": "Composition", "id": comp_id,
            "meta": {"profile": [c.profile(profile_name)]},
            "language": c.LANGUAGE,
            "text": u.narrative(title),
            "identifier": {"system": c.COMPOSITION_ID_SYSTEM, "value": comp_id},
            "status": "final",
            "type": type_concept,
            "subject": self.patient,
            "encounter": self.encounter,
            "date": self.timestamp,
            "author": [self.practitioner],
            "title": title,
            "custodian": self.organization,
            "section": built,
        }
        entries = [{"fullUrl": f"urn:uuid:{comp_id}", "resource": composition}] + self._entries
        return {
            "resourceType": "Bundle",
            "id": str(uuid.uuid4()),
            "meta": {"versionId": "1", "lastUpdated": self.timestamp, "profile": [c.DOCUMENT_BUNDLE]},
            "identifier": {"system": c.BUNDLE_ID_SYSTEM, "value": str(uuid.uuid4())},
            "type": "document",
            "timestamp": self.timestamp,
            "entry": entries,
        }
