"""
Mock ABDM server for local development without NHA sandbox credentials.

It implements the ABDM v3 endpoints the gateway uses for M1, with its own RSA key served at
/abha/api/v3/profile/public/certificate, so the gateway's encryption is exercised for real:
every encrypted field is decrypted and checked here.

Run:
    python scripts/mock_abdm.py               # http://127.0.0.1:5099
Point the gateway at it (.env):
    ABDM_GATEWAY_URL=http://127.0.0.1:5099
    ABDM_ABHA_URL=http://127.0.0.1:5099
    NHA_CLIENT_ID=mock-client
    NHA_CLIENT_SECRET=mock-secret

It also plays ABDM for CALLBACKS: it publishes a JWKS at /api/hiecm/gateway/v3/certs and has
POST /mock/callback, which signs a JWT and calls the gateway's callback URL like ABDM does:
    curl -X POST http://127.0.0.1:5099/mock/callback -H "Content-Type: application/json" \
      -d '{"path": "/api/v3/hip/patient/share", "body": {"metaData": {"hipId": "IN0110005723"}}}'
(for the gateway to accept it, leave CALLBACK_AUTH_MODE=jwt and ABDM_GATEWAY_URL pointing at this mock)

Test data: OTP 123456 is correct, anything else fails. Aadhaar ending in 0000 has no linked mobile
(UIDAI error 111). Every mobile has one ABHA.
"""
import base64
import os
import time
import uuid

import jwt
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_DER_B64 = base64.b64encode(
    KEY.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
).decode()
OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None)

GOOD_OTP = "123456"
ABHA_NUMBER = "91-1234-5678-9012"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PROFILE = {
    "ABHANumber": ABHA_NUMBER, "preferredAbhaAddress": "ramesh.kumar@sbx", "firstName": "Ramesh",
    "middleName": "", "lastName": "Kumar", "name": "Ramesh Kumar", "gender": "M", "yearOfBirth": "1990",
    "monthOfBirth": "01", "dayOfBirth": "15", "mobile": "9876543210", "email": None, "pincode": "110001",
    "stateName": "DELHI", "districtName": "New Delhi", "subdistrictName": "", "townName": "",
    "address": "12 Example Street, New Delhi", "profilePhoto": None, "status": "ACTIVE", "kycVerified": True,
}
TOKENS = {"token": "mock-x-token", "expiresIn": 1800, "refreshToken": "mock-r-token", "refreshExpiresIn": 1296000}


def dec(value):
    try:
        return KEY.decrypt(base64.b64decode(value), OAEP).decode()
    except Exception:  # noqa: BLE001
        raise BadRequest(f"Could not decrypt value: {str(value)[:20]}...")


class BadRequest(Exception):
    pass


@app.errorhandler(BadRequest)
def _bad(err):
    return jsonify({"code": "ABDM-1100", "message": str(err)}), 400


def require_gateway_token():
    if request.headers.get("Authorization") != "Bearer mock-gateway-token":
        return jsonify({"code": "ABDM-1006", "message": "Unauthorized"}), 401
    return None


def require_x_token():
    if request.headers.get("X-token") != "Bearer mock-x-token":
        return jsonify({"code": "ABDM-1006", "message": "X-token expired or invalid"}), 401
    return None


@app.before_request
def _check():
    # the sessions call, the JWKS and the status page are public; /mock/* is for the developer, not part of ABDM
    if request.path == "/" or request.path.endswith(("/gateway/v3/sessions", "/gateway/v3/certs"))             or request.path.startswith("/mock/"):
        return None
    return require_gateway_token()


def otp_ok(body):
    return dec(body["authData"]["otp"]["otpValue"]) == GOOD_OTP


# ---- callbacks: this mock plays ABDM when it calls the gateway --------------------------

CALLBACK_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CALLBACK_KID = "mock-kid-1"


