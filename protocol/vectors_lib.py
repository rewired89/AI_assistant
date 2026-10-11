"""Conformance vectors: scripted conversations with the (simulated) device, recorded as data so the real firmware can
be replayed against exactly the same inputs and held to exactly the same outputs.

A vector file is JSON: {name, description, secret_hex, device_id, start_time, device: {...}, steps: [...]}.
Step kinds:
  advance {seconds}                      move the clock; then the device ticks once
  text {raw, secure}                     a signed message, exactly as bytes on the wire
  binary {hex, secure}                   a binary frame from the host
  battery {percent} | button {kind}      physical inputs
  mic {pcm_hex}                          microphone samples (only meaningful while a capture is open)
Every step records `expect`: the device's outputs, in order. A text output is {"type", "payload"} (envelope ids, nonce,
ts and MAC are checked for validity but not compared, because they differ per run); a binary output is {"hex"}.
Rule for firmware: same inputs -> same `expect`, same order. Simulator results are not hardware results; these vectors
hold the simulator and the firmware to ONE definition."""
from __future__ import annotations

import json
import secrets as _secrets
from pathlib import Path

from protocol import Authenticator, build, decode, encode
from simulator import SimulatedDevice, Storage
from simulator.device import BinaryFrame

START = 1_800_000_000.0
SECRET_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
DEVICE_ID = "vector-bot"
DIR = Path(__file__).parent / "vectors"


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_device(tmp: Path, clock: Clock, battery: int = 100) -> SimulatedDevice:
    d = SimulatedDevice(bytes.fromhex(SECRET_HEX), Storage(tmp / "flash.json"), clock, battery_percent=battery,
                        device_id=DEVICE_ID)
    return d


def _out(outputs, verifier: Authenticator) -> list[dict]:
    res = []
    for o in outputs:
        if isinstance(o, BinaryFrame):
            res.append({"hex": bytes(o).hex()})
            continue
        msg = decode(o)
        verifier.verify_mac(msg)                      # every device message must be authentic
        res.append({"type": msg["type"], "payload": msg["payload"]})
    return res


def apply_step(dev: SimulatedDevice, clock: Clock, step: dict, verifier: Authenticator) -> list[dict]:
    k = step["kind"]
    if k == "advance":
        clock.t += step["seconds"]
        outs = dev.tick()
    elif k == "text":
        outs = dev.receive(step["raw"], secure=step.get("secure", False))
    elif k == "binary":
        outs = dev.receive_binary(bytes.fromhex(step["hex"]), secure=step.get("secure", False))
    elif k == "battery":
        outs = dev.set_battery(step["percent"])
    elif k == "button":
        outs = dev.press_button(step["press"])
    elif k == "mic":
        dev.feed_mic(bytes.fromhex(step["pcm_hex"]))
        outs = dev.tick()
    else:
        raise ValueError(k)
    return _out(outs, verifier)


def replay(vector: dict, tmp: Path) -> tuple[list[dict], list[list[dict]]]:
    """Returns (outputs of the power-on boot, outputs of each step)."""
    clock = Clock(vector["start_time"])
    dev = make_device(tmp, clock, vector.get("device", {}).get("battery", 100))
    verifier = Authenticator(bytes.fromhex(vector["secret_hex"]), lambda: clock.t, vector["device_id"])
    boot = _out(dev.boot(), verifier)
    return boot, [apply_step(dev, clock, s, verifier) for s in vector["steps"]]


class Builder:
    """Writes a vector by running the steps against the simulator and recording what it does."""

    def __init__(self, name: str, description: str, battery: int = 100):
        self.name, self.description, self.start_battery = name, description, battery
        self.steps: list[dict] = []
        self._n = 0
        self.t = START
        self._signer = Authenticator(bytes.fromhex(SECRET_HEX), lambda: self.t, DEVICE_ID)

    def _signed(self, mtype: str, payload: dict | None, mid: str | None, tamper: str | None = None, ts: float | None = None) -> str:
        self._n += 1
        mid = mid or f"cmd-{self._n:05d}"
        saved, self.t = self.t, (ts if ts is not None else self.t)
        _secrets_token = _secrets.token_hex
        _secrets.token_hex = lambda n=12, c=[self._n]: f"{c[0]:0{2 * n}x}"      # deterministic nonce
        try:
            msg = self._signer.sign(build(mtype, payload, mid))
        finally:
            _secrets.token_hex = _secrets_token
            self.t = saved
        raw = encode(msg).decode()
        if tamper == "mac":
            msg["auth"]["mac"] = ("0" if msg["auth"]["mac"][0] != "0" else "1") + msg["auth"]["mac"][1:]
            raw = encode(msg).decode()
        elif tamper == "payload":
            msg["payload"] = {**msg["payload"], "x": 1}
            raw = encode(msg).decode()
        elif tamper == "device_id":
            msg["device_id"] = "someone-else"
            raw = encode(msg).decode()
        return raw

    def text(self, mtype, payload=None, mid=None, secure=False, tamper=None, ts=None, raw=None, note="", silent=False):
        self.steps.append({"kind": "text", "note": note, "silent": silent, "raw": raw if raw is not None else self._signed(mtype, payload, mid, tamper, ts),
                           "secure": secure})
        return self

    def resend(self, index: int, note="exact replay of an earlier message"):
        self.steps.append({**self.steps[index], "note": note})
        return self

    def advance(self, seconds, note=""):
        self.t += seconds
        self.steps.append({"kind": "advance", "seconds": seconds, "note": note})
        return self

    def battery(self, percent, note=""):
        self.steps.append({"kind": "battery", "percent": percent, "note": note})
        return self

    def button(self, press="single", note=""):
        self.steps.append({"kind": "button", "press": press, "note": note})
        return self

    def binary(self, frame: bytes, secure=True, note=""):
        self.steps.append({"kind": "binary", "hex": frame.hex(), "secure": secure, "note": note})
        return self

    def mic(self, pcm: bytes, note=""):
        self.steps.append({"kind": "mic", "pcm_hex": pcm.hex(), "note": note})
        return self

    def build(self, tmp: Path) -> dict:
        v = {"name": self.name, "description": self.description, "secret_hex": SECRET_HEX, "device_id": DEVICE_ID,
             "start_time": START, "device": {"battery": self.start_battery}, "steps": self.steps}
        v["boot_expect"], results = replay(v, tmp)
        for step, expect in zip(v["steps"], results):
            step["expect"] = expect
            if step["kind"] == "text" and not step.get("silent") and not expect:
                raise AssertionError(f"{self.name}: step {step['note']!r} got no answer; fix the scenario or mark it silent")
            if step.get("silent") and expect:
                raise AssertionError(f"{self.name}: step {step['note']!r} was supposed to be silent but answered")
        return v


def dump(v: dict) -> str:
    return json.dumps(v, indent=1, sort_keys=True) + "\n"
