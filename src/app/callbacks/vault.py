"""Encrypt / decrypt what the gateway keeps at rest (callback bodies, webhook secrets, queued payloads)."""
from src.utils.encryption import EncryptionUtil


def seal_text(text):
    """Text (e.g. a raw JSON body) -> encrypted string."""
    return EncryptionUtil.encrypt_dict({"raw": text})


def open_text(sealed):
    return EncryptionUtil.decrypt_dict(sealed)["raw"]


def seal_secret(secret):
    return EncryptionUtil.encrypt_value(secret)


def open_secret(sealed):
    return EncryptionUtil.decrypt_value(sealed)
