"""
Local end-to-end drill of M2 (WBS Day 14-15), three processes + a worker talking over real HTTP on 127.0.0.1, with a
throwaway database. Nothing real is contacted; your .env and database are never touched.

    python scripts/e2e_transfer_local.py           # run, print PASS / FAIL, exit code 0 when all pass
    python scripts/e2e_transfer_local.py --keep    # then keep everything running

Clinic A is the HIP, clinic B the HIU (the fake client app is B's webhook: it receives the push). The mock ABDM plays
the consent manager: it relays callbacks to the gateway. The story:

  1. B asks for consent, ABDM notifies B, B acknowledges and fetches the consent, B asks for the records with its key.
  2. ABDM relays the consent and the request to A. A acknowledges, builds the FHIR Bundle (the real route, a sample
     i-medicus payload), encrypts it for B's key, pushes it. B's webhook is down for its first push: the worker retries.
  3. A reports TRANSFERRED; ABDM (the mock) gets one notification for every care context.
  4. B decrypts the record with its own key and reads the same Bundle.
  5. Failure cases: a push URL ABDM did not give is refused; a revoked consent stops a push and ABDM is told FAILED.
     (A HIU that refuses, retries running out and ABDM being down are covered by tests/contract/test_m2_transfer.py.)
  6. i-medicus' own PHP (Guzzle) calls keys / encrypt / decrypt the way it does and reads the record back.
"""
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("e2e_local", os.path.join(ROOT, "scripts", "e2e_local.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

from tests.fhir.samples import sample  # noqa: E402  (sample i-medicus payloads)

HIP_ID = "IN0110005723_240"
HIU_ID = "IN0110005723_241"
TXN = "a1b2c3d4-0000-1111-2222-333344445555"
CONSENT_ID = "ed486d33-3efa-4b8e-9a5c-47359467c20d"
FAKE_HIU_FAILS_FIRST = 1


def iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TransferDrill(e2e.Drill):
    def __init__(self, keep):
        super().__init__(keep)
        self.env.update({"PUSH_ALLOW_HTTP": "true", "PUSH_ALLOW_PRIVATE_URLS": "true",
                         "FAKE_HIU_FAIL_FIRST": str(FAKE_HIU_FAILS_FIRST), "OUTBOX_MAX_ATTEMPTS": "4"})
        self.push_url = f"http://127.0.0.1:{self.ports['client']}/api/abdm-webhook"

    # ---- plumbing ---------------------------------------------------------------------------------------------

    def api(self, method, path, body=None, headers=None, expect=None):
        """Call the gateway. Returns (status, parsed body)."""
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.ports['gateway']}{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                raw, status = reply.read(), reply.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        return status, (json.loads(raw) if raw else None)

    def mock_calls(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.ports['mock']}/mock/m2-calls", timeout=5) as reply:
            return json.loads(reply.read())

    def pushed(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.ports['client']}/pushed", timeout=5) as reply:
            return json.loads(reply.read())

    def _relay(self, path, body, request_id, headers):
        payload = {"path": path, "body": body, "requestId": request_id, "headers": headers,
                   "gatewayUrl": f"http://127.0.0.1:{self.ports['gateway']}"}
        request = urllib.request.Request(f"http://127.0.0.1:{self.ports['mock']}/mock/callback", data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as reply:
            return json.loads(reply.read())

    def create_client(self, name, hip_id):
        out = self.run(*self.flask, "clients", "create", "--name", name)
        client_id = re.search(r"Client-Id\s+:\s+(\S+)", out).group(1)
        secret = re.search(r"Client-Secret\s+:\s+(\S+)", out).group(1)
        callback = f"http://127.0.0.1:{self.ports['client']}/api/abdm-webhook"
        out = self.run(*self.flask, "hips", "add", "--client", client_id, "--hip-id", hip_id, "--callback-url", callback)
        self.check(f"{name}: client and facility {hip_id} registered", "Webhook secret" in out, out[-300:])
        return {"Client-Id": client_id, "Client-Secret": secret}

    def transfers(self):
        return self.query("select status, attempts, notified_at is not null from hip_transfers where transaction_id = "
                          f"'{TXN}'")

    def notifications(self):
        return [c for c in self.mock_calls() if c["path"].endswith("/health-information/notify")]

    # ---- the drill --------------------------------------------------------------------------------------------

    def setup(self):
        print("Everything below runs on 127.0.0.1 with a throwaway database.  (temp folder: %s)" % self.tmp)
        self.step(1, "prepare", "database, two client apps (clinic A = HIP, clinic B = HIU), then the processes")
        out = self.run(*self.flask, "db", "upgrade")
        self.check("database migrated (incl. hip_transfers)", "Running upgrade" in out and "hip_transfers" in
                   "".join(r[0] for r in self.query("select name from sqlite_master where type='table'")), out[-300:])
        self.auth_a = self.create_client("clinic A", HIP_ID)
        self.auth_b = self.create_client("clinic B", HIU_ID)
        self.start("mock", [sys.executable, "scripts/mock_abdm.py"])
        self.start("client", [sys.executable, "scripts/fake_client.py"])
        self.start("gateway", [*self.flask, "run", "--port", str(self.ports["gateway"]), "--no-reload"])
        self.start("worker", [*self.flask, "worker", "run"])
        up = self.wait_until(lambda: self.get(f"http://127.0.0.1:{self.ports['gateway']}/health") == 200
                             and self.get(f"http://127.0.0.1:{self.ports['mock']}/") == 200
                             and self.get(f"http://127.0.0.1:{self.ports['client']}/") is not None, 40)
        self.check("all processes are up", up, self.log("gateway")[-400:])

    def scenario_hiu_asks(self):
        self.step(2, "clinic B (HIU) asks for the records", "consent -> notification -> fetch -> request with its key")
        status, body = self.api("POST", "/api/v1/hiu/consent/init", {
            "patientId": "ravi@sbx", "requesterName": "Dr. Bela Shah", "requesterType": "REGN", "requesterValue": "MH202",
            "fromDate": "2026-10-01T00:00:00.000Z", "toDate": "2026-11-01T00:00:00.000Z", "dataEraseAt": iso(400),
            "hiType": ["Prescription"], "purposeOfRequest": "CAREMGT", "hipId": HIU_ID,
            "requestId": "6f1c2a9e-1111-4222-8333-444455556666"}, self.auth_b)
        self.check("consent init accepted (202)", status == 202, str(body))
        init = [c for c in self.mock_calls() if c["path"].endswith("/consent/v3/request/init")]
        self.check("ABDM got it with X-HIU-ID and the REQUEST-ID i-medicus keeps",
                   len(init) == 1 and init[0]["hiuId"] == HIU_ID and init[0]["requestId"] == "6f1c2a9e-1111-4222-8333-444455556666",
                   str(init))
        reply = self._relay("/api/v3/hiu/consent/request/notify", {"notification": {
            "status": "GRANTED", "consentRequestId": "creq-1", "consentArtefacts": [{"id": CONSENT_ID}]}},
            "req-hiu-notify", {"X-HIU-ID": HIU_ID})
        self.check("ABDM's consent notification reaches the gateway (routed by X-HIU-ID)", reply["gatewayStatus"] == 202)
        status, body = self.api("POST", "/api/v1/hiu/consent/on-notify", {
            "acknowledgement": [{"status": "OK", "consentId": CONSENT_ID}], "response": {"requestId": "req-hiu-notify"},
            "hipId": HIU_ID}, self.auth_b)
        self.check("B acknowledges it", status == 200 and body["data"]["status"] == "sent", str(body))
        status, body = self.api("POST", "/api/v1/hiu/consent/fetch", {"consentId": CONSENT_ID, "hipId": HIU_ID}, self.auth_b)
        self.check("B fetches the consent", status == 200 and body["data"]["status"] == "sent", str(body))

        status, self.keys_b = self.api("GET", "/api/v1/fhir/keys", headers=self.auth_b)
        self.check("B gets its key pair from the gateway", status == 200 and set(self.keys_b) == {"privateKey", "publicKey", "nonce"})
        status, body = self.api("POST", "/api/v1/hiu/health-information/request", {
            "consentId": CONSENT_ID, "fromDate": "2026-10-10T00:00:00.000Z", "toDate": "2026-11-01T00:00:00.000Z",
            "dataPushUrl": self.push_url, "expiryDate": iso(400), "publicKey": self.keys_b["publicKey"],
            "nonce": self.keys_b["nonce"], "hipId": HIU_ID, "requestId": "6f1c2a9e-2222-4222-8333-444455556666"},
            self.auth_b)
        self.check("B's health-information request is accepted (202)", status == 202, str(body))
        calls = [c for c in self.mock_calls() if c["path"].endswith("/data-flow/v3/health-information/request")]
        self.check("ABDM got B's key material and push URL", len(calls) == 1 and calls[0]["body"]["hiRequest"][
            "dataPushUrl"] == self.push_url and calls[0]["body"]["hiRequest"]["keyMaterial"]["dhPublicKey"][
            "keyValue"] == self.keys_b["publicKey"], str(calls))
        self.hi_request = calls[0]["body"]["hiRequest"]

    def scenario_hip_sends(self):
        self.step(3, "ABDM relays to clinic A (HIP), which builds, encrypts and pushes the record",
                  "B's webhook is down for the first push: the worker retries it")
        self._relay("/api/v3/consent/request/hip/notify", {"notification": {
            "status": "GRANTED", "consentId": CONSENT_ID, "consentDetail": {
                "consentId": CONSENT_ID, "patient": {"id": "ravi@sbx"}, "hip": {"id": HIP_ID}, "hiu": {"id": HIU_ID},
                "careContexts": [{"patientReference": "P-1", "careContextReference": "CC-1"},
                                 {"patientReference": "P-1", "careContextReference": "CC-2"}],
                "hiTypes": ["Prescription"], "permission": {"accessMode": "VIEW", "dateRange": {
                    "from": "2026-10-01T00:00:00.000Z", "to": "2026-11-01T00:00:00.000Z"}, "dataEraseAt": iso(400)}}}},
            "req-consent", {"X-HIP-ID": HIP_ID})
        status, _ = self.api("POST", "/api/v1/hip/consent/on-notify", {"consentId": CONSENT_ID, "requestId": "req-consent",
                                                                        "hipId": HIP_ID}, self.auth_a)
        self.check("A acknowledges the consent", status == 201)
        self._relay("/api/v3/hip/health-information/request", {"transactionId": TXN, "hiRequest": self.hi_request},
                    "req-hi", {"X-HIP-ID": HIP_ID})
        status, body = self.api("POST", "/api/v1/hip/health-information/on-request",
                                {"transactionId": TXN, "requestId": "req-hi"}, self.auth_a)
        self.check("A acknowledges the request (consent checked)", status == 200, str(body))
        self.check("the transfer is recorded with the push URL ABDM gave", self.transfers() == [("ACKNOWLEDGED", 0, 0)],
                   str(self.transfers()))

        status, bundle = self.api("POST", "/api/v1/fhir/prescription", sample("Prescription"), self.auth_a)
        self.check("A's FHIR route returns a Bundle", status == 200 and bundle["resourceType"] == "Bundle", str(bundle)[:200])
        self.bundle = bundle
        status, sender = self.api("GET", "/api/v1/fhir/keys", headers=self.auth_a)
        status, enc = self.api("POST", "/api/v1/fhir/encrypt", {
            "receiverPublicKey": self.hi_request["keyMaterial"]["dhPublicKey"]["keyValue"],
            "receiverNonce": self.hi_request["keyMaterial"]["nonce"], "senderPrivateKey": sender["privateKey"],
            "senderPublicKey": sender["publicKey"], "senderNonce": sender["nonce"],
            "plainTextData": json.dumps(bundle, separators=(",", ":"))}, self.auth_a)
        self.check("the Bundle is encrypted for B's key", status == 200 and enc["encryptedData"] and enc["keyToShare"])
        push = {"transactionId": TXN, "pushUrl": self.push_url, "expiry": self.hi_request["keyMaterial"]["dhPublicKey"]["expiry"],
                "entries": [{"content": enc["encryptedData"], "checksum": "x", "media": "application/fhir+json",
                             "careContextReference": "CC-1"}],
                "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "dhPublicKey": {
                    "expiry": self.hi_request["keyMaterial"]["dhPublicKey"]["expiry"], "parameters": "Ephemeral public key",
                    "keyValue": enc["keyToShare"]}, "nonce": sender["nonce"]}}
        status, body = self.api("POST", "/api/v1/hip/health-information/push", push, self.auth_a)
        self.check("B's webhook is down: the push is queued (202)", status == 202 and body["data"]["status"] == "queued", str(body))
        status, body = self.api("POST", "/api/v1/hip/health-information/notify", {"notification": {
            "consentId": CONSENT_ID, "transactionId": TXN, "doneAt": iso(0), "notifier": {"type": "HIP", "id": HIP_ID},
            "statusNotification": {"sessionStatus": "TRANSFERRED", "hipId": HIP_ID, "statusResponses": [
                {"careContextReference": "CC-1", "hiStatus": "DELIVERED", "description": "Data Pushed"}]}}}, self.auth_a)
        self.check("i-medicus' TRANSFERRED arrives while the push is queued: held (202)",
                   status == 202 and body["data"]["status"] == "held", str(body))
        self.check("the worker retries and the push reaches B", self.wait_until(lambda: len(self.pushed()) == 1, 40),
                   self.log("worker")[-500:])
        self.check("then ABDM is told TRANSFERRED, once", self.wait_until(lambda: len(self.notifications()) == 1, 20),
                   str(self.notifications()))
        note = self.notifications()[0]
        status_block = note["body"]["notification"]["statusNotification"]
        self.check("... as the facility, with its care context", note["hipId"] == HIP_ID and
                   status_block["sessionStatus"] == "TRANSFERRED" and
                   [r["careContextReference"] for r in status_block["statusResponses"]] == ["CC-1"], str(note))
        self.check("the transfer is NOTIFIED after 2 pushes", self.transfers() == [("NOTIFIED", 2, 1)], str(self.transfers()))
        status, state = self.api("GET", f"/api/v1/hip/transfers/{TXN}", headers=self.auth_a)
        self.check("GET /hip/transfers/<id> shows it", status == 200 and state["data"]["status"] == "NOTIFIED", str(state))

        self.step(4, "clinic B decrypts the record", "with its own private key, through the gateway's decrypt route")
        got = self.pushed()[0]
        status, text = self._decrypt(got)
        self.check("decrypt answers 200", status == 200)
        self.check("B reads exactly the Bundle A built (a JSON string holding {decryptedData: <Bundle text>}, as VCDoctor's was)",
                   json.loads(json.loads(json.loads(text))["decryptedData"]) == self.bundle)

    def _decrypt(self, got):
        data = json.dumps({"receiverPrivateKey": self.keys_b["privateKey"], "receiverNonce": self.keys_b["nonce"],
                           "senderPublicKey": got["keyMaterial"]["dhPublicKey"]["keyValue"],
                           "senderNonce": got["keyMaterial"]["nonce"], "encryptedData": got["entries"][0]["content"]}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.ports['gateway']}/api/v1/fhir/decrypt", data=data,
                                         headers={"Content-Type": "application/json", **self.auth_b})
        with urllib.request.urlopen(request, timeout=15) as reply:
            return reply.status, reply.read().decode()

    def scenario_failures(self):
        self.step(5, "failure cases", "a push URL ABDM did not give, a consent revoked before the push")
        status, sender = self.api("GET", "/api/v1/fhir/keys", headers=self.auth_a)
        # a second transfer for failure cases
        txn2 = "b2c3d4e5-0000-1111-2222-333344445555"
        self._relay("/api/v3/hip/health-information/request", {"transactionId": txn2, "hiRequest": {
            **self.hi_request, "dataPushUrl": self.push_url}}, "req-hi-2", {"X-HIP-ID": HIP_ID})
        status, body = self.api("POST", "/api/v1/hip/health-information/on-request",
                                {"transactionId": txn2, "requestId": "req-hi-2"}, self.auth_a)
        self.check("a second request is acknowledged", status == 200, str(body))
        status, enc = self.api("POST", "/api/v1/fhir/encrypt", {
            "receiverPublicKey": self.hi_request["keyMaterial"]["dhPublicKey"]["keyValue"],
            "receiverNonce": self.hi_request["keyMaterial"]["nonce"], "senderPrivateKey": sender["privateKey"],
            "senderPublicKey": sender["publicKey"], "senderNonce": sender["nonce"], "plainTextData": "{}"}, self.auth_a)
        push = {"transactionId": txn2, "entries": [{"content": enc["encryptedData"], "careContextReference": "CC-1"}],
                "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "dhPublicKey": {
                    "expiry": self.hi_request["keyMaterial"]["dhPublicKey"]["expiry"], "parameters": "x",
                    "keyValue": enc["keyToShare"]}, "nonce": sender["nonce"]}}
        status, body = self.api("POST", "/api/v1/hip/health-information/push",
                                {**push, "pushUrl": "http://evil.example.test/steal"}, self.auth_a)
        self.check("a push URL ABDM did not give is refused (400 PUSH_URL_MISMATCH)", status == 400 and
                   body["message"]["error"]["code"] == "PUSH_URL_MISMATCH", str(body))
        before = len(self.notifications())
        self._relay("/api/v3/consent/request/hip/notify", {"notification": {"status": "REVOKED", "consentId": CONSENT_ID}},
                    "req-revoke", {"X-HIP-ID": HIP_ID})
        status, body = self.api("POST", "/api/v1/hip/health-information/push", push, self.auth_a)
        self.check("after the consent is revoked the push is refused (403 CONSENT_INVALID)", status == 403 and
                   body["message"]["error"]["code"] == "CONSENT_INVALID", str(body))
        self.check("and ABDM is told FAILED", self.wait_until(lambda: len(self.notifications()) == before + 1, 20))
        failed = self.notifications()[-1]["body"]["notification"]["statusNotification"]
        self.check("... with ERRORED for the care context", failed["sessionStatus"] == "FAILED" and
                   failed["statusResponses"][0]["hiStatus"] == "ERRORED", str(failed))
        self.check("the transfer is FAILED", self.query(f"select status from hip_transfers where transaction_id = '{txn2}'")
                   == [("FAILED",)])

    def scenario_php(self):
        self.step(6, "i-medicus' own PHP (Guzzle) against the gateway", "the key / encrypt / decrypt calls as it makes them")
        vendor = r"D:\projects\i-medicus_web\src\vendor\autoload.php"
        php = self.run("where", "php").strip().splitlines()[0] if os.path.exists(vendor) else ""
        if not php or "Could not find" in php:
            print("   (skipped: no php or no i-medicus vendor folder)")
            return
        script = os.path.join(self.tmp, "smoke.php")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(f"""<?php
require '{vendor.replace(chr(92), '/')}';
$c = new GuzzleHttp\\Client(['base_uri' => 'http://127.0.0.1:{self.ports['gateway']}/']);
$h = ['Client-Id' => '{self.auth_a['Client-Id']}', 'Client-Secret' => '{self.auth_a['Client-Secret']}'];
$k = json_decode($c->get('api/v1/fhir/keys', ['headers' => $h])->getBody()->getContents(), true);
$r = json_decode($c->get('api/v1/fhir/keys', ['headers' => $h])->getBody()->getContents(), true);
$enc = json_decode($c->post('api/v1/fhir/encrypt', ['headers' => $h, 'json' => [
  'receiverPublicKey' => $r['publicKey'], 'receiverNonce' => $r['nonce'], 'senderPrivateKey' => $k['privateKey'],
  'senderPublicKey' => $k['publicKey'], 'senderNonce' => $k['nonce'], 'plainTextData' => '{{"resourceType":"Bundle"}}']])
  ->getBody()->getContents(), true);
$outer = json_decode($c->post('api/v1/fhir/decrypt', ['headers' => $h, 'json' => [
  'receiverPrivateKey' => $r['privateKey'], 'receiverNonce' => $r['nonce'], 'senderPublicKey' => $enc['keyToShare'],
  'senderNonce' => $k['nonce'], 'encryptedData' => $enc['encryptedData']]])->getBody()->getContents(), true);
echo json_decode(json_decode($outer, true)['decryptedData'], true)['resourceType'];
""")
        out = self.run(php, script).strip()
        self.check("PHP: keys -> encrypt -> decrypt (json_decode twice) gives the record back", out.endswith("Bundle"), out[-300:])


def main():
    parser = e2e.argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    drill = TransferDrill(parser.parse_args().keep)
    try:
        drill.setup()
        drill.scenario_hiu_asks()
        drill.scenario_hip_sends()
        drill.scenario_failures()
        drill.scenario_php()
        ok = drill.finish()
        if drill.keep and ok:
            drill.keep_running()
    finally:
        for _, proc, log in drill.procs:
            proc.terminate()
            log.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
