"""Firmware update format shared by host and device: canonical manifest bytes and signature verification.

Trust model: updates are signed offline with an ECDSA P-256 / SHA-256 key that only the owner holds; the device
carries the matching public key(s). TLS and the per-device HMAC protect the *channel*; the signature protects the
*image*, so a stolen host session, a stolen HMAC key or a malicious network peer still cannot install firmware.
(P-256 chosen because mbedTLS supports it. Whether ESP32-S3 secure boot v2 can verify it in ROM, and how the public
key is burned or stored, is UNVERIFIED and must be checked against the real board and ESP-IDF version.)"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

CHUNK = 4096                       # every firmware frame except the last carries exactly this many bytes
MANIFEST_FIELDS = ("product", "board", "version", "build", "size", "sha256", "created", "sign_kid")


class OtaFormatError(ValueError):
    pass


def canonical(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_signature(manifest: dict, signature_b64: str, public_key_der: bytes) -> bool:
    try:
        pub = serialization.load_der_public_key(public_key_der)
        if not isinstance(pub, ec.EllipticCurvePublicKey) or pub.curve.name != "secp256r1":
            return False
        pub.verify(base64.b64decode(signature_b64, validate=True), canonical(manifest), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, binascii.Error, TypeError):
        return False
