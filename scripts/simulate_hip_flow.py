"""
python scripts/simulate_hip_flow.py [--legacy] [--patients 10] [--artefacts 3] [--slow 3] [--r-delay 10]

The whole HIP side of M2 with real threads and real HTTP, the gateway being this repository's receiver, worker and routes:

  fake ABDM        sends consent notices; sends a consent's health-information request only AFTER it got the gateway's
                   acknowledgement of that notice (14 s later in real life, plus up to 12 s of a slow HIU)
  fake i-medicus   the notice handler of WebhookTrait::onRequestHipNotify: sleep 5 s, save the consent id on the patient's row
                   (a later notice of the same patient overwrites it), acknowledge, sleep 10 s, then ConvertRecordToFHIR with
                   what the row holds AT THAT MOMENT (transaction, keys: empty when its request handler has not saved them yet):
                   gateway encrypt -> push -> notify. The request handler: find the row by consent id, save the transaction,
                   spend --r-delay s (what it takes in staging: ~10 s) and only then save the sender keys
  fake HIU         receives every page of a push, decrypts it with the key of the transaction it was sent under, and counts
                   a consent as delivered when its page decrypts to that consent's record under that consent's transaction

All times are real seconds / 10 x --slow (--slow 10 = real time). PASS = every consent delivered exactly once and ABDM told
TRANSFERRED once for each. --legacy runs the gateway as before the acknowledge-and-hold / delivery-context change.
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
from types import SimpleNamespace

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--legacy", action="store_true")
parser.add_argument("--patients", type=int, default=10)
parser.add_argument("--artefacts", type=int, default=3)
parser.add_argument("--slow", type=float, default=3.0)
parser.add_argument("--r-delay", type=float, default=10.0, help="seconds (real) i-medicus' request handler takes before it saves keys")
args = parser.parse_args()
random.seed(7)

SCALE = 0.1 * args.slow
HIP = "IN0810041408"
lock = threading.Lock()
rows = {}                  # patient -> i-medicus' hip_link_requests row (+ keys)
hiu_keys = {}              # consent -> the HIU's keys of its request
delivered = {}             # consent -> number of pages that decrypted right
wrong = []                 # (consent, why)
abdm_notified = {}         # consent -> TRANSFERRED notifications ABDM got
stats = {"conv_done": 0, "abdm_acks": 0}
done = threading.Event()
EXPECTED = args.patients * args.artefacts
gateway = {"port": None}
client_headers = {}


def post(url, body, headers=None):
    return requests.post(url, json=body, headers=headers or {}, timeout=120)


def gw(path):
    return f"http://127.0.0.1:{gateway['port']}{path}"


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
            delay = (14 + random.uniform(0, 12)) * SCALE          # the request follows the acknowledgement
            threading.Timer(delay, send_request, args=(consent, self.headers.get("X-HIP-ID"))).start()
        elif self.path.endswith("health-information/notify"):
            n = body.get("notification", {})
            if (n.get("statusNotification") or {}).get("sessionStatus") == "TRANSFERRED":
                with lock:
                    consent = next((c for c, k in hiu_keys.items() if k["txn"] == n.get("transactionId")), None)
                    if consent != n.get("consentId"):
                        wrong.append((n.get("consentId"), f"notify under transaction of {consent}"))
                    abdm_notified[consent] = abdm_notified.get(consent, 0) + 1
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


def send_request(consent, hip):
    from src.app.abdm import fidelius
    from src.app.services import crypto_service
    keys = crypto_service.new_keys()
    block = fidelius.key_material_block(SimpleNamespace(x509_public_key_b64=keys["publicKey"], nonce_b64=keys["nonce"]))
    txn = "txn-" + consent
    with lock:
        hiu_keys[consent] = {"keys": keys, "txn": txn}
    body = {"transactionId": txn, "hiRequest": {
        "consent": {"id": consent}, "dateRange": {"from": "2026-10-10T00:00:00.000Z", "to": "2026-11-01T00:00:00.000Z"},
        "dataPushUrl": f"http://127.0.0.1:{hiu.server_address[1]}/push", "keyMaterial": block}}
    post(gw("/api/v3/hip/health-information/request"), body, {"X-HIP-ID": hip, "REQUEST-ID": "req-" + consent})


abdm = ThreadingHTTPServer(("127.0.0.1", 0), Abdm)
threading.Thread(target=abdm.serve_forever, daemon=True).start()


# ---- fake HIU: decrypts what is pushed ---------------------------------------------------------------------------------
class Hiu(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        from src.app.abdm import fidelius
        page = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        txn = page.get("transactionId")
        with lock:
            consent = next((c for c, k in hiu_keys.items() if k["txn"] == txn), None)
        if consent is None:
            wrong.append((txn, "push under an unknown transaction"))
        else:
            k = hiu_keys[consent]["keys"]
            entry = page["entries"][0]
            try:
                text = fidelius.decrypt(entry["content"], k["privateKey"], k["nonce"],
                                        page["keyMaterial"]["dhPublicKey"]["keyValue"], page["keyMaterial"]["nonce"])
            except Exception as exc:  # noqa: BLE001
                text = None
                wrong.append((consent, f"cannot decrypt: {type(exc).__name__}"))
            if text is not None:
                with lock:
                    if text == "record-" + consent and entry["careContextReference"] == "CC-" + consent:
                        delivered[consent] = delivered.get(consent, 0) + 1
                    else:
                        wrong.append((consent, f"page holds {text[:30]} / {entry['careContextReference']}"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")


hiu = ThreadingHTTPServer(("127.0.0.1", 0), Hiu)
threading.Thread(target=hiu.serve_forever, daemon=True).start()


# ---- fake i-medicus -----------------------------------------------------------------------------------------------------
def convert(patient, consent):
    """ConvertRecordToFHIR: what the row holds now, sent to the gateway's encrypt, push and notify."""
    with lock:
        row = dict(rows.get(patient, {}))
    keys = row.get("keys") or {}
    enc = post(gw("/api/v1/fhir/encrypt"), {
        "receiverPublicKey": keys.get("receiver_pk", ""), "receiverNonce": keys.get("receiver_nonce", ""),
        "senderPrivateKey": keys.get("sender_sk", ""), "senderPublicKey": keys.get("sender_pk", ""),
        "senderNonce": keys.get("sender_nonce", ""), "plainTextData": "record-" + consent}, client_headers)
    if enc.status_code != 200:
        wrong.append((consent, f"encrypt {enc.status_code}"))
        return
    encrypted = enc.json()
    push = post(gw("/api/v1/hip/health-information/push"), {
        "transactionId": row.get("txn", ""), "pushUrl": keys.get("push_url", ""),
        "entries": [{"content": encrypted["encryptedData"], "checksum": "x", "media": "application/fhir+json",
                     "careContextReference": "CC-" + consent}],
        "expiry": keys.get("expiry", ""),
        "keyMaterial": {"cryptoAlg": "ECDH", "curve": "Curve25519", "nonce": keys.get("sender_nonce"),
                        "dhPublicKey": {"expiry": keys.get("expiry", ""), "parameters": "Curve25519/32byte random key",
                                        "keyValue": encrypted["keyToShare"]}}}, client_headers)
    if push.status_code not in (200, 202):
        wrong.append((consent, f"push {push.status_code}"))
        return
    post(gw("/api/v1/hip/health-information/notify"), {"notification": {
        "consentId": row.get("consent", ""), "transactionId": row.get("txn", ""), "doneAt": "2026-11-17T00:00:00.000Z",
        "notifier": {"type": "HIP", "id": HIP},
        "statusNotification": {"sessionStatus": "TRANSFERRED", "hipId": HIP, "statusResponses": [
            {"careContextReference": "CC-" + consent, "hiStatus": "DELIVERED", "description": "Data Pushed"}]}}},
         client_headers)


