"""Firmware update over a real TLS loopback socket, including the device restarting and redialing."""
import pytest

from companion.application.clock import SystemClock
from companion.application.ota_updater import OtaError, OtaUpdater
from companion.application.ws_transport import WebSocketHostTransport, client_ssl_context, mac_gate, server_ssl_context
from companion.security.ota_signing import build_package, generate_signing_key, load_signing_key
from protocol.ota import CHUNK
from simulator import SimulatedDevice, Storage
from simulator.ota import OtaConfig
from simulator.ws_link import DeviceLink
from tests.conftest import SECRET
from tests.integration.test_wss import client_for, pki, wait_for  # noqa: F401

CLOCK = SystemClock()


@pytest.fixture
def host(pki):
    t = WebSocketHostTransport(server_ssl_context(str(pki["server_cert"]), str(pki["server_key"])),
                               pre_auth_check=mac_gate(SECRET, CLOCK), pre_auth_timeout_s=0.6, rate_per_s=200, burst=300)
    t.start()
    yield t
    t.stop()


@pytest.fixture
def rig(tmp_path, pki, host):
    key_path = tmp_path / "sign.key"
    pub = generate_signing_key(key_path)
    key = load_signing_key(key_path)
    made = []

    def _make(confirm_timeout_s=300.0):
        dev = SimulatedDevice(SECRET, Storage(tmp_path / "s.json"), CLOCK,
                              ota=OtaConfig(trusted_keys={1: pub}, slot_dir=tmp_path / "slots", confirm_timeout_s=confirm_timeout_s))
        dev.boot()
        link = DeviceLink(dev, f"wss://localhost:{host.port}", client_ssl_context(str(pki["ca_cert"])),
                          tick_s=0.02, backoff_base_s=0.05, backoff_cap_s=0.2)
        link.start()
        made.append(link)
        assert wait_for(lambda: host.connected)
        return dev, link, key, pub
    yield _make
    for l in made:
        l.stop()


def image(n, build):
    return bytes((i * 13 + build) & 0xFF for i in range(n))


def test_update_over_tls_survives_restart_and_commits(rig, host):
    dev, link, key, pub = rig()
    c = client_for(host)
    up = OtaUpdater(c, {1: pub})
    p = build_package(image(40 * CHUNK + 17, 2), key, product="nine-tails", board="sim-board", version="0.2.0", build=2)
    seen = []
    uid = up.push(p, on_progress=lambda a, b: seen.append(a), timeout_s=30)
    assert seen and dev.ota.state_name() == "verified"
    ready = up.apply(uid, 2, timeout_s=15)                    # socket drops, device redials, announces build 2
    assert ready["build"] == 2 and dev.fw_version == "0.2.0"
    assert wait_for(lambda: host.connected)
    up.confirm(uid, 2)
    assert c.request("device.get_status", timeout_s=3).result["ota"]["min_build"] == 2


def test_unconfirmed_update_rolls_back_without_the_host(rig, host):
    dev, link, key, pub = rig(confirm_timeout_s=0.8)
    c = client_for(host)
    up = OtaUpdater(c, {1: pub})
    p = build_package(image(3 * CHUNK, 2), key, product="nine-tails", board="sim-board", version="0.2.0", build=2)
    uid = up.push(p, timeout_s=15)
    up.apply(uid, 2, timeout_s=15)
    assert wait_for(lambda: dev.fw_version == "0.1.0", timeout=6)
    assert wait_for(lambda: host.connected)
    assert c.request("device.get_status", timeout_s=3).result["ota"]["last_rollback"]["reason"] == "confirm_timeout"
    with pytest.raises(OtaError):
        up.confirm(uid, 2)


def test_untrusted_package_never_leaves_the_host(rig, host, tmp_path):
    dev, link, key, pub = rig()
    c = client_for(host)
    up = OtaUpdater(c, {1: pub})
    generate_signing_key(tmp_path / "evil.key")
    evil = load_signing_key(tmp_path / "evil.key")
    p = build_package(image(CHUNK, 2), evil, product="nine-tails", board="sim-board", version="0.2.0", build=2)
    with pytest.raises(OtaError) as e:
        up.push(p)
    assert e.value.reason == "bad_package" and dev.ota.state_name() == "idle"
