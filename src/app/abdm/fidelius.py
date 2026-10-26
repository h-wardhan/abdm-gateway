"""
Fidelius: the end-to-end encryption ABDM uses for health records moving between a HIP and a HIU (M2 / M3).

The sender (HIP) and the receiver (HIU) each have key material: a key pair on the Curve25519 curve in its
SHORT WEIERSTRASS form (BouncyCastle's "curve25519"; NOT the Montgomery form that X25519 / RFC 7748 uses, the two
give different shared secrets) and a random 32 byte nonce. Both sides exchange public keys and nonces.

    shared secret  = x coordinate of (own private key * other side's public key)        32 bytes, big endian
    xor            = sender nonce XOR receiver nonce                                    32 bytes
    AES key        = HKDF-SHA256(secret = shared secret, salt = xor[:20], info = empty)  32 bytes
    IV             = xor[-12:]
    encrypted data = base64( AES-256-GCM(key, IV, plaintext) )   (ciphertext followed by the 16 byte tag)

Compatible with ABDM's reference tool (https://github.com/mgrmtech/fidelius-cli); its published test vector is in
tests/unit/test_fidelius.py.

Public keys travel as base64 of either the raw uncompressed point (65 bytes, 0x04 || X || Y) or an X.509
SubjectPublicKeyInfo with the curve parameters spelled out (what ABDM puts in `dhPublicKey.keyValue`). Both are
accepted; `KeyMaterial` offers both.

Pure Python big-integer arithmetic: fine for the few operations per transfer, but not constant time. The key pairs
are meant to be used once (a new one per data transfer), which is what ABDM requires anyway.
"""
import base64
import binascii
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---- the curve: Curve25519 in short Weierstrass form  y^2 = x^3 + A x + B  (mod P) ---------------------------------
# Derived from the Montgomery form (A_MONT, base point u = 9) so no long constant can be mistyped;
# the tests check the result against the values published for BouncyCastle's curve.
P = 2**255 - 19
N = 2**252 + 27742317777372353535851937790883648493   # order of the base point
H = 8                                                  # cofactor
_A_MONT = 486662
_INV3 = pow(3, -1, P)
A = ((3 - _A_MONT * _A_MONT) * _INV3) % P
B = ((2 * _A_MONT**3 - 9 * _A_MONT) * pow(27, -1, P)) % P
GX = (9 + _A_MONT * _INV3) % P
GY = 0x20AE19A1B8A086B4E01EDD2C7748D14C923D4D7E6D7C61B229E9C5A27ECED3D9
G = (GX, GY)

KEY_BYTES = 32
NONCE_BYTES = 32
POINT_BYTES = 65
TAG_BYTES = 16
UNCOMPRESSED = 4  # first byte of an uncompressed point


class FideliusError(ValueError):
    """Bad input: a key, nonce or payload that is not what Fidelius expects."""


class DecryptionError(FideliusError):
    """The data could not be decrypted: wrong keys or nonces, or the data was changed."""


# ---- elliptic curve arithmetic (affine coordinates, None = point at infinity) ---------------------------------------

def _inv(value):
    return pow(value, -1, P)


def _on_curve(point):
    x, y = point
    return (y * y - (x * x * x + A * x + B)) % P == 0


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 + A) * _inv(2 * y1) % P
    else:
        slope = (y2 - y1) * _inv(x2 - x1) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def _mul(scalar, point):
    result = None
    for bit in bin(scalar)[2:]:
        result = _add(result, result)
        if bit == "1":
            result = _add(result, point)
    return result


# ---- encodings ------------------------------------------------------------------------------------------------------

def _b64decode(value, what, length=None):
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise FideliusError(f"{what} is not valid base64")
    if length is not None and len(data) != length:
        raise FideliusError(f"{what} must be {length} bytes, got {len(data)}")
    return data


def _b64encode(data):
    return base64.b64encode(data).decode("ascii")


