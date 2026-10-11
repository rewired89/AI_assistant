"""Regenerate the firmware conformance vectors in protocol/vectors/.

    python -m tools.gen_vectors            # rewrite the files
    python -m tools.gen_vectors --check    # fail if the files are out of date (CI / tests use this)

Change the simulator on purpose, regenerate, and review the diff: that diff IS the change to the firmware contract."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from protocol import audio as aud
from protocol.vectors_lib import DIR, Builder, dump


def scenarios() -> list[Builder]:
    out: list[Builder] = []

    b = Builder("auth_drops_silently", "Anything that is not authentic gets NO reply at all (not even an error).")
    b.text("device.get_status", note="valid: answered")
    b.resend(0, note="same bytes again: replay, dropped silently")
    b.steps[-1]["silent"] = True
    b.text("device.get_status", tamper="mac", note="bad MAC: dropped", silent=True)
    b.text("device.get_status", tamper="payload", note="payload changed after signing: dropped", silent=True)
    b.text("device.get_status", tamper="device_id", note="signed for another device: dropped", silent=True)
    b.text("device.get_status", ts=b.t - 3600, note="timestamp an hour old: dropped", silent=True)
    b.text("device.get_status", ts=b.t + 3600, note="timestamp an hour ahead: dropped", silent=True)
    b.text("", raw="not json", note="garbage: dropped", silent=True)
    b.text("", raw="{}", note="empty object: dropped", silent=True)
    b.text("device.get_status", note="still alive afterwards")
    out.append(b)

    b = Builder("command_lifecycle", "Accepted is not done: animations complete later, loops until stopped, priority rules.")
    b.text("animation.play", {"name": "happy"}, note="accepted now")
    b.advance(0.5, "not finished yet")
    b.advance(3, "finished event arrives, then completion")
    b.text("animation.play", {"name": "idle", "loop": True, "priority": 8})
    b.advance(120, "a loop never finishes by itself")
    b.text("animation.play", {"name": "happy", "priority": 2}, note="lower priority is refused with busy")
    b.text("animation.play", {"name": "excited", "priority": 9}, note="higher priority interrupts")
    b.text("animation.stop")
    b.text("animation.play", {"name": "not_an_animation"}, note="schema violation is a failure, not a crash")
    b.text("no.such.command", {}, note="unknown command type")
    out.append(b)

    b = Builder("dedupe_retry", "The same message_id with a fresh nonce replays the cached answer and never runs twice.")
    b.text("display.set_brightness", {"level": 40}, mid="retry-me-0001")
    b.text("display.set_brightness", {"level": 70}, mid="retry-me-0001", note="same id, different payload: still the first answer")
    b.text("device.get_status", note="brightness is still 40")
    out.append(b)

    b = Builder("sleep_and_wake", "Sleep is silent radio: nothing is answered until a physical or scheduled wake.")
    b.text("power.sleep", {"reason": "user_request", "requested_by": "user"})
    b.text("device.get_status", note="asleep: no answer", silent=True)
    b.button("single", note="a single press does not wake it")
    b.button("double", note="a double press wakes it")
    b.text("device.get_status", note="awake again")
    b.text("power.sleep", {"reason": "scheduled", "requested_by": "system", "wake_at": "2020-01-01T00:00:00+00:00"},
           note="wake time in the past: refused")
    b.text("power.sleep", {"reason": "scheduled", "requested_by": "system", "wake_at": "2027-01-15T08:20:00+00:00"},
           note="valid scheduled sleep")
    b.advance(60, "too early: still asleep")
    b.advance(60 * 60 * 24 * 365, "wake time passed: it wakes by itself")
    out.append(b)

    b = Builder("battery_protection", "Firmware owns battery safety; the host cannot override it.", battery=100)
    b.battery(40)
    b.battery(19, "below 20%: warning flag, still AWAKE")
    b.battery(9, "at or below 10%: LOW_BATTERY, brightness capped to 30")
    b.text("display.set_brightness", {"level": 100}, note="request above the cap is refused by the device")
    b.text("device.get_status", note="status shows brightness at the cap and LOW_BATTERY")
    b.battery(12, "inside the 3% hysteresis band: stays LOW_BATTERY")
    b.text("device.get_status", note="still LOW_BATTERY")
    b.battery(14, "above the band: recovers")
    b.battery(4, "at or below 5%: critical, the device puts itself to sleep")
    b.text("device.get_status", note="asleep: silent", silent=True)
    out.append(b)

    b = Builder("mic_rules", "The mic can be muted remotely, never unmuted remotely; audio only over TLS.")
    b.text("audio.start_capture", {"capture_id": "cap-vec-1", "stream_no": 3, "max_seconds": 5}, secure=True, note="capture opens")
    b.mic(bytes(range(200)) * 8, note="1600 bytes arrive")
    b.text("audio.stop_capture", {"capture_id": "cap-vec-1"}, secure=True, note="end frame precedes the stopped event")
    b.text("device.set_config", {"config": {"mic_enabled": False}})
    b.text("audio.start_capture", {"capture_id": "cap-vec-2", "stream_no": 4, "max_seconds": 5}, secure=True,
           note="muted: refused")
    out.append(b)

    pcm = bytes(3200)
    play = {"stream_id": "play-vec-1", "stream_no": 7, "format": "pcm16_16k_mono"}
    b = Builder("playback_ok", "Playback: stream number comes from the signed command; frames strictly in order; completion is its own event.")
    b.text("audio.play", play, secure=True, note="opens stream 7")
    for i in range(3):
        b.binary(aud.pack(aud.KIND_PLAYBACK, 7, i, pcm), note=f"frame {i}")
    b.binary(aud.pack(aud.KIND_PLAYBACK, 7, 3, pcm, end=True), note="last frame carries END")
    b.advance(1.0, "audio plays out; finished then completed")
    b.text("device.get_status", note="idle again")
    out.append(b)

    b = Builder("playback_violations", "Bad audio is never played: wrong stream is ignored, a gap in sequence aborts the stream, audio frames on a plain channel are ignored.")
    b.text("audio.play", {**play, "stream_id": "play-vec-3"}, secure=True, note="opens stream 7")
    b.binary(aud.pack(aud.KIND_PLAYBACK, 7, 0, pcm), secure=False, note="audio frame on a plain channel: ignored")
    b.binary(aud.pack(aud.KIND_PLAYBACK, 9, 0, pcm), note="wrong stream number: ignored")
    b.binary(aud.pack(aud.KIND_PLAYBACK, 7, 0, pcm), note="frame 0 accepted")
    b.binary(aud.pack(aud.KIND_PLAYBACK, 7, 5, pcm), note="gap: stream aborted and reported")
    b.binary(aud.pack(aud.KIND_PLAYBACK, 7, 1, pcm), note="stream is gone: ignored")
    b.text("device.get_status", note="device is fine and idle")
    out.append(b)
    return out


def render() -> dict[str, str]:
    files = {}
    with tempfile.TemporaryDirectory() as t:
        for i, b in enumerate(scenarios()):
            d = Path(t) / str(i)
            d.mkdir()
            v = b.build(d)
            files[f"{b.name}.json"] = dump(v)
    return files


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    files = render()
    DIR.mkdir(exist_ok=True)
    stale = [n for n, s in files.items() if not (DIR / n).exists() or (DIR / n).read_text() != s]
    if check:
        if stale:
            print("out of date:", ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    for n, s in files.items():
        (DIR / n).write_text(s)
    print(f"wrote {len(files)} vectors to {DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
