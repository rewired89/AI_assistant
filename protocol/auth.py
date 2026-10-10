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

WINDOW_SECONDS = 30
NONCE_CACHE_SIZE = 512


def _signing_input(msg: dict) -> bytes:
    body = {k: v for k, v in msg.items() if k != "auth"}
    a = msg["auth"]
    body["auth"] = {"nonce": a["nonce"], "ts": a["ts"]}
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


class Authenticator:
    def __init__(self, secret: bytes, clock: Callable[[], float]):
        if len(secret) < 16:
            raise ValueError("shared secret must be at least 16 bytes")
        self._secret = secret
        self._clock = clock
        self._seen: OrderedDict[str, float] = OrderedDict()

    def sign(self, msg: dict) -> dict:
        signed = dict(msg)
        signed["auth"] = {"nonce": secrets.token_hex(12), "ts": int(self._clock()), "mac": ""}
        mac = hmac.new(self._secret, _signing_input(signed), hashlib.sha256).hexdigest()
        signed["auth"]["mac"] = mac
        return signed

    def verify(self, msg: dict) -> None:
        mid = msg.get("message_id")
        expected = hmac.new(self._secret, _signing_input(msg), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, msg["auth"]["mac"]):
            raise ProtocolError("auth_failed", "bad MAC", mid)
        now = self._clock()
        if abs(now - msg["auth"]["ts"]) > WINDOW_SECONDS:
            raise ProtocolError("auth_failed", "timestamp outside window", mid)
        nonce = msg["auth"]["nonce"]
        for n in [n for n, t in self._seen.items() if now - t > 2 * WINDOW_SECONDS]:
            del self._seen[n]
        if nonce in self._seen:
            raise ProtocolError("replay", "nonce already used", mid)
        self._seen[nonce] = now
        while len(self._seen) > NONCE_CACHE_SIZE:
            self._seen.popitem(last=False)
