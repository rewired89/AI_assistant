import copy
import os

import pytest

from companion.application.device_client import CommandRejected, DeviceClient, LoopbackTransport
from companion.application.ota_updater import OtaError, OtaUpdater
from companion.security.ota_signing import build_package, generate_signing_key, load_signing_key
from protocol import Authenticator, audio as aud
from protocol.ota import CHUNK
from simulator import SimulatedDevice, Storage
from simulator.ota import OtaConfig
from tests.conftest import SECRET

IMG = lambda n, tag=b"": (tag + bytes((i * 31 + 7) & 0xFF for i in range(n)))  # noqa: E731


@pytest.fixture
def keys(tmp_path):
    pub = generate_signing_key(tmp_path / "sign.key")
    return load_signing_key(tmp_path / "sign.key"), pub


def mk(tmp_path, clock, pub, **cfg):
    d = SimulatedDevice(SECRET, Storage(tmp_path / "state.json"), clock,
                        ota=OtaConfig(trusted_keys={1: pub}, slot_dir=tmp_path / "slots", **cfg))
    d.boot()
    return d


def link(dev, clock):
    c = DeviceClient(LoopbackTransport(dev, secure=True), Authenticator(SECRET, clock), clock)
    return c, OtaUpdater(c, dev.ota.cfg.trusted_keys)


def pkg(key, build=2, size=3 * CHUNK + 100, **kw):
    kw.setdefault("product", "nine-tails")
    kw.setdefault("board", "sim-board")
    return build_package(IMG(size, kw.pop("tag", b"")), key, version=kw.pop("version", f"0.{build}.0"), build=build, **kw)


@pytest.fixture
def env(tmp_path, clock, keys):
    key, pub = keys
    dev = mk(tmp_path, clock, pub)
    c, up = link(dev, clock)
    return dev, c, up, key, pub


def test_full_update_confirm_and_floor(env):
    dev, c, up, key, _ = env
    p = pkg(key)
    uid = up.push(p)
    assert dev.ota.state_name() == "verified"
    assert dev.ota.slots.read("A").startswith(b"NTFW")            # running slot untouched
    up.apply(uid, 2)
    assert dev.fw_version == "0.2.0" and dev.ota.state_name() == "trial"
    up.confirm(uid, 2)
    st = c.request("device.get_status").result
    assert st["build"] == 2 and st["ota"]["state"] == "idle" and st["ota"]["min_build"] == 2
    with pytest.raises(OtaError):                                  # cannot go back, even with a valid signature
        up.push(pkg(key, build=1))
    assert "secret" not in str(st).lower()


@pytest.mark.parametrize("size", [CHUNK, 2 * CHUNK, 5, 1])
def test_chunk_boundaries(env, size):
    dev, c, up, key, _ = env
    uid = up.push(pkg(key, size=size))
    assert dev.ota.state_name() == "verified"
    up.apply(uid, 2)
    up.confirm(uid, 2)


def test_progress_callback(env):
    _, _, up, key, _ = env
    seen = []
    up.push(pkg(key, size=20 * CHUNK), on_progress=lambda a, b: seen.append(a))
    assert seen and seen == sorted(seen)


def rejected(c, up, p, uid="upd1", stream=5, sig=None):
    with pytest.raises(CommandRejected) as e:
        c.request("ota.begin", {"update_id": uid, "stream_no": stream, "manifest": p.manifest, "signature": sig or p.signature})
    return e.value.code


def test_rejections(env, keys, tmp_path):
    dev, c, up, key, pub = env
    other = generate_signing_key(tmp_path / "evil.key")
    evil = load_signing_key(tmp_path / "evil.key")
    assert rejected(c, up, pkg(evil)) == "not_allowed"                     # untrusted key
    p = pkg(key)
    t = copy.deepcopy(p.manifest); t["size"] += 1
    with pytest.raises(CommandRejected):
        c.request("ota.begin", {"update_id": "updx", "stream_no": 5, "manifest": t, "signature": p.signature})
    assert rejected(c, up, pkg(key, product="other")) == "not_allowed"
    assert rejected(c, up, pkg(key, board="other")) == "not_allowed"
    assert rejected(c, up, pkg(key, build=1)) == "not_allowed"
    assert rejected(c, up, pkg(key, requires_build=5)) == "invalid_state"
    assert rejected(c, up, pkg(key, size=2_000_000)) == "invalid_payload"
    assert dev.ota.state_name() == "idle"


