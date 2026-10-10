"""Binary audio over a real TLS loopback socket."""
import asyncio
import time

import pytest

from companion.application.clock import SystemClock
from companion.application.device_client import CommandRejected, DeviceClient
from companion.application.ws_transport import WebSocketHostTransport, mac_gate, server_ssl_context
from companion.interfaces import TransportClosed
from protocol import Authenticator, audio as a, build, encode
from tests.conftest import SECRET
from tests.integration.test_wss import (_raw, client_for, host, make_link, pki, wait_for)  # noqa: F401

CLOCK = SystemClock()


def ramp(seconds: float) -> bytes:
    return b"".join(((i * 37) & 0x7FFF).to_bytes(2, "little") for i in range(int(seconds * a.SAMPLE_RATE)))


def first_frame():
    return encode(Authenticator(SECRET, CLOCK).sign(build("battery.updated", {"percent": 5, "power_state": "AWAKE", "warning": True}))).decode()


def test_capture_roundtrip_over_tls_is_byte_exact(host, make_link):
    dev, link = make_link(host.port)
    assert wait_for(lambda: host.connected)
    c = client_for(host)
    cap = c.start_capture(max_seconds=5)
    pcm = ramp(0.8)
    for i in range(0, len(pcm), 3200):                       # 100 ms at a time, like an I2S read loop
        link.call(lambda i=i: dev.feed_mic(pcm[i:i + 3200]))
        time.sleep(0.01)
    c.stop_capture(cap)
    assert cap.pcm == pcm and cap.done and not cap.corrupt and c.audio_dropped == 0
    assert host.stats["audio_rate_limited"] == 0


def test_playback_over_tls_completes_in_real_time(host, make_link):
    dev, _ = make_link(host.port)
    assert wait_for(lambda: host.connected)
    c = client_for(host)
    pcm = ramp(0.6)
    t0 = time.time()
    out = c.play_pcm(pcm, style="calm", prebuffer_s=0.2)
    elapsed = time.time() - t0
    fin = next(iter(c.playback_results.values()))
    assert out.completed and fin["result"] == "completed" and fin["bytes_played"] == len(pcm)
    assert elapsed >= 0.35                                   # paced, not dumped at once
    assert dev.audio_rejected == 0


def test_stop_playback_over_tls(host, make_link):
    dev, _ = make_link(host.port)
    assert wait_for(lambda: host.connected)
    c = client_for(host)
    out = c.send("audio.play", {"stream_id": "s1", "stream_no": 7, "format": "pcm16_16k_mono"})
    assert wait_for(lambda: (c.pump() or True) and out.accepted)
    for seq in range(5):
        host.send_binary(a.pack(a.KIND_PLAYBACK, 7, seq, bytes(a.CHUNK_BYTES)))
    time.sleep(0.15)
    c.stop_playback("s1")
    assert wait_for(lambda: (c.pump() or True) and "s1" in c.playback_results)
    assert c.playback_results["s1"]["result"] == "interrupted" and out.completed


def test_unsolicited_device_audio_is_dropped_by_host(host, pki):
    async def fn(ws):
        await ws.send(first_frame())
        await ws.send(a.pack(a.KIND_CAPTURE, 5, 0, b"\x00\x00"))     # stream nobody opened
        await ws.send(b"not an audio frame")
        await ws.send(a.pack(a.KIND_PLAYBACK, 5, 0, b"\x00\x00"))    # wrong direction
        await asyncio.sleep(0.3)
    asyncio.run(_raw(host, pki, fn))
    c = client_for(host)
    c.pump()
    assert c.audio_dropped == 3 and not c.captures


def test_oversize_audio_frame_closes_connection(host, pki):
    async def fn(ws):
        await ws.send(first_frame())
        await ws.send(bytes(a.HEADER_SIZE + a.MAX_PAYLOAD + 1))
        await asyncio.wait_for(ws.wait_closed(), 3)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, fn)) == 1009
    assert host.stats["audio_oversize"] == 1


def test_audio_flood_is_rate_limited(host, pki):
    frame = a.pack(a.KIND_CAPTURE, 5, 0, bytes(a.MAX_PAYLOAD))

    async def fn(ws):
        await ws.send(first_frame())
        for _ in range(200):                                 # ~820 KB at once; limit is 96 KiB/s + 192 KiB burst
            try:
                await ws.send(frame)
            except Exception:
                break
        await asyncio.wait_for(ws.wait_closed(), 5)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, fn)) == 1008
    assert host.stats["audio_rate_limited"] == 1


def test_undrained_audio_queue_is_bounded(pki):
    h = WebSocketHostTransport(server_ssl_context(str(pki["server_cert"]), str(pki["server_key"])),
                               pre_auth_check=mac_gate(SECRET, CLOCK), pre_auth_timeout_s=0.6,
                               max_queued_audio_bytes=20_000)
    h.start()
    frame = a.pack(a.KIND_CAPTURE, 5, 0, bytes(a.MAX_PAYLOAD))

    async def fn(ws):
        await ws.send(first_frame())
        for _ in range(10):
            try:
                await ws.send(frame)
            except Exception:
                break
            await asyncio.sleep(0.05)
        await asyncio.wait_for(ws.wait_closed(), 5)
        return ws.close_code
    try:
        assert asyncio.run(_raw(h, pki, fn)) == 1013
        assert h.stats["audio_queue_full"] == 1
    finally:
        h.stop()


def test_send_binary_guards(host):
    with pytest.raises(TransportClosed):
        host.send_binary(a.pack(a.KIND_PLAYBACK, 1, 0, b"\x00\x00"))


def test_binary_to_sleeping_device_is_not_received(host, make_link):
    dev, link = make_link(host.port)
    assert wait_for(lambda: host.connected)
    c = client_for(host)
    c.request("power.sleep", {"reason": "user_request", "requested_by": "user"}, timeout_s=3)
    assert wait_for(lambda: not host.connected)
    with pytest.raises(TransportClosed):
        host.send_binary(a.pack(a.KIND_PLAYBACK, 1, 0, b"\x00\x00"))
    assert dev.audio_rejected == 0


def test_send_binary_rejects_oversize_frames(host, make_link):
    make_link(host.port)
    assert wait_for(lambda: host.connected)
    with pytest.raises(ValueError):
        host.send_binary(bytes(a.HEADER_SIZE + a.MAX_PAYLOAD + 1))