@app.get("/api/hiecm/gateway/v3/certs")
def certs():
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(CALLBACK_KEY.public_key(), as_dict=True)
    jwk.update({"kid": CALLBACK_KID, "use": "sig", "alg": "RS256"})
    return jsonify({"keys": [jwk]})


@app.post("/mock/callback")
def fire_callback():
    """Body: {"path": "/api/v3/hip/patient/share", "body": {...}, "gatewayUrl": "http://127.0.0.1:5000",
    "requestId": "optional", "sendToken": true}. Returns what the gateway answered."""
    data = request.get_json(force=True)
    token = jwt.encode({"exp": int(time.time()) + 300, "iss": "mock-abdm"}, CALLBACK_KEY, algorithm="RS256",
                       headers={"kid": CALLBACK_KID})
    headers = {"REQUEST-ID": data.get("requestId") or str(uuid.uuid4()), "X-CM-ID": "sbx",
               "TIMESTAMP": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "Content-Type": "application/json"}
    if data.get("sendToken", True):
        headers["Authorization"] = f"Bearer {token}"
    url = data.get("gatewayUrl", "http://127.0.0.1:5000").rstrip("/") + data["path"]
    reply = requests.post(url, json=data.get("body", {}), headers=headers, timeout=10)
    return jsonify({"gatewayStatus": reply.status_code, "gatewayBody": reply.json() if reply.content else None})


ON_SHARE_CALLS = []


@app.post("/api/hiecm/patient-share/v3/on-share")
def on_share():
    """Scan & Share answer from a HIP (the token number the patient sees). Kept so a test can look at it."""
    ON_SHARE_CALLS.append({"hipId": request.headers.get("X-HIP-ID"), "body": request.get_json(force=True)})
    return jsonify({}), 202


@app.get("/mock/on-share-calls")
def on_share_calls():
    return jsonify(ON_SHARE_CALLS)


@app.patch("/api/hiecm/gateway/v3/bridge/url")
def bridge_url():
    return jsonify({"message": "bridge url updated (mock)", "url": request.get_json(force=True).get("url")})


@app.post("/v1/bridges/MutipleHRPAddUpdateServices")
def add_services():
    return jsonify({"message": "services updated (mock)", "count": len(request.get_json(force=True)["HRP"])})


@app.get("/")
def status_page():
    """Opening the mock in a browser: every ABDM endpoint wants a gateway token, so this tells you it is alive."""
    return jsonify({
        "service": "mock ABDM (local development only, not the real ABDM)",
        "status": "running",
        "publicEndpoints": ["GET /api/hiecm/gateway/v3/certs (JWKS)", "POST /mock/callback (play ABDM calling the gateway)"],
        "note": "all other ABDM endpoints answer 401 without a gateway token, like the real ABDM",
    })


@app.post("/api/hiecm/gateway/v3/sessions")
def sessions():
    body = request.get_json()
    if not body.get("clientId") or not body.get("clientSecret"):
        return jsonify({"message": "invalid client"}), 401
    return jsonify({"accessToken": "mock-gateway-token", "expiresIn": 1200, "tokenType": "bearer"})


@app.get("/abha/api/v3/profile/public/certificate")
def certificate():
    return jsonify({"publicKey": PUBLIC_DER_B64, "encryptionAlgorithm": "RSA/ECB/OAEPWithSHA-1AndMGF1Padding"})


# ---- enrolment -----------------------------------------------------------------------

@app.post("/abha/api/v3/enrollment/request/otp")
def enrol_request_otp():
    body = request.get_json()
    login_id = dec(body["loginId"])
    if body["loginHint"] == "aadhaar":
        if login_id.endswith("0000"):
            return jsonify({"error": {"code": "ABDM-1204",
                                      "message": "UIDAI Error code : 111 : Aadhaar does not have verified mobile"}}), 422
        return jsonify({"txnId": str(uuid.uuid4()),
                        "message": f"OTP sent to Aadhaar registered mobile number ending with ******{login_id[-4:]}"})
    return jsonify({"txnId": body.get("txnId") or str(uuid.uuid4()),
                    "message": f"OTP sent to mobile number ending with ******{login_id[-4:]}"})


