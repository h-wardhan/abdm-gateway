"""
Key generation, encryption and decryption of health records (what i-medicus used VCDoctor's Fidelius endpoints for).

Same keys, same names as before: `GenerateKeys` -> {privateKey, publicKey, nonce}; `Encrypt` -> {encryptedData,
keyToShare}; `Decrypt` -> the plain text. The maths is `app.abdm.fidelius`; this module is the input checking.
"""
from src.app.abdm import fidelius
from src.app.common import validators as v
from src.app.errors import ValidationError


def _field(data, name):
    return str(v.require(v.pick(data, name), name))


def _key_error(exc):
    return ValidationError(f"Invalid key material: {exc}", code="INVALID_KEY_MATERIAL")


def new_keys():
    """A fresh sender key pair and nonce. The public key is the raw uncompressed point (88 base64 characters), as
    VCDoctor / fidelius-cli's `publicKey` and ABDM's own PHR app give it: i-medicus keeps it in varchar(255) columns, an
    X.509 key (about 410 characters) would be cut off there and no longer be valid base64. Both forms are accepted
    everywhere keys come in."""
    material = fidelius.generate_key_material()
    return {"privateKey": material.private_key_b64, "publicKey": material.public_key_b64,
            "nonce": material.nonce_b64}


def encrypt(data):
    """Encrypt `plainTextData` for the HIU. Answer {encryptedData, keyToShare}: keyToShare is the sender's public key
    (what goes in `keyMaterial.dhPublicKey.keyValue` of the push)."""
    plain = data.get("plainTextData")
    if not isinstance(plain, str) or not plain:
        raise ValidationError("plainTextData is required (the record as a JSON string)",
                              details={"field": "plainTextData"})
    private_key, nonce = _field(data, "senderPrivateKey"), _field(data, "senderNonce")
    try:
        sender_public = fidelius.public_key_from_private(private_key)
        sent_public = v.pick(data, "senderPublicKey")
        if sent_public and fidelius.parse_public_key(sent_public) != fidelius.parse_public_key(sender_public):
            raise ValidationError("senderPublicKey does not belong to senderPrivateKey: the HIU could not decrypt",
                                  code="INVALID_KEY_MATERIAL", details={"field": "senderPublicKey"})
        encrypted = fidelius.encrypt(plain, private_key, nonce, _field(data, "receiverPublicKey"),
                                     _field(data, "receiverNonce"))
        key_to_share = fidelius.encode_x509_b64(fidelius.parse_public_key(sender_public))
    except fidelius.FideliusError as exc:
        raise _key_error(exc)
    return {"encryptedData": encrypted, "keyToShare": key_to_share}


def decrypt(data):
    """Decrypt what a HIP pushed to us. Returns the plain text."""
    try:
        return fidelius.decrypt(_field(data, "encryptedData"), _field(data, "receiverPrivateKey"),
                                _field(data, "receiverNonce"), _field(data, "senderPublicKey"),
                                _field(data, "senderNonce"))
    except fidelius.DecryptionError as exc:
        raise ValidationError(str(exc), code="DECRYPTION_FAILED")
    except fidelius.FideliusError as exc:
        raise _key_error(exc)
