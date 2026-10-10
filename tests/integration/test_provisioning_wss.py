"""End to end: mint device key -> bundle -> provision simulated robot -> connect over real TLS ->
rotate -> restart both sides -> revoke."""
import json
import time

import pytest

from companion.application.clock import SystemClock
from companion.application.device_client import CommandRejected, CommandTimeout, DeviceClient, LoopbackTransport
from companion.application.ws_transport import WebSocketHostTransport, client_ssl_context, server_ssl_context
from companion.security import SecretStore, rotate_device_key
from protocol import Authenticator, KeyRing, ProtocolError, build, encode
from simulator import SimulatedDevice, Storage
from simulator.ws_link import DeviceLink
from tests.integration.test_wss import pki, wait_for  # noqa: F401  (fixtures/helpers)

CLOCK = SystemClock()
DID = "robot-01"


@pytest.fixture
def world(tmp_path, pki):
    store = SecretStore(tmp_path / "keys.json", CLOCK)
    host = WebSocketHostTransport(server_ssl_context(str(pki["server_cert"]), str(pki["server_key"])),
                                  pre_auth_check=store.mac_gate(CLOCK), pre_auth_timeout_s=0.6)
    port = host.start()
    bundle = store.create_device(DID, pki["ca_cert"].read_text(), f"wss://localhost:{port}")
    flash = tmp_path / "flash.json"
    dev = SimulatedDevice(None, Storage(flash), CLOCK)
    assert not dev.provisioned
    dev.provision(bundle)
    dev.boot()
    link = DeviceLink(dev, bundle["url"], client_ssl_context(str(pki["ca_cert"])), tick_s=0.02,
                      backoff_base_s=0.05, backoff_cap_s=0.2)
    w = dict(store=store, host=host, bundle=bundle, dev=dev, link=link, flash=flash, tmp=tmp_path, pki=pki)
    yield w
    link.stop()
    host.stop()


def host_client(w):
    return DeviceClient(w["host"], w["store"].authenticator(DID, CLOCK), CLOCK)


def test_unprovisioned_device_is_inert_and_provisioning_is_one_shot(tmp_path):
    dev = SimulatedDevice(None, Storage(tmp_path / "f.json"), CLOCK)
    assert dev.receive(b"anything") == [] and not dev.provisioned
    b = SecretStore(tmp_path / "k.json", CLOCK).create_device(DID, "ca", "wss://h")
    dev.provision(b)
    assert dev.provisioned
    with pytest.raises(PermissionError):
        dev.provision(b)                                   # no silent key replacement
    dev.factory_reset()
    assert not dev.provisioned and dev.state.get("keys") is None
    dev.provision(b)


def test_provisioned_device_connects_and_store_records_it(world):
    assert world["store"].devices()[0]["provisioned_at"] is None
    world["link"].start()
    assert wait_for(lambda: world["host"].connected)
    assert world["store"].devices()[0]["provisioned_at"] is not None
    out = host_client(world).request("device.get_status", timeout_s=3)
    assert out.result["power_state"] == "AWAKE" and "key" not in json.dumps(out.result)


def test_other_robots_key_is_rejected_by_host_gate(world):
    other = world["store"].create_device("robot-02", "ca", "wss://h")
    world["link"].start()
    assert wait_for(lambda: world["host"].connected)
    imposter = SimulatedDevice(None, Storage(world["tmp"] / "imp.json"), CLOCK)
    imposter.provision({**other, "device_id": DID})        # right id, wrong key
    link2 = DeviceLink(imposter, world["bundle"]["url"], client_ssl_context(str(world["pki"]["ca_cert"])),
                       tick_s=0.02, backoff_base_s=0.05)
    link2.start()
    try:
        assert wait_for(lambda: world["host"].stats["preauth_rejected"] >= 1)
        assert world["host"].connected and world["host"].connections == 1   # real robot undisturbed
    finally:
        link2.stop()


