"""
A stand-in for a client app's webhook (what i-medicus' /api/abdm-webhook does), for local testing.

    python scripts/fake_client.py                      # http://127.0.0.1:5055/api/abdm-webhook/...
    WEBHOOK_SECRET=<secret from `flask hips add`>      verify X-Gateway-Signature and say so
    FAKE_CLIENT_FAIL_FIRST=2                           answer 503 to the first 2 deliveries (retry drill)
    FAKE_HIU_FAIL_FIRST=1                              answer 503 to the first data push(es) (HIU down drill)
    POST /api/abdm-webhook is the push URL of a HIU; GET /pushed lists what was pushed

Register it with the gateway (local only, it is a private address):
    CALLBACK_ALLOW_PRIVATE_URLS=true CALLBACK_ALLOW_HTTP=true
    flask hips add --client <client_id> --hip-id IN0110005723 --callback-url http://127.0.0.1:5055/api/abdm-webhook
"""
import hashlib
import hmac
import os

from flask import Flask, jsonify, request

app = Flask(__name__)
seen = {"count": 0}


@app.post("/api/abdm-webhook/<path:key>")
def webhook(key):
    seen["count"] += 1
    body = request.get_data()
    secret = os.environ.get("WEBHOOK_SECRET")
    signature = "not checked"
    if secret:
        timestamp = request.headers.get("X-Gateway-Timestamp", "")
        expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        signature = "VALID" if hmac.compare_digest(expected, request.headers.get("X-Gateway-Signature", "")) else "INVALID"
    print(f"[{seen['count']}] urlKey={key} request-id={request.headers.get('request-id')} "
          f"event={request.headers.get('X-Gateway-Event-Id')} signature={signature} bytes={len(body)}", flush=True)
    print(f"    body: {body.decode('utf-8', 'replace')[:300]}", flush=True)
    if seen["count"] <= int(os.environ.get("FAKE_CLIENT_FAIL_FIRST", 0)):
        return jsonify({"status": "error"}), 503
    return jsonify({"status": "success"})


pushed = []


@app.post("/api/abdm-webhook")
def data_push():
    """A HIP pushing encrypted health records (this app playing a HIU: its DATA_PUSH_URL is this URL)."""
    body = request.get_json(force=True, silent=True) or {}
    seen["pushes"] = seen.get("pushes", 0) + 1
    print(f"[push {seen['pushes']}] transactionId={body.get('transactionId')} entries={len(body.get('entries') or [])}",
          flush=True)
    if seen["pushes"] <= int(os.environ.get("FAKE_HIU_FAIL_FIRST", 0)):
        return jsonify({"status": "error"}), 503
    pushed.append(body)
    return jsonify({"status": "success"})


@app.get("/pushed")
def pushed_records():
    return jsonify(pushed)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("FAKE_CLIENT_PORT", 5055)))
