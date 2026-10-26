"""
AES-256-GCM Encryption Utility
--------------------------------
Provides encrypt and decrypt helpers for patient payload data.

Usage:
  from src.utils.encryption import EncryptionUtil

  # Encrypt a dict -> returns Base64 string
  encrypted = EncryptionUtil.encrypt_dict({"first_name": "John", ...})

  # Decrypt a Base64 string -> returns original dict
  original  = EncryptionUtil.decrypt_dict(encrypted)

Environment Variable Required:
  ENCRYPTION_KEY  -> 32-byte key encoded as URL-safe Base64
  Generate one-time with:
      python -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
"""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionUtil:
    # Nonce size for AES-GCM is always 12 bytes (96 bits) — do NOT change
    _NONCE_SIZE = 12

    @classmethod
    def _get_key(cls) -> bytes:
        """
        Load the AES-256 key from the environment.
        The key must be a URL-safe Base64-encoded 32-byte value.
        """
        raw = os.environ.get("ENCRYPTION_KEY", "").strip()
        if not raw:
            raise EnvironmentError(
                "ENCRYPTION_KEY is not set. "
                "Generate one with: python -c \"import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
            )
        key = base64.urlsafe_b64decode(raw + "==")  # padding-safe decode
        if len(key) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256).")
        return key

    @classmethod
    def encrypt_dict(cls, payload: dict) -> str:
        """
        Encrypt a Python dict using AES-256-GCM.

        Steps:
          1. Serialize dict  → JSON bytes
          2. Generate a fresh 12-byte random Nonce  (never reuse!)
          3. Encrypt with AESGCM  → ciphertext (includes GCM auth-tag)
          4. Prepend Nonce to ciphertext  → nonce + ciphertext
          5. Base64-encode the combined bytes → returned string

        Returns:
          URL-safe Base64 string  (safe to store in any TEXT/VARCHAR column)
        """
        key = cls._get_key()
        aesgcm = AESGCM(key)

        nonce = os.urandom(cls._NONCE_SIZE)
        plaintext = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

        ciphertext = aesgcm.encrypt(nonce, plaintext, None)  # None = no AAD

        combined = nonce + ciphertext
        return base64.urlsafe_b64encode(combined).decode("utf-8")

    @classmethod
    def decrypt_dict(cls, encrypted_b64: str) -> dict:
        """
        Decrypt a Base64 string produced by encrypt_dict().

        Steps:
          1. Base64-decode → raw bytes
          2. Split first 12 bytes as Nonce, rest as ciphertext
          3. Decrypt with AESGCM (raises InvalidTag if tampered)
          4. Decode JSON bytes → return Python dict

        NOTE: This method is intentionally NOT called anywhere in the API routes.
              Use it only when you explicitly need to read the original plaintext data.

        Returns:
          Original Python dict
        """
        key = cls._get_key()
        aesgcm = AESGCM(key)

        combined = base64.urlsafe_b64decode(encrypted_b64 + "==")

        nonce = combined[: cls._NONCE_SIZE]
        ciphertext = combined[cls._NONCE_SIZE :]

        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode("utf-8"))

    @classmethod
    def encrypt_value(cls, value) -> str:
        """
        Encrypt a single scalar value (str, int, bool, None) using AES-256-GCM.

        The value is wrapped in a JSON envelope {"v": value} before encryption
        so any Python type can be safely round-tripped.

        Returns:
          URL-safe Base64 string safe to store in any TEXT column.
          Returns None if value is None.
        """
        if value is None:
            return None
        return cls.encrypt_dict({"v": value})

    @classmethod
    def decrypt_value(cls, encrypted_b64: str):
        """
        Decrypt a single scalar value produced by encrypt_value().

        Returns:
          Original Python value (str / int / bool / None).
          Returns None if encrypted_b64 is None or empty.

        NOTE: This method is intentionally NOT called anywhere in the API routes.
              Use it only when you explicitly need to read the original plaintext value.
        """
        if not encrypted_b64:
            return None
        return cls.decrypt_dict(encrypted_b64).get("v")
