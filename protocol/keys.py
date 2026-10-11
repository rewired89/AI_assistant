"""Key ring: the keys one side accepts (kid -> key) and the kid it signs with.

Rotation rules (same on host and device, see docs/provisioning.md):
- a verified message under kid K > my signing kid makes me adopt K (the peer provably holds it)
- once I sign with K, every kid < K is retired
Both rules are applied by Authenticator.verify(); `on_change` lets the owner persist the ring.
"""
from __future__ import annotations

import secrets
from typing import Callable

MIN_KEY_BYTES = 16
KEY_BYTES = 32


def new_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


class KeyRing:
    def __init__(self, keys: dict[int, bytes], sign_kid: int, on_change: Callable[[], None] | None = None):
        if sign_kid not in keys:
            raise ValueError("signing kid not in ring")
        for kid, k in keys.items():
            self._check(kid, k)
        self._keys, self.sign_kid, self.on_change = dict(keys), sign_kid, on_change

    @staticmethod
    def _check(kid: int, key: bytes) -> None:
        if not isinstance(kid, int) or kid < 1:
            raise ValueError("kid must be a positive int")
        if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
            raise ValueError(f"key must be at least {MIN_KEY_BYTES} bytes")

    @classmethod
    def single(cls, secret: bytes) -> "KeyRing":
        return cls({1: secret}, 1)

    def destroy(self) -> None:
        """Revocation: forget every key so a live Authenticator holding this ring stops accepting immediately."""
        self._keys = {}

    def key(self, kid: int) -> bytes | None:
        return self._keys.get(kid)

    def signing_key(self) -> tuple[int, bytes]:
        return self.sign_kid, self._keys[self.sign_kid]

    def kids(self) -> list[int]:
        return sorted(self._keys)

    def add(self, kid: int, key: bytes) -> None:
        self._check(kid, key)
        if kid in self._keys:
            raise ValueError(f"kid {kid} already present")
        self._keys[kid] = key
        self._changed()

    def use_for_signing(self, kid: int) -> None:
        """Sign with kid but keep older keys accepted until the peer is seen using kid."""
        if kid not in self._keys:
            raise ValueError("unknown kid")
        self.sign_kid = kid
        self._changed()

    def adopt(self, kid: int) -> None:
        """Start signing with kid and retire older keys."""
        if kid not in self._keys:
            raise ValueError("unknown kid")
        changed = False
        if kid > self.sign_kid:
            self.sign_kid, changed = kid, True
        if kid == self.sign_kid:
            for old in [k for k in self._keys if k < kid]:
                del self._keys[old]
                changed = True
        if changed:
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def to_dict(self) -> dict:
        return {"sign_kid": self.sign_kid, "keys": {str(k): v.hex() for k, v in self._keys.items()}}

    @classmethod
    def from_dict(cls, d: dict, on_change: Callable[[], None] | None = None) -> "KeyRing":
        return cls({int(k): bytes.fromhex(v) for k, v in d["keys"].items()}, int(d["sign_kid"]), on_change)

    def __repr__(self) -> str:  # never print key material
        return f"KeyRing(kids={self.kids()}, sign_kid={self.sign_kid})"
