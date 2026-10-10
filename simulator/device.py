"""Reference model of the firmware's host-facing logic, runnable without hardware.

Defines the behavior the real firmware must match. It is NOT firmware: no GPIO,
no timing accuracy, no power measurements.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable

from protocol import (Authenticator, KeyRing, ProtocolError, SUPPORTED_VERSIONS, build, check_version,
                      decode, encode, validate_payload)

FIRMWARE_VERSION = "sim-0.1"
MAX_SLEEP_DAYS = 30


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
        os.replace(tmp, self.path)

    def load(self) -> tuple[dict, bool]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_STATE)), False
        try:
            blob = json.loads(self.path.read_text())
            state = blob["state"]
            if blob["sha256"] != self._digest(state):
                raise ValueError("checksum")
            merged = json.loads(json.dumps(DEFAULT_STATE))
            merged.update({k: v for k, v in state.items() if k != "config"})
            merged["config"].update(state.get("config", {}))
            P(merged["power_state"])
            return merged, False
        except (ValueError, KeyError, TypeError, OSError):
            return json.loads(json.dumps(DEFAULT_STATE)), True


class CommandFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class SimulatedDevice:
    def __init__(self, secret: "bytes | KeyRing | None", storage: Storage, clock: Callable[[], float],
                 thresholds: Thresholds | None = None, battery_percent: int = 100, device_id: str | None = None):
        """`secret` only seeds the key ring when flash holds none (factory provisioning in tests);
        keys already in storage win, so rotations survive restarts. None = unprovisioned device."""
        self.clock = clock
        self.storage = storage
        self.th = thresholds or Thresholds()
        self.battery = battery_percent
        self.rejected_count = 0
        self._secure = False
        self.state, self.recovered = storage.load()
        self.auth: Authenticator | None = None
        if self.state.get("keys"):
            self._set_keys(KeyRing.from_dict(self.state["keys"]), self.state.get("device_id"))
        elif secret is not None:
            self._set_keys(secret if isinstance(secret, KeyRing) else KeyRing.single(secret), device_id)
        self.power = P(self.state["power_state"])
        self.brightness = self.state["config"]["default_brightness"]
        self.animation: dict | None = None
        self.capture: dict | None = None
        self._seen: OrderedDict[str, list[dict]] = OrderedDict()
        self._out: list[dict] = []

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
        self.storage.save(self.state)

    # ---- output -------------------------------------------------------
    def _emit(self, mtype: str, payload: dict) -> None:
        msg = build(mtype, payload)
        validate_payload(msg, "event")
        self._out.append(self.auth.sign(msg))

    def _flush(self) -> list[bytes]:
        out, self._out = self._out, []
        return [encode(m) for m in out]

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
        if self.power is P.SLEEPING:
            if cause == "timer" and not self._wake_due():
                return self._flush()  # spurious timer wake: go straight back to sleep
            self._wake({"timer": "scheduled", "button": "button"}.get(cause, "reset"))
        elif self.power is P.WAKING:
            self._wake("reset")
        else:
            self._settle_after_wake()
        self._emit("device.ready", {"firmware_version": FIRMWARE_VERSION,
                                    "protocol_versions": list(SUPPORTED_VERSIONS),
                                    "boot_cause": cause, "recovered_state": self.recovered})
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
        return self._flush()[0]

    # ---- physical inputs ----------------------------------------------
    def press_button(self, kind: str = "single", button: str = "main") -> list[bytes]:
        if self.power is P.SLEEPING:
            # Physical wake works without Wi-Fi; hardware support still unverified on real board.
            return self.boot("button") if kind == "double" else []
        self._emit("button.pressed", {"button": button, "kind": kind})
        return self._flush()

    def set_battery(self, percent: int) -> list[bytes]:
        self.battery = percent
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
                self._emit("device.ready", {"firmware_version": FIRMWARE_VERSION,
                                            "protocol_versions": list(SUPPORTED_VERSIONS),
                                            "boot_cause": "timer", "recovered_state": False})
            return self._flush()
        if self.animation and not self.animation["loop"] and now >= self.animation["ends"]:
            self._finish_animation(interrupted=False)
        if self.capture and now >= self.capture["ends"]:
            self._stop_capture("timeout")
        return self._flush()

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
        self._seen[mid] = [{"type": self._out[i]["type"], "payload": self._out[i]["payload"]}
                           for i in range(start, len(self._out))
                           if self._out[i]["type"].startswith("command.")]
        while len(self._seen) > 256:
            self._seen.popitem(last=False)
        return self._flush()

    def _handle(self, msg: dict) -> None:
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

    def _cmd_audio_play(self, mid: str, p: dict):
        return {"stream_id": p["stream_id"], "note": "sim: playback not modeled"}

    def _cmd_audio_start_capture(self, mid: str, p: dict):
        if not self.state["config"]["mic_enabled"]:
            raise CommandFailure("not_allowed", "microphone is muted")
        if self.power in (P.LOW_BATTERY, P.CRITICAL_BATTERY):
            raise CommandFailure("unsafe", "capture disabled on low battery")
        if self.capture:
            raise CommandFailure("busy", "capture already active")
        self.capture = {"id": p["capture_id"], "ends": self.clock() + p["max_seconds"]}
        self._emit("audio.capture.started", {"capture_id": p["capture_id"]})

    def _stop_capture(self, reason: str) -> None:
        cid, self.capture = self.capture["id"], None
        self._emit("audio.capture.stopped", {"capture_id": cid, "reason": reason})

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

    def _cmd_device_get_status(self, mid: str, p: dict):
        return {"power_state": self.power.value, "battery_percent": self.battery,
                "brightness": self.brightness, "config": dict(self.state["config"]),
                "wake_at": self.state.get("wake_at"), "capture_active": bool(self.capture),
                "animation": self.animation["name"] if self.animation else None,
                "firmware_version": FIRMWARE_VERSION}

    def _cmd_device_set_config(self, mid: str, p: dict):
        self.state["config"].update(p["config"])
        if p["config"].get("mic_enabled") is False and self.capture:
            self._stop_capture("muted")
        self._persist()
        return {"config": dict(self.state["config"])}