class Imedicus(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        request_id = self.headers.get("request-id")
        base = gw("/api/v1/hip")
        if "/consent/request/hip/notify" in self.path:
            consent = body["notification"]["consentId"]
            patient = body["notification"]["consentDetail"]["patient"]["id"]
            if body["notification"]["status"] == "GRANTED":
                time.sleep(5 * SCALE)                                 # sleep(5)
                with lock:
                    rows.setdefault(patient, {})["consent"] = consent  # saves the consent id on its row
                post(base + "/consent/on-notify", {"consentId": consent, "requestId": request_id, "hipId": HIP}, client_headers)
                time.sleep(10 * SCALE)                                # sleep(10)
                convert(patient, consent)
                with lock:
                    stats["conv_done"] += 1
                    if stats["conv_done"] >= EXPECTED:
                        done.set()
        elif "/hip/health-information/request" in self.path:
            consent = body["hiRequest"]["consent"]["id"]
            with lock:
                match = [p for p, r in rows.items() if r.get("consent") == consent]
                if match:
                    rows[match[0]]["txn"] = body["transactionId"]     # saved before the acknowledgement
            if match:
                post(base + "/health-information/on-request", {"transactionId": body["transactionId"], "requestId": request_id},
                     client_headers)
                sender = requests.get(gw("/api/v1/fhir/keys"), headers=client_headers, timeout=60).json()
                time.sleep(args.r_delay * SCALE)                      # what the handler takes before it saves the keys
                km = body["hiRequest"]["keyMaterial"]
                with lock:
                    rows[match[0]]["keys"] = {
                        "receiver_pk": km["dhPublicKey"]["keyValue"], "receiver_nonce": km["nonce"],
                        "sender_sk": sender["privateKey"], "sender_pk": sender["publicKey"],
                        "sender_nonce": sender["nonce"], "expiry": km["dhPublicKey"]["expiry"],
                        "push_url": body["hiRequest"]["dataPushUrl"]}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"success"}')


