"""HMAC-SHA256 message authentication with timestamp window and nonce replay cache.

Protects integrity and authenticity. Confidentiality comes from TLS at the transport
layer (see docs/security.md); HMAC stays on even inside TLS (defence in depth).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections import OrderedDict
from typing import Callable

from .codec import ProtocolError
from .keys import KeyRing

WINDOW_SECONDS = 30
NONCE_CACHE_SIZE = 512


def _signing_input(msg: dict) -> bytes:
    body = {k: v for k, v in msg.items() if k != "auth"}
    a = msg["auth"]
    body["auth"] = {"nonce": a["nonce"], "ts": a["ts"]}
    if "kid" in a:
        body["auth"]["kid"] = a["kid"]
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


class Authenticator:
    """secret: raw bytes (single key, kid 1) or a KeyRing. device_id, when set, is stamped on
    everything signed and required on everything verified (binds messages to one robot)."""

    def __init__(self, secret: "bytes | KeyRing", clock: Callable[[], float], device_id: str | None = None):
        self.ring = secret if isinstance(secret, KeyRing) else KeyRing.single(secret)
        self._clock = clock
        self.device_id = device_id
        self._seen: OrderedDict[str, float] = OrderedDict()

    def sign(self, msg: dict) -> dict:
        signed = dict(msg)
        if self.device_id:
            signed["device_id"] = self.device_id
        kid, key = self.ring.signing_key()
        signed["auth"] = {"nonce": secrets.token_hex(12), "ts": int(self._clock()), "kid": kid, "mac": ""}
        signed["auth"]["mac"] = hmac.new(key, _signing_input(signed), hashlib.sha256).hexdigest()
        return signed

    def verify_mac(self, msg: dict) -> int:
        """MAC, key id, device id and timestamp; does not consume the nonce. For pre-checks (a
        connection gate) that must not break the full verify() the real consumer runs later.
        Returns the kid the message was signed under."""
        mid = msg.get("message_id")
        kid = msg["auth"].get("kid", 1)
        key = self.ring.key(kid)
        if key is None:
            raise ProtocolError("auth_failed", "unknown key id", mid)
        expected = hmac.new(key, _signing_input(msg), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, msg["auth"]["mac"]):
            raise ProtocolError("auth_failed", "bad MAC", mid)
        if self.device_id and msg.get("device_id") != self.device_id:
            raise ProtocolError("auth_failed", "wrong device id", mid)
        if abs(self._clock() - msg["auth"]["ts"]) > WINDOW_SECONDS:
            raise ProtocolError("auth_failed", "timestamp outside window", mid)
        return kid

    def verify(self, msg: dict) -> int:
        mid = msg.get("message_id")
        kid = self.verify_mac(msg)
        now = self._clock()
        nonce = msg["auth"]["nonce"]
        for n in [n for n, t in self._seen.items() if now - t > 2 * WINDOW_SECONDS]:
            del self._seen[n]
        if nonce in self._seen:
            raise ProtocolError("replay", "nonce already used", mid)
        self._seen[nonce] = now
        while len(self._seen) > NONCE_CACHE_SIZE:
            self._seen.popitem(last=False)
        if kid >= self.ring.sign_kid:  # peer provably holds this key: adopt it, retire older ones
            self.ring.adopt(kid)
        return kid
