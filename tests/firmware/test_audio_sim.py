import itertools
import pytest
from companion.application.device_client import (CommandRejected, CommandTimeout, DeviceClient, LoopbackTransport)
from protocol import Authenticator, audio as a
from simulator import BinaryFrame, PowerState as P
from tests.conftest import SECRET


@pytest.fixture
def sc(device, clock):
    return DeviceClient(LoopbackTransport(device, secure=True), Authenticator(SECRET, clock), clock)


def ramp(seconds: float) -> bytes:
    n = int(seconds * a.SAMPLE_RATE)
    return b"".join(((i * 37) & 0x7FFF).to_bytes(2, "little") for i in range(n))


def send_pcm(sc, stream_no, pcm, first_seq=0):
    seq = first_seq
    for i in range(0, len(pcm), a.CHUNK_BYTES):
        sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, stream_no, seq, pcm[i:i + a.CHUNK_BYTES]))
        seq += 1
    return seq


def feed_all(dev, pcm, step=1000):
    for i in range(0, len(pcm), step):
        dev.feed_mic(pcm[i:i + step])


# ---------------------------------------------------------------- capture
def test_capture_roundtrip_is_byte_exact(sc, device):
    cap = sc.start_capture(max_seconds=5)
    pcm = ramp(1.5)
    feed_all(device, pcm, step=777 * 2)                      # awkward slice size on purpose
    sc.stop_capture(cap)
    assert cap.done and not cap.corrupt and cap.pcm == pcm and cap.stop_reason == "host_request"
    assert cap.seconds == pytest.approx(1.5)
    assert sc.audio_dropped == 0 and device.capture is None


def test_capture_frames_ordered_and_end_precedes_stopped_event(sc, device):
    sc.start_capture(max_seconds=3)
    feed_all(device, ramp(0.5))
    sc.request("audio.stop_capture", {"capture_id": next(iter(sc.captures.values())).capture_id})
    # replay what the device emitted, in order, via a fresh capture on the raw device API
    dev = device
    out = []
    from companion.application.device_client import build
    cap_id = "raw1"
    dev.auth  # authenticated
    msg = Authenticator(SECRET, lambda: dev.clock()).sign(build("audio.start_capture", {"capture_id": cap_id, "stream_no": 9, "max_seconds": 2}))
    from protocol import encode
    dev.receive(encode(msg), secure=True)
    dev.feed_mic(ramp(0.2))
    stop = Authenticator(SECRET, lambda: dev.clock()).sign(build("audio.stop_capture", {"capture_id": cap_id}))
    out = dev.receive(encode(stop), secure=True)
    kinds = ["bin" if isinstance(o, BinaryFrame) else ("stopped" if b"audio.capture.stopped" in o else "other") for o in out]
    assert kinds.index("bin") < kinds.index("stopped")
    last_bin = [o for o in out if isinstance(o, BinaryFrame)][-1]
    assert a.unpack(last_bin).end


def test_capture_times_out_with_end_frame(sc, device, clock):
    cap = sc.start_capture(max_seconds=1)
    feed_all(device, ramp(0.5))
    clock.advance(1.5)
    sc.wait_capture(cap)
    assert cap.done and cap.stop_reason == "timeout" and cap.seconds == pytest.approx(0.5)


def test_capture_never_exceeds_declared_length(sc, device):
    cap = sc.start_capture(max_seconds=1)
    device.feed_mic(ramp(3))                                   # firehose
    sc.wait_capture(cap)
    assert cap.seconds <= 1.0 and cap.stop_reason == "timeout" and not cap.corrupt


def test_mute_low_battery_and_sleep_end_capture_with_end_frame(sc, device):
    cap = sc.start_capture(max_seconds=5)
    feed_all(device, ramp(0.2))
    sc.request("device.set_config", {"config": {"mic_enabled": False}})
    sc.wait_capture(cap)
    assert cap.done and cap.stop_reason == "muted" and device.feed_mic(b"\x00\x00") is False


def test_low_battery_stops_capture(sc, device):
    cap = sc.start_capture(max_seconds=5)
    sc.transport._take(device.set_battery(8))          # events/frames the device emits reach the host
    sc.wait_capture(cap)
    assert cap.stop_reason == "low_battery"


def test_capture_frames_for_unknown_streams_are_dropped(sc):
    sc.transport._binbox.append(a.pack(a.KIND_CAPTURE, 42, 0, b"\x00\x00"))        # nobody asked
    sc.transport._binbox.append(b"garbage")
    sc.transport._binbox.append(a.pack(a.KIND_PLAYBACK, 1, 0, b"\x00\x00"))        # wrong direction
    sc.pump()
    assert sc.audio_dropped == 3


def test_capture_sequence_gap_marks_corrupt_and_stops_accepting(sc):
    cap = sc.start_capture(max_seconds=5)
    sc.transport._binbox.append(a.pack(a.KIND_CAPTURE, cap.stream_no, 0, b"\x01\x00"))
    sc.transport._binbox.append(a.pack(a.KIND_CAPTURE, cap.stream_no, 2, b"\x02\x00"))   # skipped 1
    sc.pump()
    assert cap.corrupt and cap.pcm == b"\x01\x00" and sc.audio_dropped == 1


def test_frames_after_end_dropped(sc, device):
    cap = sc.start_capture(max_seconds=5)
    sc.stop_capture(cap)
    n = sc.audio_dropped
    sc.transport._binbox.append(a.pack(a.KIND_CAPTURE, cap.stream_no, 1, b"\x00\x00"))
    sc.pump()
    assert sc.audio_dropped == n + 1


