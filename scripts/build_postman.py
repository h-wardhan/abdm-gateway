"""
Generate docs/postman_collection.json.

    python scripts/build_postman.py

Collection variables: baseUrl, clientId, clientSecret (fill these), accessToken, txnId, xToken (set
automatically by the test scripts after session / OTP / verify calls).
"""
import json
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "postman_collection.json")

SAVE_TXN = "const d = pm.response.json().data || {}; if (d.txnId) pm.collectionVariables.set('txnId', d.txnId);"
SAVE_SEARCH_TXN = "const d = pm.response.json().data || []; if (d[0] && d[0].txnId) pm.collectionVariables.set('txnId', d[0].txnId);"
SAVE_X = ("const d = pm.response.json().data || {}; const t = d.token || (d.tokens && d.tokens.token); "
          "if (t) pm.collectionVariables.set('xToken', t); if (d.txnId) pm.collectionVariables.set('txnId', d.txnId);")


def req(name, method, path, body=None, test=None, auth=True, headers=None, description=""):
    hdrs = [{"key": "Content-Type", "value": "application/json"}] if body is not None else []
    if auth:
        hdrs += [{"key": "Authorization", "value": "{{accessToken}}"}, {"key": "Client-Id", "value": "{{clientId}}"}]
    hdrs += headers or []
    raw_url = "{{baseUrl}}" + path
    item = {
        "name": name,
        "request": {
            "method": method,
            "header": hdrs,
            "url": {"raw": raw_url, "host": ["{{baseUrl}}"], "path": [p for p in path.split("?")[0].split("/") if p]},
            "description": description,
        },
    }
    if "?" in path:
        query = path.split("?", 1)[1]
        item["request"]["url"]["query"] = [{"key": k, "value": v} for k, v in (q.split("=", 1) for q in query.split("&"))]
    if body is not None:
        item["request"]["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    if test:
        item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": [test]}}]
    return item


def folder(name, items, description=""):
    return {"name": name, "description": description, "item": items}


collection = {
    "info": {
        "name": "ABDM Gateway (M1)",
        "description": "abdm-gateway M1 API. Set baseUrl, clientId, clientSecret, run 'Create session' first. "
                       "Sandbox OTPs arrive on the Aadhaar/ABHA-linked mobile (with scripts/mock_abdm.py the OTP is 123456).",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "variable": [
        {"key": "baseUrl", "value": "http://localhost:5000"},
        {"key": "clientId", "value": ""},
        {"key": "clientSecret", "value": ""},
        {"key": "accessToken", "value": ""},
        {"key": "txnId", "value": ""},
        {"key": "xToken", "value": ""},
    ],
    "item": [
        folder("Health", [
            req("Health", "GET", "/health", auth=False),
            req("Ready", "GET", "/ready", auth=False),
        ]),
        folder("Session", [
            req("Create session", "POST", "/api/v1/auth/session",
                {"clientId": "{{clientId}}", "clientSecret": "{{clientSecret}}"}, auth=False,
                test="pm.collectionVariables.set('accessToken', pm.response.json().data.accessToken);"),
            req("Who am I", "GET", "/api/v1/auth/me"),
        ]),
        folder("1. ABHA creation (Aadhaar OTP)", [
            req("Send Aadhaar OTP", "POST", "/api/v1/abha/enrol/aadhaar/otp", {"aadhaarNumber": "999941057058"}, SAVE_TXN),
            req("Verify Aadhaar OTP (create ABHA)", "POST", "/api/v1/abha/enrol/aadhaar/verify",
                {"txnId": "{{txnId}}", "otpValue": "123456", "mobilenumber": "9876543210"}, SAVE_X),
            req("Send mobile OTP (if mobile differs)", "POST", "/api/v1/abha/enrol/mobile/otp",
                {"txnId": "{{txnId}}", "mobilenumber": "9876543210"}, SAVE_TXN),
            req("Verify mobile OTP", "POST", "/api/v1/abha/enrol/mobile/verify", {"txnId": "{{txnId}}", "otp": "123456"}),
            req("ABHA address suggestions", "GET", "/api/v1/abha/enrol/address-suggestions?txnId={{txnId}}"),
            req("Create ABHA address", "POST", "/api/v1/abha/enrol/address",
                {"txnId": "{{txnId}}", "abhaAddress": "ramesh.kumar", "preferred": 1}),
            req("Send email verification link", "POST", "/api/v1/abha/profile/email/verify-link",
                {"email": "patient@example.com"}, headers=[{"key": "X-Token", "value": "{{xToken}}"}]),
        ]),
        folder("2. Login by mobile", [
            req("Search ABHA by mobile", "POST", "/api/v1/abha/search/mobile", {"mobileNumber": "9876543210"}, SAVE_SEARCH_TXN),
            req("Send OTP (pick index)", "POST", "/api/v1/abha/login/mobile/otp", {"txnId": "{{txnId}}", "index": 1}, SAVE_TXN),
            req("Verify OTP", "POST", "/api/v1/abha/login/mobile/verify", {"txnId": "{{txnId}}", "otp": "123456"}, SAVE_X),
        ]),
        folder("3. Login by ABHA number", [
            req("Check ABHA number", "POST", "/api/v1/abha/search/abha-number", {"abhaNumber": "91-1234-5678-9012"}),
            req("Send OTP (mobile)", "POST", "/api/v1/abha/login/abha-number/mobile/otp", {"abhaNumber": "91-1234-5678-9012"}, SAVE_TXN),
            req("Verify OTP (mobile)", "POST", "/api/v1/abha/login/abha-number/mobile/verify", {"txnId": "{{txnId}}", "otp": "123456"}, SAVE_X),
            req("Send OTP (aadhaar)", "POST", "/api/v1/abha/login/abha-number/aadhaar/otp", {"abhaNumber": "91-1234-5678-9012"}, SAVE_TXN),
            req("Verify OTP (aadhaar)", "POST", "/api/v1/abha/login/abha-number/aadhaar/verify", {"txnId": "{{txnId}}", "otp": "123456"}, SAVE_X),
        ]),
        folder("4. Login by Aadhaar", [
            req("Send OTP", "POST", "/api/v1/abha/login/aadhaar/otp", {"aadhaarNumber": "999941057058"}, SAVE_TXN),
            req("Verify OTP", "POST", "/api/v1/abha/login/aadhaar/verify", {"txnId": "{{txnId}}", "otp": "123456"}, SAVE_X),
        ]),
        folder("5. Login by ABHA address", [
            req("Search ABHA address", "POST", "/api/v1/abha/search/abha-address", {"abhaAddress": "ramesh.kumar@sbx"}),
            req("Send OTP", "POST", "/api/v1/abha/login/abha-address/otp",
                {"abhaAddress": "ramesh.kumar@sbx", "scope": "mobile-verify"}, SAVE_TXN),
            req("Verify OTP", "POST", "/api/v1/abha/login/abha-address/verify",
                {"txnId": "{{txnId}}", "otp": "123456", "scope": "mobile-verify"}, SAVE_X),
            req("PHR profile", "GET", "/api/v1/abha/phr/profile?xToken={{xToken}}"),
            req("PHR card", "GET", "/api/v1/abha/phr/card?xToken={{xToken}}"),
        ]),
        folder("6. Profile, card, QR", [
            req("ABHA profile", "GET", "/api/v1/abha/profile?xToken={{xToken}}"),
            req("ABHA card", "GET", "/api/v1/abha/profile/card?xToken={{xToken}}"),
            req("ABHA QR code", "GET", "/api/v1/abha/profile/qr?xToken={{xToken}}"),
        ]),
    ],
}

if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2)
        f.write("\n")
    print(f"Wrote {os.path.abspath(OUT)}")