def test_rotation_over_wss_switches_both_sides_and_survives_restart(world):
    world["link"].start()
    assert wait_for(lambda: world["host"].connected)
    c = host_client(world)
    old_key = world["bundle"]["key"]
    kid = rotate_device_key(c, world["store"], DID)
    assert kid == 2 and world["store"].ring(DID).kids() == [2]
    c.request("device.get_status", timeout_s=3)            # first host message under kid 2 retires it on the device
    assert wait_for(lambda: world["dev"].auth.ring.kids() == [2])
    # restart both sides from disk
    world["link"].stop()
    dev2 = SimulatedDevice(None, Storage(world["flash"]), CLOCK)
    assert dev2.auth.ring.kids() == [2] and dev2.auth.ring.sign_kid == 2
    store2 = SecretStore(world["tmp"] / "keys.json", CLOCK)
    assert store2.ring(DID).kids() == [2] and store2.ring(DID).key(2) != bytes.fromhex(old_key)
    # the original provisioning key is dead
    stale = Authenticator(KeyRing({1: bytes.fromhex(old_key)}, 1), CLOCK, DID).sign(build("device.get_status"))
    assert dev2.receive(encode(stale), secure=True) == [] and dev2.rejected_count == 1


def test_rotation_refused_without_tls_and_state_unchanged(world):
    dev, store = world["dev"], world["store"]
    dev.boot()
    c = DeviceClient(LoopbackTransport(dev), store.authenticator(DID, CLOCK), CLOCK)   # secure=False path
    kid, key = store.begin_rotation(DID)
    with pytest.raises(CommandRejected) as e:
        c.request("security.rotate_key", {"kid": kid, "key": key.hex()})
    assert e.value.code == "not_allowed" and dev.auth.ring.kids() == [1]


def test_device_rejects_bad_rotation_requests(world):
    dev, store = world["dev"], world["store"]
    link = world["link"]; link.start()
    assert wait_for(lambda: world["host"].connected)
    c = host_client(world)
    for payload, code in [({"kid": 5, "key": "ab" * 32}, "invalid_state"),          # skipping ids
                          ({"kid": 1, "key": "ab" * 32}, "invalid_payload"),        # schema: kid >= 2
                          ({"kid": 2, "key": "zz" * 32}, "invalid_payload")]:
        try:
            c.request("security.rotate_key", payload, timeout_s=2)
            pytest.fail("accepted")
        except CommandRejected as e:
            assert e.code == code
        except ProtocolError:                                                       # host-side schema check
            assert code == "invalid_payload"
    assert dev.auth.ring.kids() == [1]


def test_rotation_resumes_after_lost_completion(world):
    """Device took the new key but the host never saw the reply."""
    world["link"].start()
    assert wait_for(lambda: world["host"].connected)
    store, dev, link = world["store"], world["dev"], world["link"]
    kid, key = store.begin_rotation(DID)                     # host persisted kid 2, then crashed mid-send
    msg = store.authenticator(DID, CLOCK).sign(build("security.rotate_key", {"kid": kid, "key": key.hex()}))
    link.call(lambda: dev.receive(encode(msg), secure=True))  # device applies it; replies are discarded
    assert dev.auth.ring.sign_kid == 2
    assert store.ring(DID).sign_kid == 1                      # host still thinks rotation is pending
    c = host_client(world)
    assert rotate_device_key(c, store, DID) == 2             # resume: converges instead of wedging
    assert store.ring(DID).kids() == [2]
    c.request("device.get_status", timeout_s=3)
    assert wait_for(lambda: dev.auth.ring.kids() == [2])


def test_revoked_robot_cannot_reconnect(world):
    world["link"].start()
    assert wait_for(lambda: world["host"].connected)
    world["store"].revoke(DID)
    world["link"].stop()
    link = DeviceLink(world["dev"], world["bundle"]["url"], client_ssl_context(str(world["pki"]["ca_cert"])),
                      tick_s=0.02, backoff_base_s=0.05)
    before = world["host"].stats["preauth_rejected"]
    link.start()
    try:
        assert wait_for(lambda: world["host"].stats["preauth_rejected"] > before)
        assert not world["host"].connected
    finally:
        link.stop()