def test_unsigned_channel_refused(env, clock):
    dev, _, _, key, _ = env
    c = DeviceClient(LoopbackTransport(dev, secure=False), Authenticator(SECRET, clock), clock)
    p = pkg(key)
    with pytest.raises(CommandRejected):
        c.request("ota.begin", {"update_id": "updx", "stream_no": 5, "manifest": p.manifest, "signature": p.signature})


def test_battery_and_power_gates(env):
    dev, c, up, key, _ = env
    dev.set_battery(39)
    assert rejected(c, up, pkg(key)) == "unsafe"
    dev.set_battery(100)
    uid = up.push(pkg(key))
    dev.set_battery(29)
    with pytest.raises(CommandRejected) as e:
        c.request("ota.apply", {"update_id": uid})
    assert e.value.code == "unsafe"


def test_low_battery_aborts_transfer(env):
    dev, c, up, key, _ = env
    p = pkg(key, size=10 * CHUNK)
    c.request("ota.begin", {"update_id": "upd1", "stream_no": 5, "manifest": p.manifest, "signature": p.signature})
    for i in range(2):
        c.transport.send_binary(aud.pack(aud.KIND_OTA, 5, i, p.image[i * CHUNK:(i + 1) * CHUNK], codec=aud.CODEC_RAW))
    c.transport._take(dev.set_battery(20))
    c.pump()
    assert any(e["type"] == "ota.failed" and e["payload"]["reason"] == "low_battery" for e in up.events)
    assert dev.ota.state_name() == "idle" and dev.ota.slots.read("B") == b""


def test_audio_blocked_while_receiving(env):
    dev, c, up, key, _ = env
    p = pkg(key)
    c.request("ota.begin", {"update_id": "upd1", "stream_no": 5, "manifest": p.manifest, "signature": p.signature})
    with pytest.raises(CommandRejected) as e:
        c.start_capture()
    assert e.value.code == "busy"


def begin(c, p, uid="upd1", stream=5):
    return c.request("ota.begin", {"update_id": uid, "stream_no": stream, "manifest": p.manifest, "signature": p.signature}).result


def frame(p, i, stream=5, end=None, payload=None):
    data = p.image[i * CHUNK:(i + 1) * CHUNK] if payload is None else payload
    last = (i + 1) * CHUNK >= len(p.image) if end is None else end
    return aud.pack(aud.KIND_OTA, stream, i, data, end=last, codec=aud.CODEC_RAW)


def failed_reason(up):
    return next((e["payload"]["reason"] for e in up.events if e["type"] == "ota.failed"), None)


def test_data_errors(env):
    dev, c, up, key, _ = env
    p = pkg(key)
    for bad, reason in [
        (lambda: frame(p, 1), "bad_chunk"),                                       # skipped seq
        (lambda: frame(p, 0, end=True, payload=p.image[:100]), "size_mismatch"),  # END too early
        (lambda: frame(p, 0, payload=p.image[:100], end=False), "bad_chunk"),     # short non-final chunk
    ]:
        up.events.clear()
        begin(c, p)
        c.transport.send_binary(bad())
        c.pump()
        assert failed_reason(up) == reason, reason
        assert dev.ota.state_name() == "idle"
    up.events.clear()
    begin(c, p)                                                                   # wrong stream: ignored, not fatal
    before = dev.audio_rejected
    c.transport.send_binary(frame(p, 0, stream=9))
    assert dev.audio_rejected == before + 1 and dev.ota.state_name() == "receiving"


def test_hash_mismatch(env):
    dev, c, up, key, _ = env
    p = pkg(key)
    begin(c, p)
    img = bytearray(p.image); img[10] ^= 1
    q = type(p)(p.manifest, p.signature, bytes(img))
    for i in range(4):
        c.transport.send_binary(frame(q, i))
    c.pump()
    assert failed_reason(up) == "hash_mismatch" and dev.ota.slots.read("B") == b""


def test_frames_without_begin_ignored(env):
    dev, c, up, key, _ = env
    c.transport.send_binary(frame(pkg(key), 0))
    assert dev.ota.state_name() == "idle"


def test_resume_after_power_loss(env, tmp_path, clock):
    dev, c, up, key, pub = env
    p = pkg(key, size=10 * CHUNK + 5)
    begin(c, p)
    for i in range(6):
        c.transport.send_binary(frame(p, i))
    c.pump()
    dev2 = SimulatedDevice(SECRET, Storage(tmp_path / "state.json"), clock,
                           ota=OtaConfig(trusted_keys={1: pub}, slot_dir=tmp_path / "slots"))
    dev2.boot()                                                                   # power cut, restart
    c2, up2 = link(dev2, clock)
    res = begin(c2, p, stream=6)
    assert res["resume_from"] == 4 * CHUNK                                        # last persisted progress mark
    uid = up2.push(p, update_id="upd1")
    assert dev2.ota.state_name() == "verified"
    up2.apply(uid, 2)
    up2.confirm(uid, 2)


