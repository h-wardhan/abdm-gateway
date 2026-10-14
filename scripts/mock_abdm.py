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

Test data: OTP 123456 is correct, anything else fails. Aadhaar ending in 0000 has no linked mobile
(UIDAI error 111). Every mobile has one ABHA.
"""
import base64
import os
import uuid

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
    if request.path.endswith("/gateway/v3/sessions"):
        return None
    return require_gateway_token()


def otp_ok(body):
    return dec(body["authData"]["otp"]["otpValue"]) == GOOD_OTP


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
