"""Host-side store for per-device HMAC keys, plus the one-time provisioning bundle.

- Each robot gets its own random 256-bit key (a stolen robot cannot impersonate another, and can be revoked).
- File is created 0600 on POSIX and refused on load if group/other can read it. Windows: POSIX modes do not
  apply, so rely on the user-profile ACL or set a passphrase (then the file is encrypted: scrypt + Fernet).
- Key material never appears in repr/logs/status listings. Only create_device() returns it, inside the bundle.
"""
from __future__ import annotations

import base64
import json
import os
import re
import stat
from pathlib import Path
from typing import Callable

from protocol import Authenticator, KeyRing, new_key

DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
BUNDLE_VERSION = 1


class SecretStoreError(Exception):
    pass


class InsecurePermissions(SecretStoreError):
    pass


class SecretStore:
    def __init__(self, path: Path | str, clock: Callable[[], float], passphrase: str | None = None):
        self.path, self.clock, self._pass = Path(path), clock, passphrase
        self._devices: dict[str, dict] = {}
        self._rings: dict[str, KeyRing] = {}
        if self.path.exists():
            self._load()

    # ---- file format ------------------------------------------------------------
    def _fernet(self, salt: bytes):
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        key = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(self._pass.encode())
        return Fernet(base64.urlsafe_b64encode(key))

    def _load(self) -> None:
        if os.name == "posix" and self.path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise InsecurePermissions(f"{self.path} is readable by others; run: chmod 600 {self.path}")
        try:
            blob = json.loads(self.path.read_text())
            if blob["v"] != 1:
                raise ValueError("version")
            if blob["enc"] == "fernet":
                if not self._pass:
                    raise SecretStoreError("secret store is encrypted; passphrase required")
                from cryptography.fernet import InvalidToken
                try:
                    data = json.loads(self._fernet(base64.b64decode(blob["salt"])).decrypt(blob["data"].encode()))
                except InvalidToken:
                    raise SecretStoreError("wrong passphrase or corrupted secret store") from None
            else:
                data = blob["data"]
            self._devices = data["devices"]
        except (KeyError, ValueError, TypeError) as e:
            raise SecretStoreError(f"corrupted secret store: {e}") from None
        for did, rec in self._devices.items():
            if rec["status"] == "active":
                self._rings[did] = KeyRing.from_dict(rec["ring"], on_change=self.save)

    def save(self) -> None:
        for did, ring in self._rings.items():
            self._devices[did]["ring"] = ring.to_dict()
        data = {"devices": self._devices}
        if self._pass:
            salt = os.urandom(16)
            blob = {"v": 1, "enc": "fernet", "salt": base64.b64encode(salt).decode(),
                    "data": self._fernet(salt).encrypt(json.dumps(data).encode()).decode()}
        else:
            blob = {"v": 1, "enc": "none", "data": data}
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(blob, f)
        os.replace(tmp, self.path)

    # ---- devices --------------------------------------------------------------------
    def devices(self) -> list[dict]:
        """Safe to print: no key material."""
        return [{"device_id": d, "status": r["status"], "created_at": r["created_at"],
                 "provisioned_at": r.get("provisioned_at"),
                 "kids": self._rings[d].kids() if d in self._rings else []}
                for d, r in sorted(self._devices.items())]

    def create_device(self, device_id: str, ca_pem: str, url: str) -> dict:
        """Mint a key and return the provisioning bundle. The bundle is the ONLY place the key leaves the store."""
        if not DEVICE_ID.match(device_id):
            raise SecretStoreError("device_id must be 3-32 chars of letters, digits, _ or -")
        if device_id in self._devices and self._devices[device_id]["status"] == "active":
            raise SecretStoreError(f"{device_id} already exists; revoke it first to re-provision")
        key = new_key()
        ring = KeyRing({1: key}, 1, on_change=self.save)
        self._devices[device_id] = {"status": "active", "created_at": self.clock(), "provisioned_at": None,
                                    "ring": ring.to_dict()}
        self._rings[device_id] = ring
        self.save()
        return {"v": BUNDLE_VERSION, "device_id": device_id, "kid": 1, "key": key.hex(), "ca_pem": ca_pem, "url": url}

    def ring(self, device_id: str | None) -> KeyRing | None:
        return self._rings.get(device_id) if device_id else None

    def authenticator(self, device_id: str, clock: Callable[[], float]) -> Authenticator:
        ring = self.ring(device_id)
        if ring is None:
            raise SecretStoreError(f"unknown or revoked device {device_id}")
        return Authenticator(ring, clock, device_id)

    def mark_provisioned(self, device_id: str) -> None:
        rec = self._devices.get(device_id)
        if rec and rec["status"] == "active" and rec.get("provisioned_at") is None:
            rec["provisioned_at"] = self.clock()
            self.save()

    def revoke(self, device_id: str) -> None:
        rec = self._devices.get(device_id)
        if rec is None:
            raise SecretStoreError("unknown device")
        rec["status"], rec["revoked_at"] = "revoked", self.clock()
        rec["ring"] = {"sign_kid": 0, "keys": {}}  # destroy the key material, keep the audit record
        ring = self._rings.pop(device_id, None)
        if ring is not None:
            ring.destroy()                         # live sessions built on this ring stop verifying at once
        self.save()

    def begin_rotation(self, device_id: str) -> tuple[int, bytes]:
        ring = self.ring(device_id)
        if ring is None:
            raise SecretStoreError("unknown or revoked device")
        if len(ring.kids()) != 1:
            raise SecretStoreError("rotation already in progress")
        kid, key = max(ring.kids()) + 1, new_key()
        ring.add(kid, key)  # persisted via on_change BEFORE the key is sent anywhere
        return kid, key

    # ---- gate for the wss transport ---------------------------------------------------
    def mac_gate(self, clock: Callable[[], float], only_device: str | None = None) -> Callable[[str], bool]:
        """First-frame check: known, active device_id and a valid MAC under one of its keys.
        With only_device, every other provisioned robot is refused too (one link serves one robot)."""
        from protocol import ProtocolError, decode

        def check(raw: str) -> bool:
            try:
                msg = decode(raw)
                did = msg.get("device_id")
                if only_device is not None and did != only_device:
                    return False
                ring = self.ring(did)
                if ring is None:
                    return False
                Authenticator(ring, clock, did).verify_mac(msg)
            except ProtocolError:
                return False
            self.mark_provisioned(did)
            return True
        return check
