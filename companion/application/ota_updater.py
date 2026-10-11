"""Host side of a firmware update: check, stream with flow control, wait for the device's verdicts, apply, confirm.

Only the owner starts this (CLI or admin code). It is deliberately not reachable from conversation: no LLM tool,
no voice command, no automatic download. The host pushes a package that was signed offline; the device decides
whether to accept it (signature, product, rollback floor, battery, power state) and falls back on its own if the
new image does not prove itself."""
from __future__ import annotations

import uuid
from collections import deque
from typing import Callable

from protocol import audio as aud
from protocol.ota import CHUNK

from ..security.ota_signing import Package, check_package
from .device_client import CommandRejected, CommandTimeout, DeviceClient

WINDOW_CHUNKS = 16     # 64 KiB in flight at most; the device acknowledges every 4 chunks


class OtaError(Exception):
    def __init__(self, reason: str, message: str = ""):
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason


class OtaUpdater:
    def __init__(self, client: DeviceClient, trusted_keys: dict[int, bytes], window_chunks: int = WINDOW_CHUNKS):
        self.client, self.trusted, self.window = client, trusted_keys, window_chunks
        self.events: deque = deque(maxlen=200)
        client.add_listener(self._on_event)

    def _on_event(self, msg: dict) -> None:
        if msg["type"].startswith("ota.") or msg["type"] == "device.ready":
            self.events.append(msg)

    def _take(self, *types: str, update_id: str | None = None) -> dict | None:
        for i, m in enumerate(self.events):
            if m["type"] in types and (update_id is None or m["payload"].get("update_id", update_id) == update_id):
                del self.events[i]
                return m
        return None

    def _wait(self, types: tuple[str, ...], update_id: str | None, timeout_s: float, fail_types=("ota.failed", "ota.rolled_back")) -> dict:
        end = self.client.clock.now() + timeout_s
        while True:
            self.client.pump()
            bad = self._take(*fail_types, update_id=update_id)
            if bad:
                raise OtaError(bad["payload"].get("reason", bad["type"]), bad["type"])
            m = self._take(*types, update_id=update_id)
            if m:
                return m
            if self.client.clock.now() >= end:
                raise OtaError("timeout", "/".join(types))
            self.client.clock.sleep(0.01)

    # ---- push ----------------------------------------------------------------------------------------
    def push(self, pkg: Package, update_id: str | None = None, timeout_s: float | None = None,
             on_progress: Callable[[int, int], None] | None = None) -> str:
        """Stages and verifies the image on the device. Leaves it waiting for apply(). Resumes a partial transfer."""
        try:
            check_package(pkg, self.trusted)                                   # never push what we would not trust ourselves
        except Exception as e:
            raise OtaError("bad_package", str(e)) from None
        c, m = self.client, pkg.manifest
        uid = update_id or "u" + uuid.uuid4().hex[:10]
        stream_no = c._alloc_stream()
        c._active_playback.add(stream_no)
        try:
            try:
                out = c.request("ota.begin", {"update_id": uid, "stream_no": stream_no, "manifest": m, "signature": pkg.signature},
                                timeout_s=10)
            except CommandRejected as e:
                raise OtaError("rejected", f"{e.code}: {e.message}") from None
            state, offset = out.result["state"], out.result["resume_from"]
            if state == "verified":
                return uid
            size, seq = len(pkg.image), offset // CHUNK
            acked, end_sent = offset, False
            deadline = c.clock.now() + (timeout_s if timeout_s is not None else 60 + size / 2048)
            while not end_sent:
                c.pump()
                while True:
                    ev = self._take("ota.progress", "ota.failed", "ota.verified", update_id=uid)
                    if ev is None:
                        break
                    if ev["type"] == "ota.failed":
                        raise OtaError(ev["payload"]["reason"], "device aborted the update")
                    if ev["type"] == "ota.verified":
                        return uid
                    acked = max(acked, ev["payload"]["received"])
                    if on_progress:
                        on_progress(acked, size)
                sent_any = False
                while not end_sent and offset - acked < self.window * CHUNK:
                    chunk = pkg.image[offset:offset + CHUNK]
                    end_sent = len(chunk) < CHUNK          # short or empty chunk carries END
                    c.transport.send_binary(aud.pack(aud.KIND_OTA, stream_no, seq, chunk, end=end_sent, codec=aud.CODEC_RAW))
                    offset, seq, sent_any = offset + len(chunk), seq + 1, True
                if c.clock.now() > deadline:
                    raise OtaError("timeout", "transfer")
                if not sent_any:
                    c.clock.sleep(0.005)
            self._wait(("ota.verified",), uid, 30)
            return uid
        except CommandTimeout:
            raise OtaError("timeout", "device did not answer") from None
        finally:
            c._active_playback.discard(stream_no)

    # ---- apply / confirm -------------------------------------------------------------------------------
    def apply(self, update_id: str, expect_build: int, timeout_s: float = 60) -> dict:
        """Tells the device to switch images and waits for it to come back running the new build.
        Raises OtaError if it comes back on the old one (it rolled itself back)."""
        try:
            self.client.request("ota.apply", {"update_id": update_id}, timeout_s=10)
        except CommandRejected as e:
            raise OtaError("rejected", f"{e.code}: {e.message}") from None
        ready = self._wait(("device.ready",), None, timeout_s, fail_types=("ota.rolled_back",))
        if ready["payload"].get("build") != expect_build:
            raise OtaError("wrong_build", f"device reports build {ready['payload'].get('build')}")
        return ready["payload"]

    def confirm(self, update_id: str, expect_build: int, timeout_s: float = 30) -> dict:
        """Host-side health check, then the commit. Until this succeeds the device may still roll back by itself."""
        status = self.client.request("device.get_status", timeout_s=10).result
        if status.get("build") != expect_build or status.get("ota", {}).get("state") != "trial":
            raise OtaError("unhealthy", f"unexpected device state: build={status.get('build')} ota={status.get('ota')}")
        try:
            self.client.request("ota.confirm", {"update_id": update_id}, timeout_s=timeout_s)
        except CommandRejected as e:
            raise OtaError("rejected", f"{e.code}: {e.message}") from None
        return self._wait(("ota.committed",), update_id, timeout_s)["payload"]

    def abort(self, update_id: str) -> None:
        self.client.request("ota.abort", {"update_id": update_id}, timeout_s=10)


def safe_to_apply(pipeline) -> bool:
    """Do not restart her robot mid-sentence: only apply while the voice pipeline is idle and nothing is queued."""
    from ..speech.pipeline import State
    return pipeline.state is State.IDLE and not pipeline.client.captures and not pipeline.client._active_playback
