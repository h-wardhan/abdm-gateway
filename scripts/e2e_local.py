"""
Local end-to-end test of the ABDM callback workflow. Nothing real is contacted: it starts its own mock ABDM, the
gateway, the retry worker and a fake client app (what i-medicus' /api/abdm-webhook does), all on 127.0.0.1, with a
throwaway database in a temp folder. Your .env, your database and the ABDM sandbox / production are never touched.

    python scripts/e2e_local.py           # run the scenarios, print PASS / FAIL, exit code 0 when all pass
    python scripts/e2e_local.py --keep    # then keep everything running so you can poke at it yourself

The story it plays (each step prints what it does and why):

  1. A client app (i-medicus) and one of its facilities (HIP) are registered in the gateway, with the URL ABDM
     callbacks for that facility must be delivered to.
  2. ABDM (the mock) tells the gateway "a patient scanned the QR of this facility". The gateway checks ABDM's JWT,
     stores the callback, answers 202 at once, and the worker delivers it to the client app. The fake client app
     is down for its first two tries, so you see the retries, then success.
  3. ABDM sends the same callback again (duplicate), someone sends a forged one (no JWT), and a callback comes for
     a facility nobody registered. Each is handled differently; the last one is then re-routed by hand.
"""
import argparse
import base64
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIP_ID = "IN0110005723"
FAKE_CLIENT_FAILS_FIRST = 2
SHARE_PATH = "/api/v3/hip/patient/share"
PATIENT_SHARE = {
    "requestId": "5f7a535d-a3fd-416b-b069-e6a0fc9b6f9d",
    "metaData": {"hipId": HIP_ID, "context": "hip"},
    "profile": {"patient": {"abhaNumber": "91-1234-5678-9012", "abhaAddress": "ravi@sbx", "name": "Ravi Kumar",
                            "gender": "M", "yearOfBirth": 1990}},
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Drill:
    def __init__(self, keep):
        self.keep = keep
        self.tmp = tempfile.mkdtemp(prefix="abdm-e2e-")
        self.db = os.path.join(self.tmp, "gateway.db")
        self.ports = {"mock": free_port(), "client": free_port(), "gateway": free_port()}
        self.procs = []
        self.results = []
        self.flask = [sys.executable, "-m", "flask", "--app", "wsgi"]
        self.env = {
            **os.environ,
            # Everything points at this machine. These override whatever is in .env.
            "DATABASE_URL": "sqlite:///" + self.db.replace("\\", "/"),
            "REDIS_URL": "",
            "LOG_DIR": os.path.join(self.tmp, "logs"),
            "LOG_LEVEL": "WARNING",
            "ABDM_ENV": "sbx",
            "ABDM_GATEWAY_URL": f"http://127.0.0.1:{self.ports['mock']}",
            "ABDM_ABHA_URL": f"http://127.0.0.1:{self.ports['mock']}",
            "ABDM_JWKS_URL": "",
            "NHA_CLIENT_ID": "mock-client",
            "NHA_CLIENT_SECRET": "mock-secret",
            "GATEWAY_PUBLIC_URL": "https://gateway.example.test",
            "SECRET_KEY": "e2e-secret",
            "JWT_SECRET_KEY": "e2e-jwt-secret",
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "CALLBACK_AUTH_MODE": "jwt",
            "CALLBACK_ALLOW_HTTP": "true",
            "CALLBACK_ALLOW_PRIVATE_URLS": "true",  # the fake client lives on 127.0.0.1
            "OUTBOX_BACKOFF_SECONDS": "1,1,1,1,1",  # retry quickly
            "OUTBOX_POLL_SECONDS": "1",
            "PYTHONUNBUFFERED": "1",
            "MOCK_ABDM_PORT": str(self.ports["mock"]),
            "FAKE_CLIENT_PORT": str(self.ports["client"]),
        }

    # ---- helpers --------------------------------------------------------------------------------------------

    def run(self, *args, extra_env=None):
        out = subprocess.run(list(args), cwd=ROOT, env={**self.env, **(extra_env or {})},
                             capture_output=True, text=True, timeout=120)
        return out.stdout + out.stderr

    def start(self, name, args, extra_env=None):
        log = open(os.path.join(self.tmp, f"{name}.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(args, cwd=ROOT, env={**self.env, **(extra_env or {})}, stdout=log, stderr=subprocess.STDOUT)
        self.procs.append((name, proc, log))

    def log(self, name):
        with open(os.path.join(self.tmp, f"{name}.log"), encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def get(self, url):
        """HTTP status of a GET. Any answer (even 404) means the server is up; a refused connection raises."""
        try:
            with urllib.request.urlopen(url, timeout=5) as reply:
                return reply.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def abdm_calls_gateway(self, body=PATIENT_SHARE, request_id=None, send_token=True, path=SHARE_PATH):
        """Ask the mock to play ABDM: it signs a JWT and POSTs to the gateway's callback URL."""
        payload = {"path": path, "body": body, "sendToken": send_token,
                   "gatewayUrl": f"http://127.0.0.1:{self.ports['gateway']}"}
        if request_id:
            payload["requestId"] = request_id
        request = urllib.request.Request(f"http://127.0.0.1:{self.ports['mock']}/mock/callback",
                                         data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as reply:
            return json.loads(reply.read())

    def query(self, sql):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def client_calls(self):
        return re.findall(r"^\[(\d+)\] urlKey=(\S+) request-id=(\S+) event=(\S+) signature=(\S+)", self.log("client"), re.M)

    def wait_until(self, condition, timeout=30, interval=0.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if condition():
                    return True
            except Exception:  # noqa: BLE001  (a service that is not up yet)
                pass
            time.sleep(interval)
        return False

    def step(self, number, title, why):
        print(f"\n== Step {number}: {title}\n   {why}")

    def check(self, description, ok, detail=""):
        self.results.append((description, bool(ok)))
        print(f"   [{'PASS' if ok else 'FAIL'}] {description}" + (f"  ({detail})" if detail and not ok else ""))
        return bool(ok)

    # ---- the drill --------------------------------------------------------------------------------------------

    def setup(self):
        print("Everything below runs on 127.0.0.1 with a throwaway database. The real ABDM sandbox, production and")
        print(f"your .env database are never contacted.  (temp folder: {self.tmp})")

        self.step(1, "prepare the gateway", "create the tables, register the client app and one of its facilities (HIP)")
        out = self.run(*self.flask, "db", "upgrade")
        self.check("database migrated", "a7c3d9e15b02" in out or "Running upgrade" in out, out[-300:])
        out = self.run(*self.flask, "clients", "create", "--name", "imedicus-local")
        match = re.search(r"Client-Id\s+:\s+(\S+)", out)
        self.check("client app created", match, out[-300:])
        client_id = match.group(1)
        self.client_id = client_id
        self.client_secret = re.search(r"Client-Secret\s+:\s+(\S+)", out).group(1)
        callback_url = f"http://127.0.0.1:{self.ports['client']}/api/abdm-webhook"
        out = self.run(*self.flask, "hips", "add", "--client", client_id, "--hip-id", HIP_ID, "--callback-url", callback_url)
        secret = re.search(r"Webhook secret\s+:\s+(\S+)", out)
        self.check(f"facility {HIP_ID} registered, ABDM callbacks go to {callback_url}", secret, out[-300:])
        self.webhook_secret = secret.group(1)

        self.step(2, "bridge URL registration is a dry run by default",
                  "this command changes settings on ABDM, so it only prints what it WOULD send unless you add --confirm")
        out = self.run(*self.flask, "abdm", "bridge-url", "set")
        self.check("dry run: nothing is sent to ABDM", "Dry run: nothing was sent" in out, out[-300:])

        self.step(3, "start the four processes", "mock ABDM, fake client app, the gateway, and the retry worker")
        self.start("mock", [sys.executable, "scripts/mock_abdm.py"])
        self.start("client", [sys.executable, "scripts/fake_client.py"],
                   {"WEBHOOK_SECRET": self.webhook_secret, "FAKE_CLIENT_FAIL_FIRST": str(FAKE_CLIENT_FAILS_FIRST)})
        self.start("gateway", [*self.flask, "run", "--port", str(self.ports["gateway"]), "--no-reload"])
        self.start("worker", [*self.flask, "worker", "run"])
        up = self.wait_until(lambda: self.get(f"http://127.0.0.1:{self.ports['gateway']}/health") == 200
                             and self.get(f"http://127.0.0.1:{self.ports['mock']}/") == 200
                             and self.get(f"http://127.0.0.1:{self.ports['client']}/") is not None, 40)
        self.check("all processes are up", up, self.log("gateway")[-400:])

    def scenario_delivery_with_retries(self):
        self.step(4, "a patient scans the facility QR",
                  f"ABDM calls the gateway; the fake client app refuses the first {FAKE_CLIENT_FAILS_FIRST} deliveries (503)")
        reply = self.abdm_calls_gateway(request_id="req-e2e-0001")
        self.check("gateway answered ABDM with 202 Accepted straight away", reply["gatewayStatus"] == 202, str(reply))
        delivered = self.wait_until(lambda: self.query("select status from callback_events")[0][0] == "delivered", 30)
        self.check("callback was delivered after retries", delivered, str(self.query("select status, error from callback_events")))
        calls = self.client_calls()
        self.check(f"client app was called {FAKE_CLIENT_FAILS_FIRST + 1} times ({FAKE_CLIENT_FAILS_FIRST} failures, 1 success)",
                   len(calls) == FAKE_CLIENT_FAILS_FIRST + 1, f"{len(calls)} calls")
        self.check("every call had a valid X-Gateway-Signature", all(c[4] == "VALID" for c in calls) and calls, str(calls))
        self.check("the URL tells the client app what the callback is (i-medicus picks its handler from it)",
                   {c[1] for c in calls} == {"api/v3/hip/patient/share"}, str(calls))
        self.check("the same event id on every retry (the client can dedupe)", len({c[3] for c in calls}) == 1, str(calls))
        job = self.query("select status, attempts from outbox_jobs")[0]
        self.check("queue job finished: done after 3 attempts", job == ("done", FAKE_CLIENT_FAILS_FIRST + 1), str(job))

    def scenario_duplicate(self):
        self.step(5, "ABDM sends the same callback again", "same REQUEST-ID and path: the second copy must not be delivered twice")
        before = len(self.client_calls())
        reply = self.abdm_calls_gateway(request_id="req-e2e-0001")
        self.check("gateway still answers 202 (ABDM must not retry)", reply["gatewayStatus"] == 202)
        self.check("and marks it as a duplicate", reply["gatewayBody"]["data"].get("duplicate") is True, str(reply))
        time.sleep(3)
        self.check("the client app was not called again", len(self.client_calls()) == before)
        self.check("still one stored callback", self.query("select count(*) from callback_events")[0][0] == 1)

    def scenario_forged(self):
        self.step(6, "someone sends a forged callback", "no ABDM JWT: it must be refused and nothing may be stored")
        reply = self.abdm_calls_gateway(request_id="req-forged-001", send_token=False)
        self.check("gateway answers 401", reply["gatewayStatus"] == 401, str(reply))
        self.check("nothing was stored", self.query("select count(*) from callback_events")[0][0] == 1)

    def scenario_unrouted_then_reroute(self):
        self.step(7, "a callback for a facility nobody registered",
                  "the gateway cannot know where to send it: it keeps it as 'unrouted' instead of losing it")
        before = len(self.client_calls())
        other = {**PATIENT_SHARE, "metaData": {"hipId": "IN9999999999"}}
        reply = self.abdm_calls_gateway(body=other, request_id="req-unrouted-01")
        self.check("gateway answers 202 (ABDM is not made to retry)", reply["gatewayStatus"] == 202, str(reply))
        row = self.query("select id, status, hip_id from callback_events where request_id = 'req-unrouted-01'")[0]
        self.check("stored as unrouted", row[1] == "unrouted", str(row))
        time.sleep(3)
        self.check("not delivered anywhere", len(self.client_calls()) == before)
        out = self.run(*self.flask, "callbacks", "list", "--status", "unrouted")
        self.check("`flask callbacks list --status unrouted` shows it", "IN9999999999" in out, out[-300:])
        out = self.run(*self.flask, "callbacks", "reroute", str(row[0]), "--hip-id", HIP_ID)
        self.check("`flask callbacks reroute` queues it for the registered facility", "queued" in out, out[-300:])
        self.check("and the worker delivers it",
                   self.wait_until(lambda: self.query(f"select status from callback_events where id = {row[0]}")[0][0] == "delivered", 20))

    def gateway_api(self, path, body, token=None):
        headers = {"Content-Type": "application/json", **({"Authorization": token} if token else {})}
        request = urllib.request.Request(f"http://127.0.0.1:{self.ports['gateway']}{path}", data=json.dumps(body).encode(),
                                         headers=headers)
        with urllib.request.urlopen(request, timeout=15) as reply:
            return json.loads(reply.read())

    def scenario_new_clinic_and_on_share(self):
        self.step(8, "a NEW clinic scans, with the client's default webhook",
                  "i-medicus has one webhook for all clinics, so a clinic nobody registered is routed to it and registered")
        callback_url = f"http://127.0.0.1:{self.ports['client']}/api/abdm-webhook"
        out = self.run(*self.flask, "webhook", "set", "--client", self.client_id, "--url", callback_url)
        self.check("default webhook set for the client app", "Default webhook" in out, out[-300:])
        new_hip = "IN-NEW-CLINIC-0001"
        scan = {**PATIENT_SHARE, "metaData": {"hipId": new_hip, "context": "hip"}}
        reply = self.abdm_calls_gateway(body=scan, request_id="req-newclinic-01")
        self.check("gateway answers 202", reply["gatewayStatus"] == 202, str(reply))
        self.check("the callback is delivered to the client app", self.wait_until(
            lambda: self.query("select status from callback_events where request_id = 'req-newclinic-01'")[0][0]
            == "delivered", 20), str(self.query("select status, error from callback_events")))
        row = self.query(f"select auto_registered, is_active from hip_registrations where hip_id = '{new_hip}'")
        self.check("the new clinic was registered by the gateway", row == [(1, 1)], str(row))

        self.step(9, "the client app answers the scan (on-share)",
                  "it sends the token number; the gateway finds the clinic from the requestId and tells ABDM")
        session = self.gateway_api("/api/v1/auth/session", {"clientId": self.client_id, "clientSecret": self.client_secret})
        token = session["data"]["accessToken"]
        answer = self.gateway_api("/api/v1/share/on-share",
                                  {"requestId": "req-newclinic-01", "abhaAddress": "ravi@sbx", "tokenNumber": "7"}, token)
        self.check("gateway answers sent", answer["data"] == {"status": "sent", "hipId": new_hip}, str(answer))
        with urllib.request.urlopen(f"http://127.0.0.1:{self.ports['mock']}/mock/on-share-calls", timeout=5) as reply:
            calls = json.loads(reply.read())
        self.check("ABDM (the mock) received it for the right clinic with the token",
                   len(calls) == 1 and calls[0]["hipId"] == new_hip
                   and calls[0]["body"]["acknowledgement"]["profile"]["tokenNumber"] == "7"
                   and calls[0]["body"]["response"]["requestId"] == "req-newclinic-01", str(calls))

    def scenario_queue_is_clean(self):
        self.step(10, "look at the queue", "`flask outbox stats` is how you see whether anything is stuck")
        out = self.run(*self.flask, "outbox", "stats")
        print("   " + out.strip().replace("\n", "\n   "))
        self.check("nothing pending, nothing dead", "pending   : 0" in out and "dead      : 0" in out, out)

    def finish(self):
        passed = sum(1 for _, ok in self.results if ok)
        print(f"\n{passed}/{len(self.results)} checks passed")
        for description, ok in self.results:
            if not ok:
                print(f"  FAILED: {description}")
        return passed == len(self.results)

    def keep_running(self):
        gw, mock, client = (f"http://127.0.0.1:{self.ports[n]}" for n in ("gateway", "mock", "client"))
        print(f"""
Everything is still running. Try it yourself (Ctrl+C here stops it all):

  gateway   {gw}/docs          (Swagger)
  mock ABDM {mock}/             (status page)
  fake app  logs: {os.path.join(self.tmp, 'client.log')}

Play ABDM (PowerShell):
  Invoke-RestMethod -Method Post {mock}/mock/callback -ContentType application/json -Body '{{"path": "{SHARE_PATH}", "gatewayUrl": "{gw}", "body": {{"metaData": {{"hipId": "{HIP_ID}"}}}}}}'

Look inside (same environment as the drill, run from {ROOT}):
  $env:DATABASE_URL = "sqlite:///{self.db.replace(chr(92), '/')}"
  flask --app wsgi callbacks list
  flask --app wsgi outbox stats
""")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    def stop(self):
        for _, proc, _ in self.procs:
            proc.terminate()
        for _, proc, log in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()


def main():
    parser = argparse.ArgumentParser(description="Local end-to-end test of the ABDM callback workflow (no real ABDM).")
    parser.add_argument("--keep", action="store_true", help="keep everything running afterwards, to try it yourself")
    args = parser.parse_args()

    drill = Drill(args.keep)
    ok = False
    try:
        drill.setup()
        drill.scenario_delivery_with_retries()
        drill.scenario_duplicate()
        drill.scenario_forged()
        drill.scenario_unrouted_then_reroute()
        drill.scenario_new_clinic_and_on_share()
        drill.scenario_queue_is_clean()
        ok = drill.finish()
        if args.keep:
            drill.keep_running()
    finally:
        drill.stop()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
