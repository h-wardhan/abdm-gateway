import base64
from datetime import datetime, timedelta, timezone

import pytest

from src.app.abdm import fidelius as f

# --- ABDM's published test vector (https://github.com/mgrmtech/fidelius-cli, "Wormtail" example) -----------------
PLAINTEXT = "Wormtail should never have been Potter cottage's secret keeper."
REQUESTER_PRIVATE = "DMxHPri8d7IT23KgLk281zZenMfVHSdeamq0RhwlIBk="
REQUESTER_PUBLIC = "BAheD5rUqTy4V5xR4/6HWmYpopu5CO+KO8BECS0udNqUTSNo91TIqIIy1A4Vh+F94c+n9vAcwXU2bGcfsI5f69Y="
REQUESTER_NONCE = "6uj1RdDUbcpI3lVMZvijkMC8Te20O4Bcyz0SyivX8Eg="
SENDER_PRIVATE = "AYhVZpbVeX4KS5Qm/W0+9Ye2q3rnVVGmqRICmseWni4="
SENDER_PUBLIC = "BABVt+mpRLMXiQpIfEq6bj8hlXsdtXIxLsspmMgLNI1SR5mHgDVbjHO2A+U4QlMddGzqyEidzm1AkhtSxSO2Ahg="
SENDER_NONCE = "lmXgblZwotx+DfBgKJF0lZXtAXgBEYr5khh79Zytr2Y="
ENCRYPTED = ("pzMvVZNNVtJzqPkkxcCbBUWgDEBy/mBXIeT2dJWI16ZAQnnXUb9lI+S4k8XK6mgZSKKSRIHkcNvJpllnBg548wUgavBa0vCRRwdL6kY6Yw==")
# first / last 110 characters of the requester's X.509 key as printed by the reference tool (412 characters in all)
X509_HEAD = "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB/////////////////////////////////////////7TBEBCAqqqqqqqqqqqqqqq"
X509_TAIL = "L3nNZYEmMaXPXT7QIBCANCAAQIXg+a1Kk8uFecUeP+h1pmKaKbuQjvijvARAktLnTalE0jaPdUyKiCMtQOFYfhfeHPp/bwHMF1NmxnH7COX+vW"


def b64(data):
    return base64.b64encode(data).decode()


def encrypt_vector(public=REQUESTER_PUBLIC):
    return f.encrypt(PLAINTEXT, SENDER_PRIVATE, SENDER_NONCE, public, REQUESTER_NONCE)


# ---- the curve ------------------------------------------------------------------------------------------------------

def test_curve_matches_the_published_bouncycastle_parameters():
    assert f.P == 2**255 - 19
    assert f"{f.A:X}" == "2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA984914A144"
    assert f"{f.B:X}" == "7B425ED097B425ED097B425ED097B425ED097B425ED097B4260B5E9C7710C864"
    assert f"{f.GX:X}" == "2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD245A"
    assert f"{f.GY:X}" == "20AE19A1B8A086B4E01EDD2C7748D14C923D4D7E6D7C61B229E9C5A27ECED3D9"
    assert f"{f.N:X}" == "1000000000000000000000000000000014DEF9DEA2F79CD65812631A5CF5D3ED"
    assert f.H == 8


def test_base_point_is_on_the_curve_and_has_the_published_order():
    assert f._on_curve(f.G)
    assert f._mul(f.N, f.G) is None
    assert f._mul(f.N - 1, f.G) is not None


def test_point_addition_edge_cases():
    double = f._add(f.G, f.G)
    assert double == f._mul(2, f.G) and f._on_curve(double)
    assert f._add(None, f.G) == f.G and f._add(f.G, None) == f.G
    negative = (f.GX, (-f.GY) % f.P)
    assert f._add(f.G, negative) is None
    triple = f._add(double, f.G)
    assert triple == f._mul(3, f.G) and f._on_curve(triple)


# ---- ABDM's reference vector: this is what proves compatibility ---------------------------------------------------

def test_public_keys_are_derived_exactly_like_the_reference_tool():
    assert f.public_key_from_private(REQUESTER_PRIVATE) == REQUESTER_PUBLIC
    assert f.public_key_from_private(SENDER_PRIVATE) == SENDER_PUBLIC


def test_encrypting_gives_the_reference_tools_output():
    assert encrypt_vector() == ENCRYPTED


def test_decrypting_the_reference_tools_output():
    assert f.decrypt(ENCRYPTED, REQUESTER_PRIVATE, REQUESTER_NONCE, SENDER_PUBLIC, SENDER_NONCE) == PLAINTEXT


