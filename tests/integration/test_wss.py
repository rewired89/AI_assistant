import asyncio
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from companion.application.clock import SystemClock
from companion.application.device_client import CommandRejected, CommandTimeout, DeviceClient
from companion.application.ws_transport import WebSocketHostTransport, client_ssl_context, mac_gate, server_ssl_context
from companion.interfaces import TransportClosed
from protocol import Authenticator, MAX_MESSAGE_BYTES, build, encode
from simulator import PowerState as P, SimulatedDevice, Storage
from simulator.ws_link import DeviceLink
from tests.conftest import SECRET
from tools.gen_certs import generate

CLOCK = SystemClock()


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("tls"), ["localhost", "127.0.0.1"])


@pytest.fixture
def host(pki):
    t = WebSocketHostTransport(server_ssl_context(str(pki["server_cert"]), str(pki["server_key"])),
                               pre_auth_check=mac_gate(SECRET, CLOCK), pre_auth_timeout_s=0.6, rate_per_s=20, burst=30)
    t.start()
    yield t
    t.stop()


@pytest.fixture
def make_link(tmp_path, pki):
    links = []

    def _make(host_port, ca=None, url=None, name="a", battery=100):
        dev = SimulatedDevice(SECRET, Storage(tmp_path / f"{name}.json"), CLOCK, battery_percent=battery)
        dev.boot()
        link = DeviceLink(dev, url or f"wss://localhost:{host_port}", client_ssl_context(str(ca or pki["ca_cert"])),
                          tick_s=0.02, backoff_base_s=0.05, backoff_cap_s=0.2)
        link.start()
        links.append(link)
        return dev, link
    yield _make
    for l in links:
        l.stop()


def wait_for(cond, timeout=5.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def client_for(host):
    return DeviceClient(host, Authenticator(SECRET, CLOCK), CLOCK)


def test_authenticated_command_roundtrip_over_tls(host, make_link):
    dev, link = make_link(host.port)
    assert wait_for(lambda: host.connected)
    c = client_for(host)
    out = c.request("device.get_status", timeout_s=3)
    assert out.result["power_state"] == "AWAKE"
    out = c.request("animation.play", {"name": "happy"}, timeout_s=3)       # waits for real completion
    assert out.completed and any(e["type"] == "animation.finished" for e in c.events)


def test_announce_frame_reaches_host(host, make_link):
    make_link(host.port)
    assert wait_for(lambda: host.connected)
    c = client_for(host)
    assert wait_for(lambda: (c.pump() or True) and any(e["type"] == "battery.updated" for e in c.events))


def test_device_rejects_commands_signed_with_wrong_secret(host, make_link):
    dev, _ = make_link(host.port)
    assert wait_for(lambda: host.connected)
    bad = DeviceClient(host, Authenticator(b"w" * 20, CLOCK), CLOCK)
    with pytest.raises(CommandTimeout):
        bad.request("power.sleep", {"reason": "user_request", "requested_by": "user"}, timeout_s=0.5)
    assert dev.power is P.AWAKE and dev.rejected_count >= 1


def test_wrong_ca_cannot_connect(host, make_link, tmp_path):
    other = generate(tmp_path / "other", ["localhost"])
    _, link = make_link(host.port, ca=other["ca_cert"], name="b")
    assert wait_for(lambda: link.failures >= 2)
    assert isinstance(link.last_error, ssl.SSLError) and not host.connected


def test_hostname_mismatch_rejected(pki, make_link, tmp_path):
    # same trusted CA, but the server cert only names another host
    srv = generate(tmp_path / "srv", ["other.test"])
    h = WebSocketHostTransport(server_ssl_context(str(srv["server_cert"]), str(srv["server_key"])),
                               pre_auth_check=mac_gate(SECRET, CLOCK))
    h.start()
    try:
        _, link = make_link(h.port, ca=srv["ca_cert"], name="c")        # trusts the CA, but URL says localhost
        assert wait_for(lambda: link.failures >= 2)
        assert isinstance(link.last_error, ssl.SSLError) and "hostname" in str(link.last_error).lower()
        assert not h.connected
    finally:
        h.stop()


def test_plaintext_ws_to_tls_port_fails(host):
    async def go():
        with pytest.raises(Exception):
            async with connect(f"ws://localhost:{host.port}", open_timeout=2, proxy=None):
                pass
    asyncio.run(go())


def test_tls_minimum_version_enforced_on_server(pki):
    ctx = server_ssl_context(str(pki["server_cert"]), str(pki["server_key"]))
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


async def _raw(host, pki, fn):
    async with connect(f"wss://localhost:{host.port}", ssl=client_ssl_context(str(pki["ca_cert"])), open_timeout=3,
                       proxy=None) as ws:
        return await fn(ws)


def test_silent_client_is_dropped_after_preauth_timeout(host, pki):
    async def fn(ws):
        await asyncio.wait_for(ws.wait_closed(), 3)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, fn)) == 4001
    assert host.stats["preauth_timeout"] == 1