imedicus = ThreadingHTTPServer(("127.0.0.1", 0), Imedicus)
threading.Thread(target=imedicus.serve_forever, daemon=True).start()

# ---- the gateway ---------------------------------------------------------------------------------------------------------
DB = os.path.join(tempfile.mkdtemp(), "sim.db")
flag = "false" if args.legacy else "true"
os.environ.update(
    DATABASE_URL="sqlite:///" + DB.replace("\\", "/"), REDIS_URL="", CALLBACK_AUTH_MODE="off", CALLBACK_ALLOW_HTTP="true",
    CALLBACK_ALLOW_PRIVATE_URLS="true", PUSH_ALLOW_HTTP="true", PUSH_ALLOW_PRIVATE_URLS="true", WORKER_CONCURRENCY="16",
    OUTBOX_POLL_SECONDS="0", LOG_LEVEL="ERROR", LOG_LEVEL_LIBS="ERROR", FHIR_STRUCTURAL_CHECK="false", NHA_CLIENT_ID="sim",
    NHA_CLIENT_SECRET="sim", ABDM_GATEWAY_URL=f"http://127.0.0.1:{abdm.server_address[1]}", OUTBOX_HTTP_TIMEOUT="120",
    HIP_ACK_NOTICE_AT_ONCE=flag, HIP_ONE_NOTICE_PER_CLIENT=flag, HIP_CONSENT_ACK_WAIT_SECONDS="0",
    HIP_NOTICE_HOLD_SECONDS="600", HIP_TRANSFER_WAIT_SECONDS=str(int(60 * args.slow)), HIP_PUSH_ENTRIES_PER_PAGE="1")
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
from src.app.models import ApiClient
from src.app.outbox import worker

app = create_app("development")
app.config["HIP_PAIR_DELAY_SECONDS"] = 8 * SCALE
with app.app_context():
    db.create_all()
    tenant, secret = ApiClient.create(name="sim", hip_id=HIP)
    db.session.add(tenant)
    db.session.commit()
    registry.upsert(tenant, HIP, f"http://127.0.0.1:{imedicus.server_address[1]}/api/abdm-webhook", "Sim clinic")
    client_headers.update({"Client-Id": tenant.client_id, "Client-Secret": secret})
server = make_server("127.0.0.1", 0, app, threaded=True)
gateway["port"] = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()


def notice(patient, consent):
    return {"notification": {"status": "GRANTED", "consentId": consent, "consentDetail": {
        "consentId": consent, "patient": {"id": patient}, "hip": {"id": HIP},
        "careContexts": [{"patientReference": "P", "careContextReference": "CC-" + consent}], "hiTypes": ["Prescription"],
        "permission": {"accessMode": "VIEW", "dateRange": {"from": "2026-10-01T00:00:00.000Z", "to": "2026-11-30T00:00:00.000Z"},
                       "dataEraseAt": "2126-11-30T00:00:00.000Z"}}}}


def send_notices():
    for a in range(args.artefacts):
        for p in range(args.patients):
            consent = f"c-{p}-{a}"
            post(gw("/api/v3/consent/request/hip/notify"), notice(f"p{p}@sbx", consent),
                 {"X-HIP-ID": HIP, "REQUEST-ID": "n-" + consent})


started = time.time()
threading.Thread(target=send_notices, daemon=True).start()


class Stop:
    @property
    def stopped(self):
        return (done.is_set() and time.time() - started > 1) or time.time() - started > 900


with app.app_context():
    worker.run(stopper=Stop())
time.sleep(2 * SCALE)
elapsed = time.time() - started
ok = sum(1 for c in delivered if delivered[c] == 1 and abdm_notified.get(c) == 1)
print(f"mode: {'LEGACY' if args.legacy else 'NEW (acknowledge-and-hold + delivery context)'} | {args.patients} patients x "
      f"{args.artefacts} consents = {EXPECTED} | {elapsed:.0f}s | i-medicus request handler {args.r_delay:g}s x{args.slow / 10:g}")
print(f"consents delivered right, exactly once, and reported to ABDM: {ok}/{EXPECTED} | pushes that decrypted right: "
      f"{sum(delivered.values())} | problems: {len(wrong)}")
for item in wrong[:6]:
    print("  problem:", item)
print("RESULT:", "PASS" if ok == EXPECTED and not wrong else "FAIL")
sys.exit(0 if ok == EXPECTED and not wrong else 1)