def test_x509_public_keys_give_the_same_result_as_raw_ones():
    requester_x509 = b64(f.encode_x509(f.parse_public_key(REQUESTER_PUBLIC)))
    sender_x509 = b64(f.encode_x509(f.parse_public_key(SENDER_PUBLIC)))
    assert encrypt_vector(requester_x509) == ENCRYPTED
    assert f.decrypt(ENCRYPTED, REQUESTER_PRIVATE, REQUESTER_NONCE, sender_x509, SENDER_NONCE) == PLAINTEXT


def test_x509_encoding_matches_the_reference_tool():
    x509 = b64(f.encode_x509(f.parse_public_key(REQUESTER_PUBLIC)))
    assert len(x509) == 412
    assert x509[:110] == X509_HEAD and x509[-110:] == X509_TAIL


# ---- public key parsing ---------------------------------------------------------------------------------------------

def test_parse_public_key_returns_the_point():
    point = f.parse_public_key(REQUESTER_PUBLIC)
    assert f._on_curve(point) and f.encode_point(point) == base64.b64decode(REQUESTER_PUBLIC)


def _raw(point=None, prefix=4):
    x, y = point or f.parse_public_key(REQUESTER_PUBLIC)
    return bytes([prefix]) + x.to_bytes(32, "big") + y.to_bytes(32, "big")


@pytest.mark.parametrize("value", ["", "!!!not base64!!!", "AAAA", b64(b"\x04" * 64), b64(b"\x04" * 66)])
def test_public_key_wrong_shape(value):
    with pytest.raises(f.FideliusError):
        f.parse_public_key(value)


def test_public_key_with_another_x509_header_is_rejected():
    x509 = bytearray(f.encode_x509(f.parse_public_key(REQUESTER_PUBLIC)))
    x509[20] ^= 0xFF  # inside the curve parameters
    with pytest.raises(f.FideliusError, match="neither"):
        f.parse_public_key(b64(bytes(x509)))


def test_public_key_must_be_uncompressed():
    with pytest.raises(f.FideliusError, match="uncompressed"):
        f.parse_public_key(b64(_raw(prefix=5)))


def test_public_key_off_the_curve():
    x, y = f.parse_public_key(REQUESTER_PUBLIC)
    with pytest.raises(f.FideliusError, match="not a point on Curve25519"):
        f.parse_public_key(b64(_raw((x, (y + 1) % f.P))))


def test_public_key_coordinates_must_be_below_the_prime():
    with pytest.raises(f.FideliusError, match="not a point on Curve25519"):
        f.parse_public_key(b64(_raw((f.GX + f.P, f.GY))))  # same point modulo P, but not a canonical encoding