def test_garbage_and_forged_first_frame_closed(host, pki):
    async def garbage(ws):
        await ws.send("hello")
        await asyncio.wait_for(ws.wait_closed(), 3)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, garbage)) == 4003
    forged = Authenticator(b"f" * 20, CLOCK).sign(build("battery.updated", {"percent": 1, "power_state": "AWAKE", "warning": True}))

    async def forged_fn(ws):
        await ws.send(encode(forged).decode())
        await asyncio.wait_for(ws.wait_closed(), 3)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, forged_fn)) == 4003
    assert host.receive() == [] and host.stats["preauth_rejected"] == 2


def test_unauthenticated_client_cannot_displace_real_device(host, pki, make_link):
    make_link(host.port)
    assert wait_for(lambda: host.connected)
    before = host.connections

    async def fn(ws):
        await ws.send("evil")
        await asyncio.wait_for(ws.wait_closed(), 3)
    asyncio.run(_raw(host, pki, fn))
    assert host.connected and host.connections == before
    assert client_for(host).request("device.get_status", timeout_s=3).result


def test_authenticated_newcomer_replaces_stale_connection(host, pki, make_link):
    make_link(host.port, name="one")
    assert wait_for(lambda: host.connections == 1)
    make_link(host.port, name="two")
    assert wait_for(lambda: host.connections == 2 and host.stats["replaced"] == 1)
    assert host.connected


def test_oversize_frame_closes_connection(host, make_link, pki):
    ok = Authenticator(SECRET, CLOCK).sign(build("battery.updated", {"percent": 5, "power_state": "AWAKE", "warning": True}))

    async def fn(ws):
        await ws.send(encode(ok).decode())
        await ws.send("x" * (MAX_MESSAGE_BYTES + 100))
        await asyncio.wait_for(ws.wait_closed(), 3)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, fn)) == 1009


def test_binary_frame_before_auth_is_rejected(host, pki):
    async def fn(ws):
        await ws.send(b"\x01\x02\x03")
        await asyncio.wait_for(ws.wait_closed(), 3)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, fn)) == 4003
    assert host.stats["preauth_rejected"] == 1 and host.receive_binary() == []


def test_flood_is_rate_limited(host, pki):
    a = Authenticator(SECRET, CLOCK)

    async def fn(ws):
        for _ in range(400):
            m = a.sign(build("touch.detected", {"zone": "head"}))
            try:
                await ws.send(encode(m).decode())
            except Exception:
                break
        await asyncio.wait_for(ws.wait_closed(), 3)
        return ws.close_code
    assert asyncio.run(_raw(host, pki, fn)) == 1008
    assert host.stats["rate_limited"] == 1 and len(host.receive()) <= 60


def test_send_without_device_raises(host):
    with pytest.raises(TransportClosed):
        client_for(host).send("device.get_status")


def test_device_reconnects_after_host_restart_and_keeps_running_offline(pki, make_link, tmp_path):
    def new_host(port=0):
        t = WebSocketHostTransport(server_ssl_context(str(pki["server_cert"]), str(pki["server_key"])), port=port,
                                   pre_auth_check=mac_gate(SECRET, CLOCK), pre_auth_timeout_s=0.6)
        t.start()
        return t
    h1 = new_host()
    port = h1.port
    dev, link = make_link(port, name="r")
    assert wait_for(lambda: h1.connected)
    wake = (datetime.now(timezone.utc).astimezone() + timedelta(seconds=3)).isoformat(timespec="seconds")
    # sleep through a host outage; scheduled wake must still happen with no host and no Wi-Fi link
    client_for(h1).request("power.sleep", {"reason": "battery_saving", "requested_by": "user", "wake_at": wake}, timeout_s=3)
    assert wait_for(lambda: not h1.connected)
    h1.stop()
    assert wait_for(lambda: dev.power is P.AWAKE, timeout=6)
    h2 = new_host(port)
    try:
        assert wait_for(lambda: h2.connected, timeout=6)
        assert client_for(h2).request("device.get_status", timeout_s=3).result["power_state"] == "AWAKE"
    finally:
        h2.stop()


def test_sleeping_device_drops_link_and_button_wake_reconnects(host, make_link):
    dev, link = make_link(host.port)
    assert wait_for(lambda: host.connected)
    client_for(host).request("power.sleep", {"reason": "user_request", "requested_by": "user"}, timeout_s=3)
    assert wait_for(lambda: not host.connected and not link.connected)
    with pytest.raises(TransportClosed):
        client_for(host).send("device.get_status")
    link.call(lambda: dev.press_button("double"))
    assert wait_for(lambda: host.connected, timeout=4)
    assert client_for(host).request("device.get_status", timeout_s=3).result["power_state"] == "AWAKE"


def test_command_validation_errors_come_back_over_the_wire(host, make_link):
    make_link(host.port, battery=8)
    assert wait_for(lambda: host.connected)
    with pytest.raises(CommandRejected) as e:
        client_for(host).request("display.set_brightness", {"level": 90}, timeout_s=3)
    assert e.value.code == "unsafe"