@app.post("/abha/api/v3/enrollment/enrol/byAadhaar")
def enrol_by_aadhaar():
    body = request.get_json()
    if not otp_ok(body):
        return jsonify({"code": "ABDM-1100", "message": "Invalid OTP value."}), 400
    mobile = body["authData"]["otp"]["mobile"]
    profile = {
        "preferredAddress": "ramesh.kumar@sbx", "firstName": "Ramesh", "middleName": "", "lastName": "Kumar",
        "dob": "15-01-1990", "gender": "M", "photo": None,
        # Aadhaar-linked mobile differs when the entered mobile ends in 1 → client must verify the new mobile
        "mobile": None if mobile.endswith("1") else mobile, "mobileVerified": not mobile.endswith("1"),
        "email": None, "phrAddress": ["ramesh.kumar@sbx"], "address": "12 Example Street", "districtCode": "1",
        "stateCode": "7", "pinCode": "110001", "abhaType": "STANDARD", "stateName": "DELHI",
        "districtName": "New Delhi", "ABHANumber": ABHA_NUMBER, "abhaStatus": "ACTIVE",
    }
    return jsonify({"message": "Account created successfully", "txnId": body["authData"]["otp"]["txnId"],
                    "tokens": TOKENS, "ABHAProfile": profile, "isNew": True})


@app.post("/abha/api/v3/enrollment/auth/byAbdm")
def enrol_auth_by_abdm():
    body = request.get_json()
    txn = body["authData"]["otp"]["txnId"]
    if not otp_ok(body):
        return jsonify({"txnId": txn, "authResult": "failed", "message": "Please enter a valid OTP. Entered OTP is either expired or incorrect."})
    return jsonify({"txnId": txn, "authResult": "success", "message": "Mobile number is now successfully linked to your Account"})


@app.get("/abha/api/v3/enrollment/enrol/suggestion")
def suggestion():
    return jsonify({"txnId": request.headers.get("Transaction_Id"),
                    "abhaAddressList": ["ramesh.kumar", "ramesh1990", "kumar.ramesh"]})


@app.post("/abha/api/v3/enrollment/enrol/abha-address")
def create_address():
    body = request.get_json()
    return jsonify({"txnId": body["txnId"], "healthIdNumber": ABHA_NUMBER,
                    "preferredAbhaAddress": f"{body['abhaAddress']}@sbx"})


@app.post("/abha/api/v3/profile/account/request/emailVerificationLink")
def email_link():
    denied = require_x_token()
    if denied:
        return denied
    email = dec(request.get_json()["loginId"])
    return jsonify({"txnId": str(uuid.uuid4()), "message": f"Verification link has been sent to {email}"})


@app.post("/abha/api/v3/profile/account/request/otp")
def profile_request_otp():
    denied = require_x_token()
    if denied:
        return denied
    body = request.get_json()
    value = dec(body["loginId"])
    what = "mobile number" if body["loginHint"] == "mobile" else "email"
    return jsonify({"txnId": str(uuid.uuid4()), "message": f"OTP sent to {what} {value[:2]}******"})


@app.post("/abha/api/v3/profile/account/verify")
def profile_verify():
    denied = require_x_token()
    if denied:
        return denied
    body = request.get_json()
    txn = body["authData"]["otp"]["txnId"]
    if not otp_ok(body):
        return jsonify({"txnId": txn, "authResult": "failed", "message": "Please enter a valid OTP. Entered OTP is either expired or incorrect."})
    return jsonify({"txnId": txn, "authResult": "success", "message": "Profile updated successfully"})


# ---- search + login ------------------------------------------------------------------

