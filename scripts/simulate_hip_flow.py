"""
python scripts/simulate_hip_flow.py [--legacy] [--patients 20] [--artefacts 4]

The HIP side of M2 with real threads and real HTTP, times scaled down 10x:

  a fake ABDM      sends the consent notices; sends the health-information request of a consent only AFTER it got the
                   gateway's acknowledgement of that notice, 1.4 s later (14 s in real life) plus 0 to 4 s of a slow HIU
  the gateway     the real receiver and worker (this repository), a file database
  a fake i-medicus the notice handler as WebhookTrait::onRequestHipNotify: sleep 0.5 s (5 s), save the consent id on the
                   patient's row (a later notice of the same patient overwrites it), acknowledge through the gateway, sleep 1.0 s
                   (10 s), then build the records with the transaction and keys the row holds at that moment;
                   the request handler as onHealthInformationRequest: find the row by consent id, acknowledge, save the keys

A conversion is right when the row still holds that consent and the keys of that consent's request. --legacy runs the
gateway as it was before the acknowledge-and-hold change (the acknowledgement comes from i-medicus), for comparison.
"""
import argparse
import json
import os
import random
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--legacy", action="store_true")
parser.add_argument("--patients", type=int, default=20)
parser.add_argument("--artefacts", type=int, default=4)
parser.add_argument("--slow", type=float, default=1.0, help="multiply every sleep: 1 = real time / 10, 10 = real time")
args = parser.parse_args()
random.seed(7)

SCALE = 0.1 * args.slow
HIP = "IN0810041408"
lock = threading.Lock()
T0 = time.time()
log = []


def note(*a):
    log.append((round(time.time() - T0, 2),) + a)


rows = {}                 # patient -> {"consent": id, "keys_for": id}  (i-medicus' hip_link_requests rows)
stats = {"conversions_ok": 0, "conversions_bad": [], "requests_lost": [], "requests": 0, "abdm_acks": 0,
         "abdm_acks_by_consent": {}}
done = threading.Event()
EXPECTED = args.patients * args.artefacts
gateway_port = {"port": None}
client_headers = {}


def post(url, body, headers=None):
    return requests.post(url, json=body, headers=headers or {}, timeout=60)