def _point_outside_the_prime_order_subgroup():
    """The curve has cofactor 8: most of its points are not in the subgroup ABDM uses."""
    sqrt_minus_one = pow(2, (f.P - 1) // 4, f.P)
    x = 2
    while True:
        value = (x**3 + f.A * x + f.B) % f.P
        y = pow(value, (f.P + 3) // 8, f.P)
        if y * y % f.P != value:
            y = y * sqrt_minus_one % f.P
        if y * y % f.P == value and f._mul(f.N, (x, y)) is not None:
            return x, y
        x += 1


def test_small_subgroup_public_key_is_rejected():
    point = _point_outside_the_prime_order_subgroup()
    assert f._on_curve(point)
    with pytest.raises(f.FideliusError, match="prime order subgroup"):
        f.parse_public_key(b64(_raw(point)))
    with pytest.raises(f.FideliusError, match="prime order subgroup"):
        encrypt_vector(b64(_raw(point)))


# ---- key material ---------------------------------------------------------------------------------------------------

def test_generated_key_material_is_consistent():
    material = f.generate_key_material()
    assert (len(material.private_key), len(material.public_key), len(material.nonce)) == (32, 65, 32)
    assert 1 <= int.from_bytes(material.private_key, "big") < f.N
    assert material.public_key_b64 == f.public_key_from_private(material.private_key_b64)
    assert f.parse_public_key(material.public_key_b64) == f.parse_public_key(material.x509_public_key_b64)
    assert f.parse_public_key(material.public_key_b64) is not None
    assert base64.b64decode(material.nonce_b64) == material.nonce
    assert base64.b64decode(material.private_key_b64) == material.private_key


def test_generated_key_material_is_different_every_time():
    first, second = f.generate_key_material(), f.generate_key_material()
    assert first.private_key != second.private_key and first.nonce != second.nonce


def test_generation_uses_the_given_random_source():
    class Fixed:
        def randbelow(self, n):
            assert n == f.N - 1
            return 41  # private key 42

        def token_bytes(self, n):
            return bytes(range(n))

    material = f.generate_key_material(Fixed())
    assert int.from_bytes(material.private_key, "big") == 42
    assert material.public_key == f.encode_point(f._mul(42, f.G))
    assert material.nonce == bytes(range(32))


# ---- round trips ----------------------------------------------------------------------------------------------------

def _pair():
    return f.generate_key_material(), f.generate_key_material()


def test_round_trip_in_both_directions():
    sender, receiver = _pair()
    encrypted = f.encrypt("hello", sender.private_key_b64, sender.nonce_b64, receiver.x509_public_key_b64, receiver.nonce_b64)
    assert f.decrypt(encrypted, receiver.private_key_b64, receiver.nonce_b64, sender.x509_public_key_b64, sender.nonce_b64) == "hello"
    # the shared secret is symmetric, so the roles can also be swapped
    assert f.decrypt(encrypted, sender.private_key_b64, sender.nonce_b64, receiver.public_key_b64, receiver.nonce_b64) == "hello"


@pytest.mark.parametrize("text", ["", "x", "नमस्ते, मरीज़ का नाम: रवि", '{"resourceType": "Bundle", "entry": []}', "emoji 🩺", pytest.param("a" * 100_000, id="long")])
def test_round_trip_texts(text):
    sender, receiver = _pair()
    encrypted = f.encrypt(text, sender.private_key_b64, sender.nonce_b64, receiver.public_key_b64, receiver.nonce_b64)
    assert f.decrypt(encrypted, receiver.private_key_b64, receiver.nonce_b64, sender.public_key_b64, sender.nonce_b64) == text


def test_round_trip_a_five_megabyte_payload():
    sender, receiver = _pair()
    payload = ("record-" * 1000).encode() * 750  # ~5 MB
    assert len(payload) > 5_000_000
    encrypted = f.encrypt(payload, sender.private_key_b64, sender.nonce_b64, receiver.public_key_b64, receiver.nonce_b64)
    decrypted = f.decrypt_bytes(encrypted, receiver.private_key_b64, receiver.nonce_b64, sender.public_key_b64, sender.nonce_b64)
    assert decrypted == payload


def test_binary_data_round_trips_as_bytes_and_is_not_text():
    sender, receiver = _pair()
    encrypted = f.encrypt(b"\xff\xfe\x00\x01", sender.private_key_b64, sender.nonce_b64, receiver.public_key_b64, receiver.nonce_b64)
    args = (encrypted, receiver.private_key_b64, receiver.nonce_b64, sender.public_key_b64, sender.nonce_b64)
    assert f.decrypt_bytes(*args) == b"\xff\xfe\x00\x01"
    with pytest.raises(f.DecryptionError, match="not UTF-8"):
        f.decrypt(*args)


def test_the_same_message_encrypts_differently_with_different_nonces():
    sender, receiver = _pair()
    first = f.encrypt("same", sender.private_key_b64, sender.nonce_b64, receiver.public_key_b64, receiver.nonce_b64)
    other = f.generate_key_material()
    second = f.encrypt("same", sender.private_key_b64, other.nonce_b64, receiver.public_key_b64, receiver.nonce_b64)
    assert first != second


# ---- wrong keys, tampering ------------------------------------------------------------------------------------------

def _vector_decrypt(encrypted=ENCRYPTED, private=REQUESTER_PRIVATE, nonce=REQUESTER_NONCE, public=SENDER_PUBLIC, other_nonce=SENDER_NONCE):
    return f.decrypt(encrypted, private, nonce, public, other_nonce)


def test_wrong_private_key():
    with pytest.raises(f.DecryptionError, match="wrong keys or nonces"):
        _vector_decrypt(private=f.generate_key_material().private_key_b64)


def test_wrong_sender_public_key():
    with pytest.raises(f.DecryptionError):
        _vector_decrypt(public=f.generate_key_material().public_key_b64)


@pytest.mark.parametrize("which", ["own", "other"])
def test_wrong_nonce(which):
    wrong = f.generate_key_material().nonce_b64
    with pytest.raises(f.DecryptionError):
        _vector_decrypt(**({"nonce": wrong} if which == "own" else {"other_nonce": wrong}))


def test_tampered_ciphertext_is_detected():
    data = bytearray(base64.b64decode(ENCRYPTED))
    data[3] ^= 0x01
    with pytest.raises(f.DecryptionError):
        _vector_decrypt(encrypted=b64(bytes(data)))


def test_tampered_tag_is_detected():
    data = bytearray(base64.b64decode(ENCRYPTED))
    data[-1] ^= 0x80
    with pytest.raises(f.DecryptionError):
        _vector_decrypt(encrypted=b64(bytes(data)))


def test_truncated_data():
    with pytest.raises(f.DecryptionError, match="too short"):
        _vector_decrypt(encrypted=b64(b"\x00" * 15))
    with pytest.raises(f.DecryptionError):
        _vector_decrypt(encrypted=b64(base64.b64decode(ENCRYPTED)[:-5]))


def test_encrypted_data_must_be_base64():
    with pytest.raises(f.FideliusError, match="encrypted data is not valid base64"):
        _vector_decrypt(encrypted="***")


# ---- input validation -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "***", b64(b"\x01" * 31), b64(b"\x01" * 33), b64(b"\x00" * 32),
                                   b64(f.N.to_bytes(32, "big")), b64(b"\xff" * 32)])
