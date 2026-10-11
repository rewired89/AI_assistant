"""Device side of firmware updates, as an executable specification for the real firmware.

Model: two image slots (A/B). The running slot is never written. A signed update is staged into the *other* slot,
verified (signature at begin, size and SHA-256 at the end), then applied by switching the boot slot and rebooting
into a trial. The new image must be confirmed by the host within a window; otherwise, or if it fails its self-test
or keeps crashing, the device falls back to the last good slot on its own, without the host. Committing raises an
anti-rollback floor so older (even validly signed) builds cannot be installed afterwards.

Simulation markers (these stand in for real behavior, which only the real firmware on the real board can show):
an image containing b"NT-FAIL-SELFTEST" fails its boot self-test; b"NT-BOOTLOOP" crashes on every boot.
Slot size, flash behavior, power-loss timing and the secure-boot chain are all UNVERIFIED."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from protocol import audio as aud
from protocol.ota import CHUNK, sha256_hex, verify_signature

FACTORY_IMAGE = b"NTFW factory image 0.1.0"
PROGRESS_EVERY = 4   # chunks


@dataclass
class OtaConfig:
    trusted_keys: dict[int, bytes]
    product: str = "nine-tails"
    board: str = "sim-board"                 # UNVERIFIED: the real board id is unknown until the hardware arrives
    slot_size: int = 1_572_864               # UNVERIFIED: depends on the real flash size and partition table
    min_battery_begin: int = 40
    min_battery_apply: int = 30
    confirm_timeout_s: float = 300.0
    max_boot_attempts: int = 2
    slot_dir: Path | None = None
    factory_version: str = "0.1.0"
    factory_build: int = 1


class SlotStore:
    """Two image slots. With a directory they survive 'power loss' (a fresh SimulatedDevice on the same directory)."""

    def __init__(self, directory: Path | None):
        self.dir = Path(directory) if directory else None
        self.mem: dict[str, bytearray] = {"A": bytearray(), "B": bytearray()}
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            for s in "AB":
                f = self.dir / f"{s}.bin"
                if f.exists():
                    self.mem[s] = bytearray(f.read_bytes())

    def _flush(self, slot: str) -> None:
        if self.dir:
            tmp = self.dir / f"{slot}.tmp"
            tmp.write_bytes(bytes(self.mem[slot]))
            os.replace(tmp, self.dir / f"{slot}.bin")

    def read(self, slot: str) -> bytes:
        return bytes(self.mem[slot])

    def erase(self, slot: str) -> None:
        self.mem[slot] = bytearray()
        self._flush(slot)

    def write_at(self, slot: str, offset: int, data: bytes) -> None:
        buf = self.mem[slot]
        if offset > len(buf):
            raise ValueError("gap in image")
        buf[offset:offset + len(data)] = data
        self._flush(slot)


class OtaEngine:
    def __init__(self, dev, cfg: OtaConfig):
        self.dev, self.cfg = dev, cfg
        self.slots = SlotStore(cfg.slot_dir)
        self.reboot_requested = False
        st = dev.state.get("ota")
        if st is None:
            self.slots.erase("A"); self.slots.erase("B")
            self.slots.write_at("A", 0, FACTORY_IMAGE)
            st = {"good": "A", "boot": "A", "min_build": cfg.factory_build, "staging": None, "trial": None,
                  "last_rollback": None, "last_committed": None, "notice": None,
                  "slots": {"A": {"version": cfg.factory_version, "build": cfg.factory_build,
                                  "sha256": sha256_hex(FACTORY_IMAGE), "size": len(FACTORY_IMAGE)}, "B": None}}
            dev.state["ota"] = st
        self.st = st

    # ---- views --------------------------------------------------------------------------
    def running(self) -> dict:
        return self.st["slots"][self.st["boot"]]

    @property
    def other(self) -> str:
        return "B" if self.st["good"] == "A" else "A"

    def state_name(self) -> str:
        if self.st["trial"]:
            return "trial"
        s = self.st["staging"]
        return s["state"] if s else "idle"

    def busy(self) -> bool:
        s = self.st["staging"]
        return bool(s and s["state"] == "receiving")

    def summary(self) -> dict:
        return {"state": self.state_name(), "slot": self.st["boot"], "good_build": self.st["slots"][self.st["good"]]["build"],
                "min_build": self.st["min_build"], "last_rollback": self.st["last_rollback"]}

    def take_reboot(self) -> bool:
        r, self.reboot_requested = self.reboot_requested, False
        return r

    def take_notice(self) -> dict | None:
        n, self.st["notice"] = self.st["notice"], None
        if n:
            self.dev._persist()
        return n

    # ---- begin -------------------------------------------------------------------------------
    def begin(self, p: dict) -> dict:
        from .device import CommandFailure, PowerState as P
        m, st, c, dev = p["manifest"], self.st, self.cfg, self.dev
        der = c.trusted_keys.get(m["sign_kid"])
        if der is None or not verify_signature(m, p["signature"], der):       # before anything else is touched
            raise CommandFailure("not_allowed", "update is not signed by a trusted key")
        if m["product"] != c.product or m["board"] != c.board:
            raise CommandFailure("not_allowed", "update is for a different product or board")
        active = st["slots"][st["good"]]
        if m["build"] <= active["build"] or m["build"] < st["min_build"]:
            raise CommandFailure("not_allowed", "build is not newer than the installed one (rollback protection)")
        if m.get("requires_build", 0) > active["build"]:
            raise CommandFailure("invalid_state", "install the required earlier build first")
        if m["size"] > c.slot_size:
            raise CommandFailure("invalid_payload", "image does not fit in a slot")
        if st["trial"]:
            raise CommandFailure("invalid_state", "previous update is still awaiting confirmation")
        if dev.power not in (P.AWAKE, P.IDLE):
            raise CommandFailure("unsafe", f"cannot update in power state {dev.power.value}")
        if dev.battery < c.min_battery_begin:
            raise CommandFailure("unsafe", f"battery below {c.min_battery_begin}%")
        if dev.capture or dev.playback:
            raise CommandFailure("busy", "audio is active")
        cur = st["staging"]
        if cur and cur["update_id"] == p["update_id"] and cur["manifest"] == m and cur["state"] in ("receiving", "verified"):
            cur["stream_no"] = p["stream_no"]                       # resume after a dropped link or a power cut
            dev._persist()
            return {"resume_from": cur["received"], "chunk_size": CHUNK, "state": cur["state"]}
        if cur:
            self.slots.erase(cur["slot"])
        slot = self.other
        self.slots.erase(slot)
        st["staging"] = {"update_id": p["update_id"], "manifest": m, "signature": p["signature"], "stream_no": p["stream_no"],
                         "slot": slot, "received": 0, "state": "receiving"}
        dev._persist()
        return {"resume_from": 0, "chunk_size": CHUNK, "state": "receiving"}

    # ---- data --------------------------------------------------------------------------------
    def on_frame(self, f) -> None:
        from .device import PowerState as P
        s = self.st["staging"]
        if s is None or s["state"] != "receiving" or s["stream_no"] != f.stream_no:
            self.dev.audio_rejected += 1
            return
        if self.dev.power not in (P.AWAKE, P.IDLE, P.LOW_BATTERY):
            return self.fail("power_state")
        n = len(f.payload)
        if f.seq != s["received"] // CHUNK or (not f.end and n != CHUNK) or n > CHUNK:
            return self.fail("bad_chunk")
        if s["received"] + n > s["manifest"]["size"]:
            return self.fail("size_mismatch")
        try:
            self.slots.write_at(s["slot"], s["received"], f.payload)
        except (ValueError, OSError):
            return self.fail("write_error")
        s["received"] += n
        if f.end:
            return self._finish(s)
        if (f.seq + 1) % PROGRESS_EVERY == 0:
            self.dev._persist()
            self.dev._emit("ota.progress", {"update_id": s["update_id"], "received": s["received"], "size": s["manifest"]["size"]})

    def _finish(self, s: dict) -> None:
        m = s["manifest"]
        if s["received"] != m["size"]:
            return self.fail("size_mismatch")
        if sha256_hex(self.slots.read(s["slot"])[:m["size"]]) != m["sha256"]:
            return self.fail("hash_mismatch")
        s["state"] = "verified"
        self.dev._persist()
        self.dev._emit("ota.verified", {"update_id": s["update_id"], "build": m["build"]})

    def fail(self, reason: str) -> None:
        s = self.st["staging"]
        if s is None:
            return
        self.slots.erase(s["slot"])
        self.st["staging"] = None
        self.dev._persist()
        self.dev._emit("ota.failed", {"update_id": s["update_id"], "reason": reason})

    def abort(self, update_id: str) -> None:
        from .device import CommandFailure
        s = self.st["staging"]
        if s is None or s["update_id"] != update_id:
            raise CommandFailure("invalid_state", "no such update in progress")
        self.fail("aborted")

    # ---- apply, confirm, rollback ---------------------------------------------------------------------
    def apply(self, update_id: str) -> dict:
        from .device import CommandFailure, PowerState as P
        s, st, dev = self.st["staging"], self.st, self.dev
        if s is None or s["update_id"] != update_id or s["state"] != "verified":
            raise CommandFailure("invalid_state", "no verified update with that id")
        if dev.power not in (P.AWAKE, P.IDLE):
            raise CommandFailure("unsafe", f"cannot apply in power state {dev.power.value}")
        if dev.battery < self.cfg.min_battery_apply:
            raise CommandFailure("unsafe", f"battery below {self.cfg.min_battery_apply}%")
        if dev.capture or dev.playback:
            raise CommandFailure("busy", "audio is active")
        m = s["manifest"]
        st["slots"][s["slot"]] = {"version": m["version"], "build": m["build"], "sha256": m["sha256"], "size": m["size"]}
        st["trial"] = {"update_id": update_id, "build": m["build"], "from_build": st["slots"][st["good"]]["build"],
                       "attempts": 0, "deadline": None}
        st["boot"], st["staging"] = s["slot"], None
        dev._persist()
        dev._emit("ota.applying", {"update_id": update_id, "build": m["build"]})
        self.reboot_requested = True
        return {"build": m["build"]}

    def on_boot(self) -> None:
        """Runs on every processor start. Decides whether a trial image may keep running."""
        t, st = self.st["trial"], self.st
        if t is None:
            return
        t["attempts"] += 1
        image = self.slots.read(st["boot"])
        if b"NT-BOOTLOOP" in image:
            self._rollback("boot_failures")
        elif b"NT-FAIL-SELFTEST" in image:
            self._rollback("self_test_failed")
        elif t["attempts"] > self.cfg.max_boot_attempts:
            self._rollback("boot_failures")
        elif t["deadline"] is None:
            t["deadline"] = self.dev.clock() + self.cfg.confirm_timeout_s
        self.dev._persist()

    def _rollback(self, reason: str) -> None:
        st, t = self.st, self.st["trial"]
        bad = st["boot"]
        notice = {"update_id": t["update_id"], "from_build": t["build"], "to_build": st["slots"][st["good"]]["build"], "reason": reason}
        st["boot"], st["trial"], st["slots"][bad] = st["good"], None, None
        self.slots.erase(bad)
        st["last_rollback"], st["notice"] = notice, notice
        self.dev._persist()

    def confirm(self, update_id: str) -> dict:
        from .device import CommandFailure
        st, t = self.st, self.st["trial"]
        if t is None:
            if st["last_committed"] == update_id:
                return {"build": st["slots"][st["good"]]["build"], "already": True}
            raise CommandFailure("invalid_state", "no update is awaiting confirmation")
        if t["update_id"] != update_id:
            raise CommandFailure("invalid_state", "that is not the update being tried")
        st["good"] = st["boot"]
        st["min_build"] = max(st["min_build"], t["build"])
        st["trial"], st["last_committed"] = None, update_id
        self.dev._persist()
        run = self.running()
        self.dev._emit("ota.committed", {"update_id": update_id, "build": run["build"], "version": run["version"]})
        return {"build": run["build"]}

    def tick(self, now: float) -> None:
        t = self.st["trial"]
        if t and t["deadline"] is not None and now >= t["deadline"]:
            self._rollback("confirm_timeout")
            self.reboot_requested = True
