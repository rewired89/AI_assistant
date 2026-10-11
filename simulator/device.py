"""Reference model of the firmware's host-facing logic, runnable without hardware.

Defines the behavior the real firmware must match. It is NOT firmware: no GPIO,
no timing accuracy, no power measurements.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import OrderedDict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable

from protocol import audio as aud
from .ota import OtaConfig, OtaEngine
from protocol import (Authenticator, KeyRing, ProtocolError, SUPPORTED_VERSIONS, build, check_version,
                      decode, encode, validate_payload)

FIRMWARE_VERSION = "sim-0.1"
MAX_SLEEP_DAYS = 30


class BinaryFrame(bytes):
    """Outbound binary audio frame. Travels in the same ordered output list as JSON messages so
    ordering between events and audio is preserved on the wire."""


PLAY_PREBUFFER_BYTES = 4800        # 150 ms before playback starts or resumes
MAX_PLAY_BUFFER = 64 * 1024        # ~2 s of PCM the device can hold; more means the host overran it
MAX_PLAY_BYTES = 30 * aud.BYTES_PER_SECOND
PLAY_IDLE_TIMEOUT_S = 5.0


class PowerState(str, Enum):
    AWAKE = "AWAKE"
    IDLE = "IDLE"
    SLEEPING = "SLEEPING"
    WAKING = "WAKING"
    LOW_BATTERY = "LOW_BATTERY"
    CRITICAL_BATTERY = "CRITICAL_BATTERY"


P = PowerState
ALLOWED_TRANSITIONS: dict[PowerState, frozenset[PowerState]] = {
    P.AWAKE: frozenset({P.IDLE, P.SLEEPING, P.LOW_BATTERY, P.CRITICAL_BATTERY}),
    P.IDLE: frozenset({P.AWAKE, P.SLEEPING, P.LOW_BATTERY, P.CRITICAL_BATTERY}),
    P.SLEEPING: frozenset({P.WAKING}),
    P.WAKING: frozenset({P.AWAKE, P.LOW_BATTERY, P.CRITICAL_BATTERY}),
    P.LOW_BATTERY: frozenset({P.AWAKE, P.IDLE, P.SLEEPING, P.CRITICAL_BATTERY}),
    P.CRITICAL_BATTERY: frozenset({P.SLEEPING}),
}

ANIMATION_MS = {"waking": 1500, "sleeping": 2000, "happy": 1200, "excited": 1200,
                "celebratory": 2000, "surprised": 800}
DEFAULT_ANIMATION_MS = 1500


class InvalidTransition(Exception):
    pass


class Thresholds:
    def __init__(self, warn=20, low=10, critical=5, hysteresis=3, low_brightness_cap=30):
        if not (0 <= critical < low < warn <= 100):
            raise ValueError("need 0 <= critical < low < warn <= 100")
        self.warn, self.low, self.critical = warn, low, critical
        self.hysteresis, self.low_brightness_cap = hysteresis, low_brightness_cap


DEFAULT_STATE = {
    "power_state": "AWAKE", "wake_at": None, "sleep_reason": None, "boot_count": 0,
    "config": {"volume": 60, "default_brightness": 70, "idle_sleep_seconds": 300,
               "mic_enabled": True},
}


class Storage:
    """Checksummed JSON file with atomic replace. Corruption yields (defaults, recovered=True)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @staticmethod
    def _digest(state: dict) -> str:
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    def save(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": state, "sha256": self._digest(state)}))
        if self.path.exists() and self._read(self.path) is not None:      # keep the last GOOD copy as a fallback
            shutil.copyfile(self.path, self.path.with_suffix(".bak"))
        os.replace(tmp, self.path)

    def _read(self, path: Path) -> dict | None:
        try:
            blob = json.loads(path.read_text())
            state = blob["state"]
            if blob["sha256"] != self._digest(state):
                return None
            merged = json.loads(json.dumps(DEFAULT_STATE))
            merged.update({k: v for k, v in state.items() if k != "config"})
            merged["config"].update(state.get("config", {}))
            P(merged["power_state"])
            return merged
        except (ValueError, KeyError, TypeError, OSError):
            return None

    def load(self) -> tuple[dict, bool]:
        """Primary file, else the last good backup (recovered=True), else factory defaults (recovered=True).
        A recovered state always comes back with the microphone MUTED: after damage the device must fail safe, and
        only a physical long press can unmute it."""
        if not self.path.exists() and not self.path.with_suffix(".bak").exists():
            return json.loads(json.dumps(DEFAULT_STATE)), False
        state = self._read(self.path)
        if state is not None:
            return state, False
        state = self._read(self.path.with_suffix(".bak")) or json.loads(json.dumps(DEFAULT_STATE))
        state["config"]["mic_enabled"] = False
        return state, True


class CommandFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class SimulatedDevice:
    def __init__(self, secret: "bytes | KeyRing | None", storage: Storage, clock: Callable[[], float],
                 thresholds: Thresholds | None = None, battery_percent: int = 100, device_id: str | None = None,
                 ota: OtaConfig | None = None):
        """`secret` only seeds the key ring when flash holds none (factory provisioning in tests);
        keys already in storage win, so rotations survive restarts. None = unprovisioned device."""
        self.clock = clock
        self.storage = storage
        self.th = thresholds or Thresholds()
        self.battery = battery_percent
        self.rejected_count = 0
        self._secure = False
        self.state, self.recovered = storage.load()
        self.rebooted = 0
        self.ota = OtaEngine(self, ota) if ota is not None else None
        self.auth: Authenticator | None = None
        if self.state.get("keys"):
            self._set_keys(KeyRing.from_dict(self.state["keys"]), self.state.get("device_id"))
        elif secret is not None:
            self._set_keys(secret if isinstance(secret, KeyRing) else KeyRing.single(secret), device_id)
        self.power = P(self.state["power_state"])
        self.brightness = self.state["config"]["default_brightness"]
        self.animation: dict | None = None
        self.capture: dict | None = None
        self.playback: dict | None = None
        self.audio_rejected = 0
        self.on_speaker = None                      # optional sink for played PCM (a desk setup wires a real speaker here)
        self._seen: OrderedDict[str, list[dict]] = OrderedDict()
        self._out: list[dict] = []

    @property
    def fw_version(self) -> str:
        return self.ota.running()["version"] if self.ota else FIRMWARE_VERSION

    def reboot(self, cause: str = "reset") -> list[bytes]:
        """Processor restart (e.g. after an update). RAM is lost, flash state survives, boot logic runs again."""
        self.rebooted += 1
        self.animation = self.capture = self.playback = None
        self._seen.clear()
        self._out = []
        if self.auth is not None:
            self.auth._seen.clear()
        self._persist()
        self.state, self.recovered = self.storage.load()
        if self.ota:
            self.ota.st = self.state["ota"]
        self.power = P(self.state["power_state"])
        outs = self.boot(cause)
        if self.ota:
            notice = self.ota.take_notice()
            if notice:                                  # also delivered here so a connected host sees it immediately
                self._emit("ota.rolled_back", notice)
                outs = outs + self._flush()
        return outs

    # ---- keys / provisioning --------------------------------------------
    def _set_keys(self, ring: KeyRing, device_id: str | None) -> None:
        ring.on_change = self._persist
        self.auth = Authenticator(ring, self.clock, device_id)
        self.state["device_id"] = device_id

    @property
    def provisioned(self) -> bool:
        return self.auth is not None

    def provision(self, bundle: dict) -> None:
        """One-time, physical-channel (USB) provisioning. Refuses to overwrite existing keys:
        replacing them needs factory_reset(), a deliberate physical action."""
        if self.auth is not None:
            raise PermissionError("already provisioned; factory reset first")
        if bundle.get("v") != 1 or bundle.get("kid") != 1:
            raise ValueError("unsupported bundle")
        key = bytes.fromhex(bundle["key"])
        self._set_keys(KeyRing({1: key}, 1), bundle["device_id"])
        self._persist()

    def factory_reset(self) -> None:
        self.state = json.loads(json.dumps(DEFAULT_STATE))
        self.auth, self.power = None, P.AWAKE
        if self.ota:
            self.ota = OtaEngine(self, self.ota.cfg)             # fresh slots and floor, as on first boot
        self.storage.save(self.state)

    # ---- output -------------------------------------------------------
    def _emit(self, mtype: str, payload: dict) -> None:
        msg = build(mtype, payload)
        validate_payload(msg, "event")
        if self.auth is None:
            return                                   # unprovisioned: inert, says nothing
        self._out.append(self.auth.sign(msg))

    def _flush(self) -> list[bytes]:
        out, self._out = self._out, []
        return [m if isinstance(m, BinaryFrame) else encode(m) for m in out]

    # ---- persistence / state ------------------------------------------
    def _persist(self) -> None:
        if self.auth is not None:
            self.state["keys"] = self.auth.ring.to_dict()
        self.state["power_state"] = self.power.value
        self.storage.save(self.state)

    def _go(self, new: PowerState) -> None:
        if new == self.power:
            return
        if new not in ALLOWED_TRANSITIONS[self.power]:
            raise InvalidTransition(f"{self.power.value} -> {new.value}")
        self.power = new
        self._persist()

    # ---- boot / wake --------------------------------------------------
    def boot(self, cause: str = "power_on") -> list[bytes]:
        """Called on every processor start. Deep-sleep wake is a reboot on ESP32."""
        self.state["boot_count"] += 1
        if self.ota:
            self.ota.on_boot()
        if self.power is P.SLEEPING:
            if cause == "timer" and not self._wake_due():
                return self._flush()  # spurious timer wake: go straight back to sleep
            self._wake({"timer": "scheduled", "button": "button"}.get(cause, "reset"))
        elif self.power is P.WAKING:
            self._wake("reset")
        else:
            self._settle_after_wake()
        ready = {"firmware_version": self.fw_version, "protocol_versions": list(SUPPORTED_VERSIONS),
                 "boot_cause": cause, "recovered_state": self.recovered}
        if self.ota:
            ready["build"] = self.ota.running()["build"]
        self._emit("device.ready", ready)
        return self._flush()

    def _wake_due(self) -> bool:
        w = self.state.get("wake_at")
        return w is not None and self.clock() >= datetime.fromisoformat(w).timestamp()

    def _wake(self, cause: str) -> None:
        self._go(P.WAKING)
        self._emit("power.waking", {"cause": cause})
        self.state["wake_at"] = self.state["sleep_reason"] = None
        self._settle_after_wake()
        self._emit("power.woke", {"cause": cause})
        self._persist()

    def _settle_after_wake(self) -> None:
        target = self._battery_state()
        if self.power is P.WAKING:
            self._go(target)
        elif target is not self.power and self.power in (P.AWAKE, P.IDLE, P.LOW_BATTERY):
            self._go(target)
        self._apply_low_battery_limits()

    def _battery_state(self) -> PowerState:
        if self.battery <= self.th.critical:
            return P.CRITICAL_BATTERY
        if self.battery <= self.th.low:
            return P.LOW_BATTERY
        if self.power is P.LOW_BATTERY and self.battery < self.th.low + self.th.hysteresis:
            return P.LOW_BATTERY
        return P.AWAKE if self.power in (P.WAKING, P.LOW_BATTERY) else self.power

    def _apply_low_battery_limits(self) -> None:
        if self.power in (P.LOW_BATTERY, P.CRITICAL_BATTERY):
            self.brightness = min(self.brightness, self.th.low_brightness_cap)
            if self.capture:
                self._stop_capture("low_battery")

    def announce(self) -> bytes:
        """First frame after (re)connecting: current battery/power state, so the host learns status
        without the device claiming a reboot (device.ready is boot-only)."""
        self._emit("battery.updated", {"percent": self.battery, "power_state": self.power.value,
                                       "warning": self.battery <= self.th.warn})
        if self.ota:
            notice = self.ota.take_notice()
            if notice:
                self._emit("ota.rolled_back", notice)
        first, self._out = self._out[0], self._out[1:]                 # the rest goes out with the next flush
        return encode(first)

    # ---- physical inputs ----------------------------------------------
    def press_button(self, kind: str = "single", button: str = "main") -> list[bytes]:
        if self.power is P.SLEEPING:
            # Physical wake works without Wi-Fi; hardware support still unverified on real board.
            return self.boot("button") if kind == "double" else []
        if kind == "long":
            # The ONLY way to unmute the microphone (and a quick physical mute). The host can never unmute.
            on = not self.state["config"]["mic_enabled"]
            self.state["config"]["mic_enabled"] = on
            if not on and self.capture:
                self._stop_capture("muted")
            self._persist()
        self._emit("button.pressed", {"button": button, "kind": kind})
        return self._flush()

    def set_battery(self, percent: int) -> list[bytes]:
        self.battery = percent
        if self.ota and self.ota.busy() and percent < self.ota.cfg.min_battery_apply and self.power is not P.SLEEPING:
            self.ota.fail("low_battery")                 # never keep writing flash on a dying battery
        if self.power is P.SLEEPING:
            return []
        target = self._battery_state()
        warning = percent <= self.th.warn
        if target is P.CRITICAL_BATTERY and self.power is not P.CRITICAL_BATTERY:
            self._go(P.CRITICAL_BATTERY)
        elif target is not self.power and self.power in (P.AWAKE, P.IDLE, P.LOW_BATTERY):
            self._go(target)
        self._apply_low_battery_limits()
        self._emit("battery.updated", {"percent": percent, "power_state": self.power.value,
                                       "warning": warning})
        if self.power is P.CRITICAL_BATTERY:
            self._enter_sleep(None, "critical_battery")  # persist, shut down; button/reset only
        return self._flush()

    def tick(self) -> list[bytes]:
        now = self.clock()
        if self.power is P.SLEEPING:
            if self._wake_due():
                self._wake("scheduled")
                self._emit("device.ready", {"firmware_version": self.fw_version,
                                            "protocol_versions": list(SUPPORTED_VERSIONS),
                                            "boot_cause": "timer", "recovered_state": False})
            return self._flush()
        if self.animation and not self.animation["loop"] and now >= self.animation["ends"]:
            self._finish_animation(interrupted=False)
        if self.capture and now >= self.capture["ends"]:
            self._stop_capture("timeout")
        self._advance_playback(now)
        if self.ota:
            self.ota.tick(now)
        return self._with_reboot(self._flush())

    def _with_reboot(self, out: list) -> list:
        """An update (or a rollback) asked for a restart: send what is queued, then restart."""
        if self.ota and self.ota.take_reboot():
            return out + self.reboot("reset")
        return out

    # ---- inbound ------------------------------------------------------
    def receive(self, raw: bytes | str, secure: bool = False) -> list[bytes]:
        """secure=True means the frame arrived over a verified TLS session (set by the wss link)."""
        if self.power is P.SLEEPING or self.auth is None:
            return []  # radio off while sleeping; unprovisioned devices ignore everything
        self._secure = secure
        try:
            msg = decode(raw)
            self.auth.verify(msg)  # authenticate before replying to anything
            check_version(msg)
        except ProtocolError as e:
            self.rejected_count += 1
            if e.code in ("unsupported_version",) and e.message_id:
                self._emit("command.failed", {"command_id": e.message_id, "code": "unsupported_command",
                                              "message": e.message[:200]})
            return self._flush()  # silent drop for malformed/auth/replay: no oracle for attackers
        mid = msg["message_id"]
        if mid in self._seen:  # duplicate: do not re-execute, repeat the terminal answer
            for m in self._seen[mid]:
                self._out.append(self.auth.sign(build(m["type"], m["payload"])))
            return self._flush()
        start = len(self._out)
        self._handle(msg)
        self._seen[mid] = [{"type": m["type"], "payload": m["payload"]}
                           for m in self._out[start:]
                           if isinstance(m, dict) and m["type"].startswith("command.")]
        while len(self._seen) > 256:
            self._seen.popitem(last=False)
        return self._with_reboot(self._flush())

    def _handle(self, msg: dict) -> None:
        self._advance_playback(self.clock())  # commands must see playback progress as of now
        mid, mtype = msg["message_id"], msg["type"]
        try:
            validate_payload(msg, "command")
        except ProtocolError as e:
            code = "unsupported_command" if e.code == "unknown_type" else "invalid_payload"
            self._emit("command.failed", {"command_id": mid, "code": code, "message": e.message[:200]})
            return
        try:
            handler = getattr(self, "_cmd_" + mtype.replace(".", "_"))
            self._emit("command.accepted", {"command_id": mid, "command_type": mtype})
            result = handler(mid, msg["payload"])
            if result is not False:  # False = completion deferred to a later event
                self._emit("command.completed", {"command_id": mid, **({"result": result} if result else {})})
        except CommandFailure as f:
            self._emit("command.failed", {"command_id": mid, "code": f.code, "message": f.message})

    # ---- commands -----------------------------------------------------
    def _cmd_animation_play(self, mid: str, p: dict):
        prio = p.get("priority", 5)
        if self.animation:
            if self.animation["priority"] > prio:
                raise CommandFailure("busy", "higher-priority animation playing")
            self._finish_animation(interrupted=True)
        ms = ANIMATION_MS.get(p["name"], DEFAULT_ANIMATION_MS)
        self.animation = {"name": p["name"], "loop": p.get("loop", False), "priority": prio,
                          "cmd": mid, "ends": self.clock() + ms / 1000}
        return False

    def _finish_animation(self, interrupted: bool) -> None:
        a, self.animation = self.animation, None
        self._emit("animation.finished", {"name": a["name"], "interrupted": interrupted})
        self._emit("command.completed", {"command_id": a["cmd"]})

    def _cmd_animation_stop(self, mid: str, p: dict):
        if not self.animation:
            raise CommandFailure("invalid_state", "no animation playing")
        self._finish_animation(interrupted=True)

    def _cmd_display_set_expression(self, mid: str, p: dict):
        return {"expression": p["expression"]}

    def _cmd_display_set_brightness(self, mid: str, p: dict):
        cap = self.th.low_brightness_cap
        if self.power in (P.LOW_BATTERY, P.CRITICAL_BATTERY) and p["level"] > cap:
            raise CommandFailure("unsafe", f"brightness capped at {cap} on low battery")
        self.brightness = p["level"]

    # ---- audio ----------------------------------------------------------
    def _cmd_audio_play(self, mid: str, p: dict):
        if self.ota and self.ota.busy():
            raise CommandFailure("busy", "firmware update in progress")
        if p["format"] != "pcm16_16k_mono":
            raise CommandFailure("unsupported_command", "only pcm16_16k_mono is supported in this firmware")
        if self.playback:
            raise CommandFailure("busy", "playback already active")
        self.playback = {"id": p["stream_id"], "stream_no": p["stream_no"], "cmd": mid, "next_seq": 0,
                         "received": 0, "consumed": 0, "ended": False, "playing": False, "stalled": False,
                         "underruns": 0, "last_t": self.clock(), "last_rx": self.clock(),
                         "style": p.get("style", "normal"), "volume": p.get("volume")}
        return False  # completion is audio.playback.finished + command.completed, after the audio has played

    def _cmd_audio_stop_playback(self, mid: str, p: dict):
        if not self.playback or self.playback["id"] != p["stream_id"]:
            raise CommandFailure("invalid_state", "no such active playback")
        self._finish_playback("interrupted")

    def _finish_playback(self, result: str, fail: tuple[str, str] | None = None) -> None:
        pl, self.playback = self.playback, None
        self._emit("audio.playback.finished", {"stream_id": pl["id"], "result": result,
                                               "bytes_played": int(pl["consumed"]), "underruns": pl["underruns"]})
        if fail:
            self._emit("command.failed", {"command_id": pl["cmd"], "code": fail[0], "message": fail[1]})
        else:
            self._emit("command.completed", {"command_id": pl["cmd"]})

    def _advance_playback(self, now: float) -> None:
        pl = self.playback
        if pl is None:
            return
        dt, pl["last_t"] = max(0.0, now - pl["last_t"]), now
        if pl["playing"]:
            want = dt * aud.BYTES_PER_SECOND
            have = pl["received"] - pl["consumed"]
            take = min(want, have)
            pl["consumed"] += take
            if take < want and not pl["ended"]:  # ran dry: audible glitch, rebuffer before resuming
                pl["underruns"] += 1
                pl["playing"], pl["stalled"] = False, True
        if pl["ended"] and pl["consumed"] >= pl["received"] - 1e-6:
            self._finish_playback("completed")
        elif not pl["ended"] and now - pl["last_rx"] > PLAY_IDLE_TIMEOUT_S:
            self._finish_playback("error", ("timeout", "no audio data from host"))

    def receive_binary(self, frame: bytes, secure: bool = False) -> list[bytes]:
        """Host -> device audio. Accepted only on a TLS session, for the one stream the host opened
        with a signed audio.play, strictly in sequence. Anything else is dropped and counted."""
        if self.power is P.SLEEPING or self.auth is None:
            return []
        pl = self.playback
        try:
            if not secure:
                raise aud.AudioFrameError("binary audio needs a TLS channel")
            f = aud.unpack(frame)
            if f.kind == aud.KIND_OTA:
                if self.ota is None:
                    raise aud.AudioFrameError("no update support")
                self.ota.on_frame(f)
                return self._flush()
            if f.kind != aud.KIND_PLAYBACK or f.codec != aud.CODEC_PCM16:
                raise aud.AudioFrameError("unexpected kind/codec")
            if pl is None or pl["stream_no"] != f.stream_no or pl["ended"]:
                raise aud.AudioFrameError("no such open stream")
        except aud.AudioFrameError:
            self.audio_rejected += 1
            return self._flush()
        now = self.clock()
        self._advance_playback(now)
        if self.playback is None:  # finished or timed out while we were catching up
            self.audio_rejected += 1
            return self._flush()
        if f.seq != pl["next_seq"]:
            self._finish_playback("error", ("invalid_state", "audio sequence gap"))
            return self._flush()
        pl["next_seq"] += 1
        if self.on_speaker is not None:
            self.on_speaker(f.payload)
        pl["received"] += len(f.payload)
        pl["last_rx"] = now
        if pl["received"] - pl["consumed"] > MAX_PLAY_BUFFER or pl["received"] > MAX_PLAY_BYTES:
            self._finish_playback("error", ("busy", "audio buffer overrun"))
            return self._flush()
        if f.end:
            pl["ended"] = True
        if not pl["playing"] and (pl["received"] - pl["consumed"] >= PLAY_PREBUFFER_BYTES or pl["ended"]):
            pl["playing"], pl["stalled"], pl["last_t"] = True, False, now
        if pl["ended"] and pl["received"] == 0:
            self._finish_playback("completed")
        return self._flush()

    def feed_mic(self, pcm: bytes) -> bool:
        """Test/sim hook standing in for the I2S microphone. Returns False when the mic is closed."""
        cap = self.capture
        if cap is None:
            return False
        room = cap["max_bytes"] - cap["sent"] - len(cap["rem"])
        data = cap["rem"] + pcm[:max(0, room)]
        cap["rem"] = b""
        while len(data) >= aud.CHUNK_BYTES:
            self._out.append(BinaryFrame(aud.pack(aud.KIND_CAPTURE, cap["stream_no"], cap["seq"], data[:aud.CHUNK_BYTES])))
            cap["seq"] += 1
            cap["sent"] += aud.CHUNK_BYTES
            data = data[aud.CHUNK_BYTES:]
        cap["rem"] = data
        if len(pcm) > room:
            self._stop_capture("timeout")
        return True

    def _cmd_audio_start_capture(self, mid: str, p: dict):
        if self.ota and self.ota.busy():
            raise CommandFailure("busy", "firmware update in progress")
        if not self.state["config"]["mic_enabled"]:
            raise CommandFailure("not_allowed", "microphone is muted")
        if self.power in (P.LOW_BATTERY, P.CRITICAL_BATTERY):
            raise CommandFailure("unsafe", "capture disabled on low battery")
        if self.capture:
            raise CommandFailure("busy", "capture already active")
        self.capture = {"id": p["capture_id"], "stream_no": p["stream_no"], "ends": self.clock() + p["max_seconds"],
                        "seq": 0, "rem": b"", "sent": 0, "max_bytes": p["max_seconds"] * aud.BYTES_PER_SECOND}
        self._emit("audio.capture.started", {"capture_id": p["capture_id"]})

    def _stop_capture(self, reason: str) -> None:
        cap, self.capture = self.capture, None
        tail = cap["rem"][: len(cap["rem"]) // 2 * 2]
        self._out.append(BinaryFrame(aud.pack(aud.KIND_CAPTURE, cap["stream_no"], cap["seq"], tail, end=True)))
        self._emit("audio.capture.stopped", {"capture_id": cap["id"], "reason": reason})

    def _cmd_audio_stop_capture(self, mid: str, p: dict):
        if not self.capture or self.capture["id"] != p["capture_id"]:
            raise CommandFailure("invalid_state", "no such active capture")
        self._stop_capture("host_request")

    def _cmd_power_sleep(self, mid: str, p: dict):
        wake_at = p.get("wake_at")
        if wake_at is not None:
            try:
                dt = datetime.fromisoformat(wake_at)
            except ValueError:
                raise CommandFailure("invalid_payload", "wake_at is not ISO 8601") from None
            if dt.tzinfo is None:
                raise CommandFailure("invalid_payload", "wake_at needs a UTC offset")
            now = datetime.fromtimestamp(self.clock(), tz=dt.tzinfo)
            if dt <= now:
                raise CommandFailure("invalid_payload", "wake_at is not in the future")
            if dt - now > timedelta(days=MAX_SLEEP_DAYS):
                raise CommandFailure("invalid_payload", f"wake_at more than {MAX_SLEEP_DAYS} days away")
        if self.power not in (P.AWAKE, P.IDLE, P.LOW_BATTERY, P.CRITICAL_BATTERY):
            raise CommandFailure("invalid_state", f"cannot sleep from {self.power.value}")
        self._enter_sleep(wake_at, p["reason"])
        return {"wake_at": wake_at}

    def _enter_sleep(self, wake_at: str | None, reason: str) -> None:
        if self.animation:
            self._finish_animation(interrupted=True)
        if self.capture:
            self._stop_capture("low_battery")
        if self.playback:
            self._finish_playback("interrupted")
        self.state["wake_at"], self.state["sleep_reason"] = wake_at, reason
        self._go(P.SLEEPING)
        self._emit("power.sleeping", {"reason": reason, "wake_at": wake_at})

    def _cmd_power_wake(self, mid: str, p: dict):
        if self.power is P.CRITICAL_BATTERY:
            raise CommandFailure("unsafe", "critical battery")
        raise CommandFailure("invalid_state", f"already {self.power.value}")

    def _cmd_security_rotate_key(self, mid: str, p: dict):
        if not self._secure:
            raise CommandFailure("not_allowed", "key rotation needs a TLS channel")
        ring = self.auth.ring
        if len(ring.kids()) != 1:
            raise CommandFailure("invalid_state", "previous rotation not finished")
        if p["kid"] != max(ring.kids()) + 1:
            raise CommandFailure("invalid_state", "kid must be the next key id")
        ring.add(p["kid"], bytes.fromhex(p["key"]))
        ring.use_for_signing(p["kid"])  # old key stays valid until the host is seen using the new one
        return {"kid": p["kid"]}

    def _ota_or_fail(self):
        if self.ota is None:
            raise CommandFailure("unsupported_command", "this firmware has no update support")
        if not self._secure:
            raise CommandFailure("not_allowed", "firmware updates need a TLS channel")
        return self.ota

    def _cmd_ota_begin(self, mid: str, p: dict):
        return self._ota_or_fail().begin(p)

    def _cmd_ota_abort(self, mid: str, p: dict):
        self._ota_or_fail().abort(p["update_id"])

    def _cmd_ota_apply(self, mid: str, p: dict):
        return self._ota_or_fail().apply(p["update_id"])

    def _cmd_ota_confirm(self, mid: str, p: dict):
        return self._ota_or_fail().confirm(p["update_id"])

    def _cmd_device_get_status(self, mid: str, p: dict):
        return {"power_state": self.power.value, "battery_percent": self.battery,
                "brightness": self.brightness, "config": dict(self.state["config"]),
                "wake_at": self.state.get("wake_at"), "capture_active": bool(self.capture), "playback_active": bool(self.playback),
                "animation": self.animation["name"] if self.animation else None,
                "firmware_version": self.fw_version, **({"build": self.ota.running()["build"], "ota": self.ota.summary()} if self.ota else {})}

    def _cmd_device_set_config(self, mid: str, p: dict):
        self.state["config"].update(p["config"])
        if p["config"].get("mic_enabled") is False and self.capture:
            self._stop_capture("muted")
        self._persist()
        return {"config": dict(self.state["config"])}