def _der(tag, content):
    size = len(content)
    if size < 0x80:
        header = bytes([tag, size])
    elif size < 0x100:
        header = bytes([tag, 0x81, size])
    else:
        header = bytes([tag, 0x82, size >> 8, size & 0xFF])
    return header + content


def _der_integer(value):
    return _der(0x02, value.to_bytes((value.bit_length() + 8) // 8, "big"))  # room for a sign bit


def _spki_algorithm():
    """X.509 AlgorithmIdentifier with the curve parameters spelled out (explicit EC parameters)."""
    ec_public_key = _der(0x06, bytes.fromhex("2a8648ce3d0201"))
    prime_field = _der(0x30, _der(0x06, bytes.fromhex("2a8648ce3d0101")) + _der_integer(P))
    curve = _der(0x30, _der(0x04, A.to_bytes(32, "big")) + _der(0x04, B.to_bytes(32, "big")))
    base_point = _der(0x04, bytes([UNCOMPRESSED]) + GX.to_bytes(32, "big") + GY.to_bytes(32, "big"))
    parameters = _der(0x30, _der_integer(1) + prime_field + curve + base_point + _der_integer(N) + _der_integer(H))
    return _der(0x30, ec_public_key + parameters)


# Everything of an X.509 public key before the 65 byte point: the same for every key on this curve
_BIT_STRING_HEADER = bytes([0x03, POINT_BYTES + 1, 0x00])
_X509_PREFIX = _der(0x30, _spki_algorithm() + _BIT_STRING_HEADER + bytes(POINT_BYTES))[:-POINT_BYTES]


def encode_point(point):
    return bytes([UNCOMPRESSED]) + point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")


def encode_x509(point):
    return _X509_PREFIX + encode_point(point)


def parse_public_key(value):
    """Base64 public key (raw point or X.509) -> a validated curve point. Raises FideliusError."""
    data = _b64decode(value, "public key")
    if len(data) == POINT_BYTES:
        raw = data
    elif len(data) == len(_X509_PREFIX) + POINT_BYTES and data.startswith(_X509_PREFIX):
        raw = data[len(_X509_PREFIX):]
    else:
        raise FideliusError("public key is neither a 65 byte point nor an X.509 key for Curve25519")
    if raw[0] != UNCOMPRESSED:
        raise FideliusError("public key must be an uncompressed point (0x04 prefix)")
    point = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big"))
    if not (0 <= point[0] < P and 0 <= point[1] < P) or not _on_curve(point):
        raise FideliusError("public key is not a point on Curve25519")
    if _mul(N, point) is not None:  # blocks small-subgroup points (the cofactor is 8)
        raise FideliusError("public key is not in the prime order subgroup")
    return point


def _private_scalar(private_key):
    data = _b64decode(private_key, "private key", KEY_BYTES)
    scalar = int.from_bytes(data, "big")
    if not 1 <= scalar < N:
        raise FideliusError("private key is out of range")
    return scalar


def _nonce(value, what):
    return _b64decode(value, what, NONCE_BYTES)


# ---- key material ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class KeyMaterial:
    private_key: bytes   # 32 bytes, big endian scalar
    public_key: bytes    # 65 bytes, uncompressed point
    nonce: bytes         # 32 bytes

    @property
    def private_key_b64(self):
        return _b64encode(self.private_key)

    @property
    def public_key_b64(self):
        return _b64encode(self.public_key)

    @property
    def x509_public_key_b64(self):
        return _b64encode(_X509_PREFIX + self.public_key)

    @property
    def nonce_b64(self):
        return _b64encode(self.nonce)


def generate_key_material(rng=secrets):
    """A fresh key pair and nonce. `rng` is anything with randbelow() and token_bytes() (the `secrets` module)."""
    scalar = 1 + rng.randbelow(N - 1)
    return KeyMaterial(
        private_key=scalar.to_bytes(KEY_BYTES, "big"),
        public_key=encode_point(_mul(scalar, G)),
        nonce=rng.token_bytes(NONCE_BYTES),
    )


def public_key_from_private(private_key):
    """Base64 raw public key for a base64 private key."""
    return _b64encode(encode_point(_mul(_private_scalar(private_key), G)))


KEY_MATERIAL_VALIDITY = timedelta(hours=24)


def key_material_block(material, expires_in=KEY_MATERIAL_VALIDITY, now=None):
    """The `keyMaterial` object ABDM exchanges in consent / health information requests."""
    expiry = (now or datetime.now(timezone.utc)) + expires_in
    return {
        "cryptoAlg": "ECDH",
        "curve": "Curve25519",
        "dhPublicKey": {
            "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "parameters": "Curve25519/32byte random key",
            "keyValue": material.x509_public_key_b64,
        },
        "nonce": material.nonce_b64,
    }


def read_key_material_block(block, now=None):
    """Other side's `keyMaterial` -> (public key base64, nonce base64). Rejects an expired or malformed block."""
    try:
        public_key = block["dhPublicKey"]["keyValue"]
        expiry = block["dhPublicKey"]["expiry"]
        nonce = block["nonce"]
    except (KeyError, TypeError):
        raise FideliusError("keyMaterial needs dhPublicKey.keyValue, dhPublicKey.expiry and nonce")
    try:
        expires_at = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
    except ValueError:
        raise FideliusError("keyMaterial expiry is not a date")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= (now or datetime.now(timezone.utc)):
        raise FideliusError("keyMaterial has expired")
    parse_public_key(public_key)
    _nonce(nonce, "nonce")
    return public_key, nonce


# ---- encrypt / decrypt ----------------------------------------------------------------------------------------------

def _cipher(own_private_key, own_nonce, other_public_key, other_nonce):
    own_nonce_bytes = _nonce(own_nonce, "own nonce")
    other_nonce_bytes = _nonce(other_nonce, "other nonce")
    scalar = _private_scalar(own_private_key)
    shared = _mul(scalar, parse_public_key(other_public_key))
    # parse_public_key proved the point has prime order and the scalar is in [1, N), so shared is never infinity
    secret = shared[0].to_bytes(32, "big")
    mixed = bytes(a ^ b for a, b in zip(own_nonce_bytes, other_nonce_bytes))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=mixed[:20], info=None).derive(secret)
    return AESGCM(key), mixed[-12:]


def encrypt(plaintext, sender_private_key, sender_nonce, receiver_public_key, receiver_nonce):
    """Encrypt `plaintext` (str or bytes) for the receiver. Keys and nonces are base64. Returns base64 text."""
    cipher, iv = _cipher(sender_private_key, sender_nonce, receiver_public_key, receiver_nonce)
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else bytes(plaintext)
    return _b64encode(cipher.encrypt(iv, data, None))


def decrypt_bytes(encrypted_data, receiver_private_key, receiver_nonce, sender_public_key, sender_nonce):
    cipher, iv = _cipher(receiver_private_key, receiver_nonce, sender_public_key, sender_nonce)
    data = _b64decode(encrypted_data, "encrypted data")
    if len(data) < TAG_BYTES:
        raise DecryptionError("encrypted data is too short")
    try:
        return cipher.decrypt(iv, data, None)
    except InvalidTag:
        raise DecryptionError("could not decrypt: wrong keys or nonces, or the data was changed")


def decrypt(encrypted_data, receiver_private_key, receiver_nonce, sender_public_key, sender_nonce):
    """Decrypt data from the sender. Returns text (UTF-8); use decrypt_bytes for binary data."""
    plain = decrypt_bytes(encrypted_data, receiver_private_key, receiver_nonce, sender_public_key, sender_nonce)
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        raise DecryptionError("decrypted data is not UTF-8 text")