def test_verified_survives_restart(env, tmp_path, clock):
    dev, c, up, key, pub = env
    uid = up.push(pkg(key))
    dev2 = SimulatedDevice(SECRET, Storage(tmp_path / "state.json"), clock,
                           ota=OtaConfig(trusted_keys={1: pub}, slot_dir=tmp_path / "slots"))
    dev2.boot()
    c2, up2 = link(dev2, clock)
    assert dev2.ota.state_name() == "verified"
    up2.apply(uid, 2)


def test_abort(env):
    dev, c, up, key, _ = env
    p = pkg(key)
    begin(c, p)
    up.abort("upd1")
    assert dev.ota.state_name() == "idle"
    with pytest.raises(CommandRejected):
        up.abort("upd1")


# ---------------------------------------------------------------- trial and rollback
def test_unconfirmed_update_rolls_back_by_itself(env, clock):
    dev, c, up, key, _ = env
    uid = up.push(pkg(key))
    up.apply(uid, 2)
    clock.advance(299)
    c.pump()
    assert dev.fw_version == "0.2.0"
    clock.advance(2)
    c.pump()
    assert dev.fw_version == "0.1.0" and dev.ota.summary()["last_rollback"]["reason"] == "confirm_timeout"
    assert any(e["type"] == "ota.rolled_back" for e in up.events)
    assert dev.ota.st["min_build"] == 1                                            # floor not raised
    with pytest.raises(CommandRejected):
        c.request("ota.confirm", {"update_id": uid})


@pytest.mark.parametrize("tag,reason", [(b"NT-FAIL-SELFTEST", "self_test_failed"), (b"NT-BOOTLOOP", "boot_failures")])
def test_bad_images_roll_back_on_boot(env, tag, reason):
    dev, c, up, key, _ = env
    uid = up.push(pkg(key, tag=tag))
    with pytest.raises(OtaError):
        up.apply(uid, 2)
    assert dev.fw_version == "0.1.0" and dev.ota.summary()["last_rollback"]["reason"] == reason
    assert dev.ota.slots.read("B") == b""
    up.push(pkg(key, build=3))                                                      # device is usable and updatable again


def test_repeated_power_cuts_during_trial_roll_back(env, tmp_path, clock):
    dev, c, up, key, pub = env
    uid = up.push(pkg(key))
    up.apply(uid, 2)
    d = dev
    for _ in range(3):
        d = SimulatedDevice(SECRET, Storage(tmp_path / "state.json"), clock,
                            ota=OtaConfig(trusted_keys={1: pub}, slot_dir=tmp_path / "slots"))
        d.boot()
    assert d.fw_version == "0.1.0" and d.ota.summary()["last_rollback"]["reason"] == "boot_failures"


def test_confirm_rules(env):
    dev, c, up, key, _ = env
    uid = up.push(pkg(key))
    up.apply(uid, 2)
    with pytest.raises(CommandRejected):
        c.request("ota.confirm", {"update_id": "nope"})
    up.confirm(uid, 2)
    assert c.request("ota.confirm", {"update_id": uid}).result["already"] is True   # idempotent
    with pytest.raises(CommandRejected):
        c.request("ota.confirm", {"update_id": "other"})


def test_begin_refused_during_trial(env):
    dev, c, up, key, _ = env
    up.apply(up.push(pkg(key)), 2)
    assert rejected(c, up, pkg(key, build=3)) == "invalid_state"


def test_state_survives_corruption_as_factory(env, tmp_path, clock, keys):
    dev, c, up, key, pub = env
    up.confirm(*(lambda u: (up.apply(u, 2) and u, 2))(up.push(pkg(key))))
    (tmp_path / "state.json").write_bytes(b"garbage")
    for f in (tmp_path / "state.json.bak", tmp_path / "state.json.tmp"):
        if f.exists():
            f.unlink()
    d = SimulatedDevice(SECRET, Storage(tmp_path / "state.json"), clock,
                        ota=OtaConfig(trusted_keys={1: pub}, slot_dir=tmp_path / "slots"))
    d.boot()
    assert d.recovered and d.ota.st["min_build"] >= 1 and d.fw_version == "0.1.0"