@app.post("/abha/api/v3/profile/account/abha/search")
def search_mobile():
    dec(request.get_json()["mobile"])
    return jsonify([{"txnId": str(uuid.uuid4()),
                     "ABHA": [{"index": 1, "ABHANumber": "xx-xxxx-xxxx-9012", "name": "Ramesh Kumar", "gender": "M"}]}])


@app.post("/abha/api/v3/profile/login/request/otp")
def login_request_otp():
    body = request.get_json()
    dec(body["loginId"])
    return jsonify({"txnId": body.get("txnId") or str(uuid.uuid4()), "message": "OTP sent to mobile number ending with ******3210"})


@app.post("/abha/api/v3/profile/login/verify")
def login_verify():
    body = request.get_json()
    txn = body["authData"]["otp"]["txnId"]
    if not otp_ok(body):
        return jsonify({"txnId": txn, "authResult": "failed", "message": "Please enter a valid OTP. Entered OTP is either expired or incorrect."})
    return jsonify({"txnId": txn, "authResult": "success", "message": "OTP verified successfully", **TOKENS,
                    "accounts": [{"ABHANumber": ABHA_NUMBER, "preferredAbhaAddress": "ramesh.kumar@sbx", "name": "Ramesh Kumar"}]})


@app.post("/abha/api/v3/profile/login/verify/user")
def login_verify_user():
    return jsonify({**TOKENS, "message": "User verified"})


@app.post("/abha/api/v3/phr/web/login/abha/search")
def phr_search():
    address = request.get_json()["abhaAddress"]
    if not address.startswith("ramesh"):
        return jsonify({"code": "ABDM-1114", "message": "ABHA address not found"}), 404
    return jsonify({"healthIdNumber": ABHA_NUMBER, "abhaAddress": address, "authMethods": ["MOBILE_OTP", "AADHAAR_OTP"],
                    "fullName": "Ramesh Kumar", "status": "ACTIVE"})


@app.post("/abha/api/v3/phr/web/login/abha/request/otp")
def phr_request_otp():
    dec(request.get_json()["loginId"])
    return jsonify({"txnId": str(uuid.uuid4()), "message": "OTP sent to mobile number ending with ******3210"})


@app.post("/abha/api/v3/phr/web/login/abha/verify")
def phr_verify():
    body = request.get_json()
    if not otp_ok(body):
        return jsonify({"authResult": "failed", "message": "Please enter a valid OTP. Entered OTP is either expired or incorrect."})
    return jsonify({"message": "OTP verified successfully", "authResult": "success", "tokens": TOKENS,
                    "users": [{"abhaAddress": "ramesh.kumar@sbx", "fullName": "Ramesh Kumar", "abhaNumber": ABHA_NUMBER,
                               "status": "ACTIVE", "kycStatus": "VERIFIED"}]})


# ---- profile / card / QR -------------------------------------------------------------

@app.get("/abha/api/v3/profile/account")
def account():
    return require_x_token() or jsonify(PROFILE)


@app.get("/abha/api/v3/phr/web/login/profile/abha-profile")
def phr_profile():
    return require_x_token() or jsonify({
        "abhaAddress": "ramesh.kumar@sbx", "fullName": "Ramesh Kumar", "firstName": "Ramesh", "lastName": "Kumar",
        "gender": "M", "dateOfBirth": "15-01-1990", "mobile": "9876543210", "address": "12 Example Street",
        "stateName": "DELHI", "districtName": "New Delhi", "pinCode": "110001", "kycStatus": "VERIFIED",
        "status": "ACTIVE", "yearOfBirth": "1990", "monthOfBirth": "01", "dayOfBirth": "15",
    })


@app.get("/abha/api/v3/profile/account/abha-card")
@app.get("/abha/api/v3/phr/web/login/profile/abha/phr-card")
@app.get("/abha/api/v3/profile/account/qrCode")
def png_file():
    return require_x_token() or Response(PNG, mimetype="image/png")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("MOCK_ABDM_PORT", 5099)))
