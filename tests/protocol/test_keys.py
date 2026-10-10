import pytest
from protocol import Authenticator, KeyRing, ProtocolError, build, decode, encode, new_key
from tests.conftest import SECRET


def test_keyring_validation_and_repr_hides_keys():
    with pytest.raises(ValueError):
        KeyRing({1: b"short"}, 1)
    with pytest.raises(ValueError):
        KeyRing({1: SECRET}, 2)
    with pytest.raises(ValueError):
        KeyRing({0: SECRET}, 0)
    k = new_key()
    ring = KeyRing({1: k}, 1)
    assert k.hex() not in repr(ring) and len(k) == 32
    with pytest.raises(ValueError):
        ring.add(1, new_key())


def test_adopt_retires_older_and_notifies_once():
    calls = []
    ring = KeyRing({1: new_key()}, 1, on_change=lambda: calls.append(1))
    ring.add(2, new_key())
    ring.adopt(2)
    assert ring.kids() == [2] and ring.sign_kid == 2
    n = len(calls)
    ring.adopt(2)
    assert len(calls) == n  # no change, no write


def test_roundtrip_dict_preserves_keys():
    ring = KeyRing({1: new_key(), 2: new_key()}, 2)
    again = KeyRing.from_dict(ring.to_dict())
    assert again.kids() == [1, 2] and again.sign_kid == 2 and again.key(2) == ring.key(2)


def test_device_id_binding(clock):
    a = Authenticator(SECRET, clock, "robot-01")
    msg = a.sign(build("device.get_status"))
    assert msg["device_id"] == "robot-01"
    Authenticator(SECRET, clock, "robot-01").verify(msg)
    with pytest.raises(ProtocolError) as e:
        Authenticator(SECRET, clock, "robot-02").verify(msg)
    assert "device id" in e.value.message
    msg["device_id"] = "robot-02"            # tampering is caught by the MAC first
    with pytest.raises(ProtocolError):
        Authenticator(SECRET, clock, "robot-02").verify(msg)
    unbound = Authenticator(SECRET, clock).sign(build("device.get_status"))
    with pytest.raises(ProtocolError):       # a device-bound verifier refuses messages with no device id
        Authenticator(SECRET, clock, "robot-01").verify(unbound)


def test_unknown_kid_and_kid_tampering(clock):
    ring_a = KeyRing({1: new_key(), 2: new_key()}, 2)
    msg = Authenticator(ring_a, clock).sign(build("device.get_status"))
    only_one = KeyRing({1: ring_a.key(1)}, 1)
    with pytest.raises(ProtocolError) as e:
        Authenticator(only_one, clock).verify(msg)
    assert "key id" in e.value.message
    msg["auth"]["kid"] = 1                    # claim the old key: MAC no longer matches
    with pytest.raises(ProtocolError):
        Authenticator(ring_a, clock).verify(msg)


def test_rotation_adoption_both_directions(clock):
    k1, k2 = new_key(), new_key()
    host = Authenticator(KeyRing({1: k1, 2: k2}, 1), clock)      # host knows both, signs with 1
    dev = Authenticator(KeyRing({1: k1, 2: k2}, 2), clock)       # device already switched to 2
    reply = dev.sign(build("battery.updated", {"percent": 1, "power_state": "AWAKE", "warning": True}))
    assert host.verify(reply) == 2 and host.ring.sign_kid == 2 and host.ring.kids() == [2]
    nxt = host.sign(build("device.get_status"))
    assert nxt["auth"]["kid"] == 2
    assert dev.verify(nxt) == 2 and dev.ring.kids() == [2]
    old = Authenticator(KeyRing({1: k1}, 1), clock).sign(build("device.get_status"))
    with pytest.raises(ProtocolError):                           # retired key no longer accepted
        dev.verify(old)


def test_message_without_kid_is_kid_one(clock):
    a = Authenticator(SECRET, clock)
    m = a.sign(build("device.get_status"))
    # legacy sender: no kid field at all, MAC computed without it
    import hmac, hashlib, json
    legacy = {k: v for k, v in m.items() if k != "auth"}
    legacy["auth"] = {"nonce": "ab" * 8, "ts": int(clock()), "mac": ""}
    body = dict(legacy); body["auth"] = {"nonce": legacy["auth"]["nonce"], "ts": legacy["auth"]["ts"]}
    legacy["auth"]["mac"] = hmac.new(SECRET, json.dumps(body, separators=(",", ":"), sort_keys=True).encode(),
                                     hashlib.sha256).hexdigest()
    assert Authenticator(SECRET, clock).verify(decode(encode(legacy))) == 1
