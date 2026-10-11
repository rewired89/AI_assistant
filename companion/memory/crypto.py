"""Field-level encryption for the memory files (AES-256-GCM, key from a passphrase via scrypt).

Encrypted: key, value, tags, source_context of every memory. Each ciphertext is bound (as AAD) to the user, the row
id and the field name, so a value cannot be moved to another row or field without detection.
NOT encrypted: category, status, source, confidence, timestamps, expiry, row count, settings. Someone with the file
can see how many memories exist, in which category and when they changed, but not what they say.
A keyed lookup index (HMAC) lets the store find a memory by key without decrypting everything; it reveals only whether
two rows share a key, not the key.
This protects a stolen disk, backup or copied file. It does NOT protect against someone using the running host,
and if the passphrase sits next to the file (e.g. in the same folder) it protects nothing."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PREFIX = "e1:"
CHECK_TEXT = "nine-tails-memory-check"
MIN_PASSPHRASE = 8


class CryptoError(Exception):
    pass


class FieldCipher:
    def __init__(self, passphrase: str, salt: bytes, n: int = 2 ** 15):
        if len(passphrase) < MIN_PASSPHRASE:
            raise CryptoError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
        master = Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode())
        self._aes = AESGCM(HKDF(hashes.SHA256(), 32, salt, b"nt-mem-enc").derive(master))
        self._idx = HKDF(hashes.SHA256(), 32, salt, b"nt-mem-idx").derive(master)

    @staticmethod
    def new_salt() -> bytes:
        return os.urandom(16)

    def seal(self, text: str | None, aad: str) -> str | None:
        if text is None:
            return None
        nonce = os.urandom(12)
        return PREFIX + base64.b64encode(nonce + self._aes.encrypt(nonce, text.encode(), aad.encode())).decode()

    def open(self, blob: str | None, aad: str) -> str | None:
        if blob is None:
            return None
        if not blob.startswith(PREFIX):
            raise CryptoError("field is not encrypted")
        try:
            raw = base64.b64decode(blob[len(PREFIX):], validate=True)
            return self._aes.decrypt(raw[:12], raw[12:], aad.encode()).decode()
        except (InvalidTag, ValueError):
            raise CryptoError("memory data failed its integrity check (wrong passphrase or tampered file)") from None

    def index(self, category: str, key: str) -> str:
        return hmac.new(self._idx, f"{category}|{key}".encode(), hashlib.sha256).hexdigest()
