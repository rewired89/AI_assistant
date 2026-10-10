"""Host-driven key rotation over an authenticated TLS session. See docs/provisioning.md."""
from __future__ import annotations

from ..application.device_client import CommandRejected, DeviceClient
from .secrets import SecretStore


def rotate_device_key(client: DeviceClient, store: SecretStore, device_id: str, timeout_s: float = 5.0) -> int:
    """Mint kid N+1 (or resume a pending one), persist it BEFORE sending, send it signed under the
    old key, and wait. The device's replies are signed under the new key, which makes verify() adopt
    it and retire the old one on both sides. `client.auth` must share the store's ring
    (use store.authenticator()). Safe to call again after a crash or lost reply."""
    ring = store.ring(device_id)
    if ring is None:
        raise ValueError("unknown or revoked device")
    if len(ring.kids()) == 1:
        kid, key = store.begin_rotation(device_id)
    else:  # an earlier attempt left the new key pending: resume with the same key
        kid = max(ring.kids())
        key = ring.key(kid)
    try:
        client.request("security.rotate_key", {"kid": kid, "key": key.hex()}, timeout_s=timeout_s)
    except CommandRejected as e:
        if e.code != "invalid_state":
            raise
        # device may already hold the key (our completion was lost): any reply it signs with kid proves it
        client.request("device.get_status", timeout_s=timeout_s)
    client.pump()
    if ring.kids() != [kid]:
        client.request("device.get_status", timeout_s=timeout_s)  # first message under the new key retires the old
    return kid
