"""Host-side client: signs commands, tracks acceptance vs completion, enforces timeouts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from protocol import Authenticator, ProtocolError, build, check_version, decode, encode, validate_payload
from protocol import audio as aud
import uuid

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
class Capture:
    """Host view of one microphone capture. PCM arrives as binary frames; `done` means the END frame was seen."""
    capture_id: str
    stream_no: int
    max_bytes: int
    chunks: list[bytes] = field(default_factory=list)
    next_seq: int = 0
    done: bool = False
    corrupt: bool = False
    stop_reason: str | None = None

    @property
    def pcm(self) -> bytes:
        return b"".join(self.chunks)

    @property
    def seconds(self) -> float:
        return aud.pcm_seconds(sum(len(c) for c in self.chunks))


@dataclass
class DeviceClient:
    transport: DeviceTransport
    auth: Authenticator
    clock: Clock
    events: list[dict] = field(default_factory=list)
    _pending: dict[str, Outcome] = field(default_factory=dict)
    captures: dict[int, Capture] = field(default_factory=dict)
    playback_results: dict[str, dict] = field(default_factory=dict)
    audio_dropped: int = 0
    listeners: list = field(default_factory=list)
    _stream_counter: int = 0
    _active_playback: set = field(default_factory=set)

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
        for raw in self.transport.receive_binary():
            self._on_audio(raw)

    def add_listener(self, fn) -> None:
        """fn(event_message) is called for every verified device event that is not a command reply."""
        self.listeners.append(fn)

    def _on_audio(self, raw: bytes) -> None:
        try:
            f = aud.unpack(raw)
        except aud.AudioFrameError:
            self.audio_dropped += 1
            return
        cap = self.captures.get(f.stream_no)
        if f.kind != aud.KIND_CAPTURE or cap is None or cap.done or f.codec != aud.CODEC_PCM16:
            self.audio_dropped += 1  # unknown or finished stream: never accept audio nobody asked for
            return
        if f.seq != cap.next_seq or sum(len(c) for c in cap.chunks) + len(f.payload) > cap.max_bytes:
            cap.corrupt = True
            self.audio_dropped += 1
            return
        cap.next_seq += 1
        if f.payload:
            cap.chunks.append(f.payload)
        cap.done = f.end

    def _alloc_stream(self) -> int:
        busy = {n for n, c in self.captures.items() if not c.done} | self._active_playback
        for _ in range(65535):
            self._stream_counter = self._stream_counter % 65535 + 1
            if self._stream_counter not in busy:
                return self._stream_counter
        raise RuntimeError("no free stream numbers")

    # ---- audio: capture -----------------------------------------------------------
    def start_capture(self, max_seconds: int = 10, timeout_s: float = 5.0) -> Capture:
        cap = Capture(uuid.uuid4().hex[:12], self._alloc_stream(), max_seconds * aud.BYTES_PER_SECOND)
        self.captures[cap.stream_no] = cap
        try:
            self.request("audio.start_capture", {"capture_id": cap.capture_id, "stream_no": cap.stream_no,
                                                 "max_seconds": max_seconds}, timeout_s=timeout_s)
        except BaseException:
            self.captures.pop(cap.stream_no, None)
            raise
        return cap

    def wait_capture(self, cap: Capture, timeout_s: float = 35.0) -> Capture:
        deadline = self.clock.now() + timeout_s
        while not cap.done:
            self.pump()
            if cap.done:
                break
            if self.clock.now() >= deadline:
                raise CommandTimeout("capture did not finish")
            self.clock.sleep(0.02)
        return cap

    def stop_capture(self, cap: Capture, timeout_s: float = 5.0) -> Capture:
        self.request("audio.stop_capture", {"capture_id": cap.capture_id}, timeout_s=timeout_s)
        return self.wait_capture(cap, timeout_s)

    # ---- audio: playback ------------------------------------------------------------
    def begin_playback(self, pcm: bytes, style: str = "normal", volume: int | None = None, prebuffer_s: float = 0.5,
                       timeout_s: float | None = None) -> "PlaybackJob":
        """Non-blocking playback: call job.step() from your loop. See PlaybackJob."""
        return PlaybackJob(self, pcm, style, volume, prebuffer_s, timeout_s)

    def play_pcm(self, pcm: bytes, style: str = "normal", volume: int | None = None, prebuffer_s: float = 0.5,
                 timeout_s: float | None = None) -> Outcome:
        """Blocking convenience wrapper over PlaybackJob."""
        job = self.begin_playback(pcm, style, volume, prebuffer_s, timeout_s)
        while not job.step():
            self.clock.sleep(0.02)
        return job.out

    def stop_playback(self, stream_id: str, timeout_s: float = 5.0) -> Outcome:
        return self.request("audio.stop_playback", {"stream_id": stream_id}, timeout_s=timeout_s)

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
            if t == "audio.capture.stopped":
                for cap in self.captures.values():
                    if cap.capture_id == p["capture_id"]:
                        cap.stop_reason = p["reason"]
                        if cap.corrupt:      # its END frame may have been dropped as out-of-sequence; the event is final
                            cap.done = True
            elif t == "audio.playback.finished":
                self.playback_results[p["stream_id"]] = p
            self.events.append(msg)
            del self.events[:-500]                    # keep memory bounded; use add_listener for real consumption
            for fn in list(self.listeners):
                fn(msg)

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


class PlaybackJob:
    """One audio.play stream. Sends `prebuffer_s` of audio at once, then holds that lead by pacing at exactly
    real time (the device drains at its sample clock and buffers ~2 s, so faster would overrun it).
    step() returns True only when the device reports the audio played out, never merely when it was sent.
    Raises CommandRejected / CommandTimeout if the device refuses, aborts or goes quiet."""

    def __init__(self, client: "DeviceClient", pcm: bytes, style: str, volume: int | None,
                 prebuffer_s: float, timeout_s: float | None):
        if len(pcm) % 2:
            raise ValueError("PCM16 data must have an even number of bytes")
        if not 0.1 <= prebuffer_s <= 1.5:
            raise ValueError("prebuffer_s must be between 0.1 and 1.5 s (device buffer is ~2 s)")
        self.c, self.pcm, self.prebuffer_s = client, pcm, prebuffer_s
        self.stream_id, self.stream_no = "s" + uuid.uuid4().hex[:10], client._alloc_stream()
        payload = {"stream_id": self.stream_id, "stream_no": self.stream_no, "format": "pcm16_16k_mono", "style": style}
        if volume is not None:
            payload["volume"] = volume
        self.out = client.send("audio.play", payload)
        client._active_playback.add(self.stream_no)
        self.t0 = client.clock.now()
        self.limit = timeout_s if timeout_s is not None else aud.pcm_seconds(len(pcm)) + 10
        self.sent = self.seq = 0
        self.end_sent = self.finished = False

    def _release(self) -> None:
        self.finished = True
        self.c._active_playback.discard(self.stream_no)

    def step(self) -> bool:
        if self.finished:
            return True
        c = self.c
        try:
            c.pump()
            if self.out.failed:
                raise CommandRejected(*self.out.failed)
            if self.out.completed:
                self._release()
                return True
            if c.clock.now() - self.t0 > self.limit:
                raise CommandTimeout("playback did not complete")
            if self.out.accepted:
                allowed = (self.prebuffer_s + (c.clock.now() - self.t0)) * aud.BYTES_PER_SECOND
                while self.sent < len(self.pcm) and self.sent <= allowed:
                    chunk = self.pcm[self.sent:self.sent + aud.CHUNK_BYTES]
                    c.transport.send_binary(aud.pack(aud.KIND_PLAYBACK, self.stream_no, self.seq, chunk))
                    self.sent, self.seq = self.sent + len(chunk), self.seq + 1
                if self.sent >= len(self.pcm) and not self.end_sent:
                    c.transport.send_binary(aud.pack(aud.KIND_PLAYBACK, self.stream_no, self.seq, b"", end=True))
                    self.end_sent = True
        except BaseException:
            self._release()
            raise
        return False

    def cancel(self) -> None:
        """Barge-in: stop the speaker now. Best effort; the stream is released either way."""
        try:
            if not self.finished:
                self.c.stop_playback(self.stream_id, timeout_s=2.0)
        except Exception:
            pass
        finally:
            self._release()


class LoopbackTransport(DeviceTransport):
    """Connects a DeviceClient directly to a SimulatedDevice in-process."""

    def __init__(self, device, secure: bool = False):
        """secure mimics a TLS session: keys rotation and binary audio are only honored when True."""
        self.device, self.secure, self._inbox, self._binbox = device, secure, [], []

    def _take(self, outputs) -> None:
        from simulator.device import BinaryFrame
        for o in outputs:
            (self._binbox if isinstance(o, BinaryFrame) else self._inbox).append(bytes(o))

    def send(self, raw: bytes) -> None:
        self._take(self.device.receive(raw, secure=self.secure))

    def send_binary(self, raw: bytes) -> None:
        self._take(self.device.receive_binary(raw, secure=self.secure))

    def receive(self) -> list[bytes]:
        self._take(self.device.tick())  # device runs on its own clock
        out, self._inbox = self._inbox, []
        return out

    def receive_binary(self) -> list[bytes]:
        out, self._binbox = self._binbox, []
        return out