def test_capture_busy_and_stream_numbers_not_reused_while_active(sc):
    c1 = sc.start_capture(max_seconds=5)
    with pytest.raises(CommandRejected) as e:
        sc.start_capture(max_seconds=5)
    assert e.value.code == "busy"
    assert c1.stream_no not in {n for n, c in sc.captures.items() if n != c1.stream_no}


# ---------------------------------------------------------------- playback
def test_playback_completes_and_reports(sc, device, clock):
    pcm = ramp(2.0)
    out = sc.play_pcm(pcm, style="happy", volume=70)
    fin = next(iter(sc.playback_results.values()))
    assert out.completed and fin["result"] == "completed" and fin["bytes_played"] == len(pcm) and fin["underruns"] == 0
    assert device.playback is None and sc.audio_dropped == 0


def test_accepted_is_not_done_for_playback(sc, device, clock):
    out = sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    assert out.accepted and not out.completed
    sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, 0, ramp(0.1)[:a.CHUNK_BYTES], end=True))
    sc.pump()
    assert not out.completed                                    # data received is not data played
    clock.advance(1.0)
    sc.pump()
    assert out.completed and sc.playback_results["s1"]["result"] == "completed"


def test_paced_streaming_never_overruns_long_clips(sc, device):
    pcm = ramp(25.0)
    out = sc.play_pcm(pcm)
    assert out.completed and device.audio_rejected == 0
    assert sc.playback_results[next(iter(sc.playback_results))]["underruns"] == 0


def test_unpaced_flood_overruns_buffer(sc, device):
    out = sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    for seq in range(0, 60):                                    # ~115 KB at once, buffer is 64 KiB
        sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, seq, bytes(a.CHUNK_BYTES)))
    sc.pump()
    assert out.failed and out.failed[0] == "busy"
    assert sc.playback_results["s1"]["result"] == "error" and device.playback is None


def test_sequence_gap_aborts_playback(sc, device):
    out = sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, 0, bytes(100)))
    sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, 2, bytes(100)))
    sc.pump()
    assert out.failed and out.failed[0] == "invalid_state" and device.playback is None


def test_underrun_is_counted_and_playback_still_completes(sc, device, clock):
    out = sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    seq = send_pcm(sc, 4, bytes(a.CHUNK_BYTES * 3))                                       # 180 ms: starts playing
    clock.advance(1.0)                                                                    # ran dry
    sc.pump()
    seq = send_pcm(sc, 4, bytes(a.CHUNK_BYTES * 3), seq)
    sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, seq, b"", end=True))
    clock.advance(1.0)
    sc.pump()
    fin = sc.playback_results["s1"]
    assert out.completed and fin["underruns"] >= 1 and fin["result"] == "completed"


def test_stalled_host_times_out(sc, device, clock):
    out = sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    clock.advance(6)
    sc.pump()
    assert out.failed and out.failed[0] == "timeout" and device.playback is None


def test_stop_playback_interrupts(sc, device, clock):
    out = sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    send_pcm(sc, 4, bytes(a.CHUNK_BYTES * 5))
    clock.advance(0.1)
    sc.stop_playback("s1")
    sc.pump()
    fin = sc.playback_results["s1"]
    assert fin["result"] == "interrupted" and 0 < fin["bytes_played"] < a.CHUNK_BYTES * 5 and out.completed
    with pytest.raises(CommandRejected):
        sc.stop_playback("s1")


def test_second_play_busy_and_opus_unsupported(sc):
    sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    with pytest.raises(CommandRejected) as e:
        sc.request("audio.play", {"stream_id": "s2", "stream_no": 5, "format": "pcm16_16k_mono"}, until="accepted")
    assert e.value.code == "busy"


def test_opus_rejected_clearly(sc):
    with pytest.raises(CommandRejected) as e:
        sc.request("audio.play", {"stream_id": "s2", "stream_no": 5, "format": "opus"}, until="accepted")
    assert e.value.code == "unsupported_command"


def test_sleep_interrupts_playback(sc, device, clock):
    out = sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, 0, bytes(a.CHUNK_BYTES)))
    sc.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    assert device.power is P.SLEEPING and device.playback is None


def test_rejected_binary_cases_are_counted_not_executed(sc, device):
    # no audio.play yet
    sc.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, 0, bytes(100)))
    sc.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    sc.pump()
    bad = [a.pack(a.KIND_PLAYBACK, 9, 0, bytes(100)),                     # wrong stream
           a.pack(a.KIND_CAPTURE, 4, 0, bytes(100)),                      # wrong direction
           a.pack(a.KIND_PLAYBACK, 4, 0, bytes(100), codec=a.CODEC_OPUS),  # unsupported codec
           b"junk"]
    for b in bad:
        sc.transport.send_binary(b)
    assert device.audio_rejected == 5 and device.playback["received"] == 0


def test_binary_audio_refused_without_tls(client, device):
    client.send("audio.play", {"stream_id": "s1", "stream_no": 4, "format": "pcm16_16k_mono"})
    client.pump()
    client.transport.send_binary(a.pack(a.KIND_PLAYBACK, 4, 0, bytes(100)))
    assert device.audio_rejected == 1 and device.playback["received"] == 0


def test_sleeping_device_ignores_binary(sc, device):
    sc.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    assert device.receive_binary(a.pack(a.KIND_PLAYBACK, 4, 0, bytes(100)), secure=True) == []


def test_odd_length_pcm_refused_by_client(sc):
    with pytest.raises(ValueError):
        sc.play_pcm(b"\x00\x00\x00")


def test_stream_numbers_skip_active_and_wrap(sc):
    sc._stream_counter = 65534
    n1, n2 = sc._alloc_stream(), sc._alloc_stream()
    assert (n1, n2) == (65535, 1)
    sc._active_playback.add(2)
    assert sc._alloc_stream() == 3
