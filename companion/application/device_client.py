"""Host-side client: signs commands, tracks acceptance vs completion, enforces timeouts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from protocol import Authenticator, ProtocolError, build, check_version, decode, encode, validate_payload

from ..interfaces import Clock, DeviceTransport


class CommandTimeout(Exception):
    pass


class CommandRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


@dataclass
class Outcome:
    message_id: str
    accepted: bool = False
    completed: bool = False
    failed: tuple[str, str] | None = None
    result: dict | None = None


@dataclass
class DeviceClient:
    transport: DeviceTransport
    auth: Authenticator
    clock: Clock
    events: list[dict] = field(default_factory=list)
    _pending: dict[str, Outcome] = field(default_factory=dict)

    def send(self, mtype: str, payload: dict | None = None) -> Outcome:
        msg = build(mtype, payload)
        validate_payload(msg, "command")  # host-side validation before anything is sent
        self.transport.send(encode(self.auth.sign(msg)))
        out = self._pending[msg["message_id"]] = Outcome(msg["message_id"])
        return out

    def pump(self) -> None:
        for raw in self.transport.receive():
            try:
                msg = decode(raw)
                self.auth.verify(msg)
                check_version(msg)
                validate_payload(msg, "event")
            except ProtocolError:
                continue  # drop anything unauthenticated or malformed
            self._apply(msg)

    def _apply(self, msg: dict) -> None:
        p, t = msg["payload"], msg["type"]
        out = self._pending.get(p.get("command_id", ""))
        if t == "command.accepted" and out:
            out.accepted = True
        elif t == "command.completed" and out:
            out.completed, out.result = True, p.get("result")
        elif t == "command.failed" and out:
            out.failed = (p["code"], p["message"])
        else:
            self.events.append(msg)

    def request(self, mtype: str, payload: dict | None = None, timeout_s: float = 5.0,
                until: str = "completed") -> Outcome:
        """Send and wait. until='accepted' returns once the device accepts; 'completed' waits
        for the completion event. Accepted alone is never reported as done."""
        out = self.send(mtype, payload)
        deadline = self.clock.now() + timeout_s
        while True:
            self.pump()
            if out.failed:
                raise CommandRejected(*out.failed)
            if (until == "accepted" and out.accepted) or out.completed:
                return out
            if self.clock.now() >= deadline:
                raise CommandTimeout(f"{mtype} ({'accepted' if out.accepted else 'no ack'})")
            self.clock.sleep(0.05)


def backoff_delays(base: float = 1.0, cap: float = 30.0, jitter: float = 0.2,
                   rng=None) -> Iterator[float]:
    """Exponential backoff with bounded jitter, for reconnect loops."""
    import random
    rng = rng or random.Random()
    delay = base
    while True:
        yield min(cap, delay) * (1 + rng.uniform(-jitter, jitter))
        delay = min(cap, delay * 2)


class LoopbackTransport(DeviceTransport):
    """Connects a DeviceClient directly to a SimulatedDevice in-process."""

    def __init__(self, device):
        self.device, self._inbox = device, []

    def send(self, raw: bytes) -> None:
        self._inbox.extend(self.device.receive(raw))

    def receive(self) -> list[bytes]:
        self._inbox.extend(self.device.tick())  # device runs on its own clock
        out, self._inbox = self._inbox, []
        return out