# ---- fake ABDM ----------------------------------------------------------------------------------------------------------
class Abdm(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        out = {}
        if self.path.endswith("/sessions"):
            out = {"accessToken": "t", "expiresIn": 1200}
        elif self.path.endswith("consent/v3/request/hip/on-notify"):
            consent = (body.get("acknowledgement") or {}).get("consentId")
            with lock:
                stats["abdm_acks"] += 1
                stats["abdm_acks_by_consent"][consent] = stats["abdm_acks_by_consent"].get(consent, 0) + 1
            delay = (1.4 + random.uniform(0, 4.0)) * SCALE   # request after the ack; the HIU may be slow
            threading.Timer(delay, send_request, args=(consent, self.headers.get("X-HIP-ID"))).start()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


def send_request(consent, hip):
    from src.app.abdm import fidelius
    from src.app.services import crypto_service
    from types import SimpleNamespace
    keys = crypto_service.new_keys()
    block = fidelius.key_material_block(SimpleNamespace(x509_public_key_b64=keys["publicKey"], nonce_b64=keys["nonce"]))
    body = {"transactionId": "txn-" + consent, "hiRequest": {
        "consent": {"id": consent}, "dateRange": {"from": "2026-10-10T00:00:00.000Z", "to": "2026-11-01T00:00:00.000Z"},
        "dataPushUrl": "https://hiu.example.test/push", "keyMaterial": block}}
    post(f"http://127.0.0.1:{gateway_port['port']}/api/v3/hip/health-information/request", body,
         {"X-HIP-ID": hip, "REQUEST-ID": "req-" + consent})


abdm = ThreadingHTTPServer(("127.0.0.1", 0), Abdm)
threading.Thread(target=abdm.serve_forever, daemon=True).start()


# ---- fake i-medicus -----------------------------------------------------------------------------------------------------
class Imedicus(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        request_id = self.headers.get("request-id")
        base = f"http://127.0.0.1:{gateway_port['port']}/api/v1/hip"
        if "/consent/request/hip/notify" in self.path:
            consent = body["notification"]["consentId"]
            patient = body["notification"]["consentDetail"]["patient"]["id"]
            if body["notification"]["status"] == "GRANTED":
                note('notice-in', consent)
                time.sleep(5 * SCALE)                             # sleep(5)
                with lock:
                    rows.setdefault(patient, {})["consent"] = consent   # saves the consent id on its row
                post(base + "/consent/on-notify", {"consentId": consent, "requestId": request_id, "hipId": HIP}, client_headers)
                time.sleep(10 * SCALE)                            # sleep(10)
                note('check', consent)
                with lock:                                        # ConvertRecordToFHIR: what the row holds now
                    row = rows.get(patient, {})
                    ok = row.get("consent") == consent and row.get("keys_for") == consent
                    if ok:
                        stats["conversions_ok"] += 1
                    else:
                        stats["conversions_bad"].append((patient, consent, dict(row)))
                    if stats["conversions_ok"] + len(stats["conversions_bad"]) >= EXPECTED:
                        done.set()
        elif "/hip/health-information/request" in self.path:
            consent = body["hiRequest"]["consent"]["id"]
            note('request-in', consent)
            with lock:
                stats["requests"] += 1
                match = [p for p, r in rows.items() if r.get("consent") == consent]
            if not match:                                         # findable only by the consent id saved on a row
                with lock:
                    stats["requests_lost"].append(consent)
            else:
                pass  # the real handler has no sleep before its acknowledgement
                note('ack-start', consent)
                post(base + "/health-information/on-request", {"transactionId": body["transactionId"], "requestId": request_id},
                     client_headers)
                note('keys-saved', consent)
                with lock:
                    rows[match[0]]["keys_for"] = consent
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"success"}')


imedicus = ThreadingHTTPServer(("127.0.0.1", 0), Imedicus)
threading.Thread(target=imedicus.serve_forever, daemon=True).start()

# ---- the gateway ---------------------------------------------------------------------------------------------------------
DB = os.path.join(tempfile.mkdtemp(), "sim.db")
os.environ.update(
    DATABASE_URL="sqlite:///" + DB.replace("\\", "/"), REDIS_URL="", CALLBACK_AUTH_MODE="off", CALLBACK_ALLOW_HTTP="true",
    CALLBACK_ALLOW_PRIVATE_URLS="true", WORKER_CONCURRENCY="16", OUTBOX_POLL_SECONDS="0", LOG_LEVEL="ERROR",
    LOG_LEVEL_LIBS="ERROR", FHIR_STRUCTURAL_CHECK="false", NHA_CLIENT_ID="sim", NHA_CLIENT_SECRET="sim",
    ABDM_GATEWAY_URL=f"http://127.0.0.1:{abdm.server_address[1]}", OUTBOX_HTTP_TIMEOUT="60",
    HIP_ACK_NOTICE_AT_ONCE="false" if args.legacy else "true", HIP_CONSENT_ACK_WAIT_SECONDS="0",
    HIP_NOTICE_HOLD_SECONDS="120")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


from werkzeug.serving import make_server

from src.app import create_app
from src.app.callbacks import registry
from src.app.extensions import db
from src.app.models import ApiClient, CallbackEvent, OutboxJob
from src.app.outbox import worker

app = create_app("development")
app.config["HIP_PAIR_DELAY_SECONDS"] = 8 * SCALE      # 8 s, scaled
with app.app_context():
    db.create_all()
    tenant, secret = ApiClient.create(name="sim", hip_id=HIP)
    db.session.add(tenant)
    db.session.commit()
    registry.upsert(tenant, HIP, f"http://127.0.0.1:{imedicus.server_address[1]}/api/abdm-webhook", "Sim clinic")
    client_headers.update({"Client-Id": tenant.client_id, "Client-Secret": secret})
server = make_server("127.0.0.1", 0, app, threaded=True)
gateway_port["port"] = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()


def notice(patient, consent):
    return {"notification": {"status": "GRANTED", "consentId": consent, "consentDetail": {
        "consentId": consent, "patient": {"id": patient}, "hip": {"id": HIP},
        "careContexts": [{"patientReference": "P", "careContextReference": f"CC-{patient}"}], "hiTypes": ["Prescription"],
        "permission": {"accessMode": "VIEW", "dateRange": {"from": "2026-10-01T00:00:00.000Z", "to": "2026-11-30T00:00:00.000Z"},
                       "dataEraseAt": "2126-11-30T00:00:00.000Z"}}}}


def send_notices():
    for a in range(args.artefacts):
        for p in range(args.patients):
            consent = f"c-{p}-{a}"
            post(f"http://127.0.0.1:{gateway_port['port']}/api/v3/consent/request/hip/notify", notice(f"p{p}@sbx", consent),
                 {"X-HIP-ID": HIP, "REQUEST-ID": "n-" + consent})


started = time.time()
threading.Thread(target=send_notices, daemon=True).start()


class Stop:
    @property
    def stopped(self):
        return done.is_set() or time.time() - started > 150 * args.slow


with app.app_context():
    worker.run(stopper=Stop())
elapsed = time.time() - started
ok, bad = stats["conversions_ok"], stats["conversions_bad"]
print(f"mode: {'LEGACY (i-medicus acknowledges)' if args.legacy else 'ACKNOWLEDGE-AND-HOLD'} | {args.patients} patients x "
      f"{args.artefacts} artefacts = {EXPECTED} notices | {elapsed:.1f}s (times scaled 10x: 1 s here = 10 s real)")
print(f"conversions with the right transaction and keys: {ok}/{EXPECTED} | wrong or missing keys: {len(bad)} | "
      f"requests i-medicus could not find a row for: {len(stats['requests_lost'])} of {stats['requests']}")
print(f"acknowledgements ABDM got: {stats['abdm_acks']} (expected {EXPECTED}, one per notice) | "
      f"notices acknowledged more than once: {sum(1 for v in stats['abdm_acks_by_consent'].values() if v > 1)}")
for b in bad[:3]: print("BAD", b, [e for e in log if e[2] == b[1]])
print("RESULT:", "PASS" if ok == EXPECTED and not stats["requests_lost"] and stats["abdm_acks"] == EXPECTED else "FAIL")
sys.exit(0 if ok == EXPECTED else 1)