def test_bad_private_keys(value):
    with pytest.raises(f.FideliusError, match="private key"):
        f.public_key_from_private(value)


@pytest.mark.parametrize("value", ["", "***", b64(b"\x01" * 31), b64(b"\x01" * 33)])
def test_bad_nonces(value):
    with pytest.raises(f.FideliusError, match="nonce"):
        f.encrypt("x", SENDER_PRIVATE, value, REQUESTER_PUBLIC, REQUESTER_NONCE)
    with pytest.raises(f.FideliusError, match="nonce"):
        f.encrypt("x", SENDER_PRIVATE, SENDER_NONCE, REQUESTER_PUBLIC, value)


def test_largest_valid_private_key_is_accepted():
    assert f.public_key_from_private(b64((f.N - 1).to_bytes(32, "big"))) == b64(f.encode_point(f._mul(f.N - 1, f.G)))
    assert f.public_key_from_private(b64((1).to_bytes(32, "big"))) == b64(f.encode_point(f.G))


# ---- the keyMaterial block ABDM exchanges ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def test_key_material_block_shape():
    material = f.generate_key_material()
    block = f.key_material_block(material, now=NOW)
    assert block == {
        "cryptoAlg": "ECDH",
        "curve": "Curve25519",
        "dhPublicKey": {
            "expiry": "2026-10-23T12:00:00.000Z",
            "parameters": "Curve25519/32byte random key",
            "keyValue": material.x509_public_key_b64,
        },
        "nonce": material.nonce_b64,
    }


def test_key_material_block_expiry_can_be_chosen_and_defaults_to_now():
    block = f.key_material_block(f.generate_key_material(), expires_in=timedelta(minutes=5), now=NOW)
    assert block["dhPublicKey"]["expiry"] == "2026-10-22T12:05:00.000Z"
    live = f.key_material_block(f.generate_key_material())
    assert f.read_key_material_block(live)  # valid for the next 24 hours


def test_read_key_material_block_round_trip():
    material = f.generate_key_material()
    public_key, nonce = f.read_key_material_block(f.key_material_block(material, now=NOW), now=NOW)
    assert (public_key, nonce) == (material.x509_public_key_b64, material.nonce_b64)


def test_read_key_material_block_rejects_an_expired_block():
    block = f.key_material_block(f.generate_key_material(), now=NOW)
    with pytest.raises(f.FideliusError, match="expired"):
        f.read_key_material_block(block, now=NOW + timedelta(days=2))
    block["dhPublicKey"]["expiry"] = "2026-10-23T12:00:00.000Z"
    with pytest.raises(f.FideliusError, match="expired"):
        f.read_key_material_block(block, now=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))  # expiry == now


def test_read_key_material_block_accepts_an_expiry_without_a_zone():
    block = f.key_material_block(f.generate_key_material(), now=NOW)
    block["dhPublicKey"]["expiry"] = "2026-10-23T12:00:00"
    assert f.read_key_material_block(block, now=NOW)


@pytest.mark.parametrize("mutate", [
    lambda b: b.pop("nonce"),
    lambda b: b["dhPublicKey"].pop("keyValue"),
    lambda b: b["dhPublicKey"].pop("expiry"),
    lambda b: b.pop("dhPublicKey"),
])
def test_read_key_material_block_missing_fields(mutate):
    block = f.key_material_block(f.generate_key_material(), now=NOW)
    mutate(block)
    with pytest.raises(f.FideliusError, match="keyMaterial needs"):
        f.read_key_material_block(block, now=NOW)


@pytest.mark.parametrize("block", [None, "text", 5, []])
def test_read_key_material_block_not_an_object(block):
    with pytest.raises(f.FideliusError, match="keyMaterial needs"):
        f.read_key_material_block(block, now=NOW)


def test_read_key_material_block_bad_values():
    block = f.key_material_block(f.generate_key_material(), now=NOW)
    block["dhPublicKey"]["expiry"] = "tomorrow"
    with pytest.raises(f.FideliusError, match="not a date"):
        f.read_key_material_block(block, now=NOW)

    block = f.key_material_block(f.generate_key_material(), now=NOW)
    block["dhPublicKey"]["keyValue"] = "AAAA"
    with pytest.raises(f.FideliusError, match="public key"):
        f.read_key_material_block(block, now=NOW)

    block = f.key_material_block(f.generate_key_material(), now=NOW)
    block["nonce"] = "AAAA"
    with pytest.raises(f.FideliusError, match="nonce"):
        f.read_key_material_block(block, now=NOW)
