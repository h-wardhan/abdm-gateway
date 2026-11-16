"""python scripts/load_test_callbacks.py   (LOAD_TEST_DATABASE_URL=postgresql://... to run it on Postgres)

Load test of the callback receiver + threaded worker with real threads, a real HTTP webhook and a file database.
900 callbacks: 100 patients x 3 consents x (HIP notice, HIP health-information request, HIU notice)."""
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tempfile
DB = os.path.join(tempfile.mkdtemp(), "load_test.db")  # a file database; LOAD_TEST_DATABASE_URL uses Postgres instead
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(DB + suffix):
        os.remove(DB + suffix)
os.environ.update(DATABASE_URL=os.environ.get("LOAD_TEST_DATABASE_URL") or "sqlite:///" + DB.replace("\\", "/"), REDIS_URL="", CALLBACK_AUTH_MODE="off",
                  CALLBACK_ALLOW_HTTP="true", CALLBACK_ALLOW_PRIVATE_URLS="true", WORKER_CONCURRENCY="16",
                  OUTBOX_POLL_SECONDS="0", LOG_LEVEL="ERROR", LOG_LEVEL_LIBS="ERROR", FHIR_STRUCTURAL_CHECK="false")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PATIENTS, CONSENTS = int(os.environ.get("LOAD_PATIENTS", 100)), 3
NOTICE_SECONDS, OTHER_SECONDS = 0.4, 0.05          # i-medicus takes 17-45 s per notice; scaled down ~50x
lock = threading.Lock()
active_notices = defaultdict(int)
overlaps = []
delivered = Counter()
running = {"now": 0, "max": 0}


class Hook(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        is_notice = "/consent/request/hip/notify" in self.path
        patient = ((body.get("notification") or {}).get("consentDetail") or {}).get("patient", {}).get("id")
        key = self.headers.get("request-id")
        with lock:
            delivered[key] += 1
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
            if is_notice:
                active_notices[patient] += 1
                if active_notices[patient] > 1:
                    overlaps.append(patient)
        time.sleep(NOTICE_SECONDS if is_notice else OTHER_SECONDS)
        with lock:
            running["now"] -= 1
            if is_notice:
                active_notices[patient] -= 1
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"success"}')


server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

from src.app import create_app
from src.app.callbacks import registry
from src.app.extensions import db
from src.app.models import ApiClient, CallbackEvent, OutboxJob
from src.app.outbox import worker

app = create_app("development")
HIP = "IN0810041408"
with app.app_context():
    db.create_all()
    tenant, _secret = ApiClient.create(name="load", hip_id=HIP)
    db.session.add(tenant)
    db.session.commit()
    registry.upsert(tenant, HIP, f"http://127.0.0.1:{port}/api/abdm-webhook", "Load clinic")

client = app.test_client()
t0 = time.time()
sent = 0
for p in range(PATIENTS):
    for c in range(CONSENTS):
        consent = f"c-{p}-{c}"
        notice = {"notification": {"status": "GRANTED", "consentId": consent, "consentDetail": {
            "consentId": consent, "patient": {"id": f"patient{p}@sbx"}, "hip": {"id": HIP}, "careContexts": [
                {"patientReference": f"P{p}", "careContextReference": f"CC-{p}-{c}"}]}}}
        request = {"transactionId": f"t-{p}-{c}", "hiRequest": {"consent": {"id": consent}, "dataPushUrl": "https://x.test"}}
        hiu = {"notification": {"status": "GRANTED", "consentRequestId": f"cr-{p}-{c}"}}
        for path, body, rid in (("v3/consent/request/hip/notify", notice, f"n-{p}-{c}"),
                                ("v3/hip/health-information/request", request, f"r-{p}-{c}"),
                                ("v3/hiu/consent/request/notify", hiu, f"h-{p}-{c}")):
            res = client.post(f"/api/{path}", data=json.dumps(body), headers={
                "REQUEST-ID": rid, "X-HIP-ID": HIP, "Content-Type": "application/json"})
            assert res.status_code == 202, (path, res.status_code, res.data[:200])
            sent += 1
ingest = time.time() - t0
print(f"ingested {sent} callbacks in {ingest:.1f}s ({sent / ingest:.0f}/s)")

with app.app_context():
    total = OutboxJob.query.count()

    class Stop:
        t_end = None

        @property
        def stopped(self):
            db.session.expire_all()
            left = OutboxJob.query.filter(OutboxJob.status.in_(("pending", "processing"))).count()
            return left == 0

    import time as _t
    spent = {"dispatch": 0.0, "calls": 0, "lane": 0.0, "lane_calls": 0, "due": 0.0}
    real_dispatch, real_lane, real_due = worker.dispatch, worker.lane_of, worker.engine.due_ids
    def timed_dispatch(*a, **k):
        t = _t.time(); r = real_dispatch(*a, **k); spent["dispatch"] += _t.time() - t; spent["calls"] += 1; return r
    def timed_lane(*a, **k):
        t = _t.time(); r = real_lane(*a, **k); spent["lane"] += _t.time() - t; spent["lane_calls"] += 1; return r
    def timed_due(*a, **k):
        t = _t.time(); r = real_due(*a, **k); spent["due"] += _t.time() - t; return r
    worker.dispatch, worker.lane_of, worker.engine.due_ids = timed_dispatch, timed_lane, timed_due
    t1 = time.time()
    worker.run(stopper=Stop())
    print("dispatch: %.1fs in %d calls | lane_of: %.1fs in %d calls | due_ids: %.1fs" % (
        spent["dispatch"], spent["calls"], spent["lane"], spent["lane_calls"], spent["due"]))
    drain = time.time() - t1
    db.session.expire_all()
    statuses = Counter(j.status for j in OutboxJob.query.all())
    event_status = Counter(e.status for e in CallbackEvent.query.all())
    attempts = Counter(j.attempts for j in OutboxJob.query.all())

print(f"drained {total} jobs in {drain:.1f}s ({total / drain:.0f}/s)")
print("job statuses:", dict(statuses), "| event statuses:", dict(event_status), "| attempts per job:", dict(attempts))
print("max webhook calls at once:", running["max"], "| deliveries of the same callback more than once:",
      sum(1 for v in delivered.values() if v > 1), "| callbacks never delivered:", sent - len(delivered))
print("notices of ONE patient running together:", len(overlaps))
ok = (statuses == {"done": total} and not overlaps and sent == len(delivered)
      and all(v == 1 for v in delivered.values()) and running["max"] <= 16)
print("RESULT:", "PASS" if ok else "FAIL")
