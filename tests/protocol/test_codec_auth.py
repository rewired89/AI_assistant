import json
import pytest
from protocol import (Authenticator, MAX_MESSAGE_BYTES, ProtocolError, build, check_version,
                      decode, encode, validate_payload)
from tests.conftest import SECRET


def signed(clock, mtype="animation.play", payload=None, **over):
    m = Authenticator(SECRET, clock).sign(build(mtype, {"name": "happy"} if payload is None else payload))
    m.update(over)
    return m


def test_roundtrip_and_payload_valid(clock):
    m = decode(encode(signed(clock)))
    Authenticator(SECRET, clock).verify(m)
    validate_payload(m, "command")


@pytest.mark.parametrize("payload", [{}, {"name": "dance"}, {"name": "happy", "x": 1},
                                     {"name": "happy", "priority": 99}])
def test_invalid_payloads_rejected(clock, payload):
    with pytest.raises(ProtocolError) as e:
        validate_payload(signed(clock, payload=payload), "command")
    assert e.value.code == "invalid_payload"


def test_unknown_type_and_direction(clock):
    with pytest.raises(ProtocolError):
        validate_payload(signed(clock, "gpio.write", {"pin": 4}), "command")
    with pytest.raises(ProtocolError):  # an event is not a valid command
        validate_payload(signed(clock, "button.pressed", {"button": "a", "kind": "single"}), "command")


def test_malformed_and_oversize(clock):
    for raw in (b"not json", b"[]", b"{}", b'{"protocol_version":1}'):
        with pytest.raises(ProtocolError) as e:
            decode(raw)
        assert e.value.code == "malformed"
    with pytest.raises(ProtocolError) as e:
        decode(b" " * (MAX_MESSAGE_BYTES + 1))
    assert e.value.code == "too_large"


def test_unsupported_version(clock):
    m = signed(clock)
    m["protocol_version"] = 2
    with pytest.raises(ProtocolError) as e:
        check_version(m)
    assert e.value.code == "unsupported_version"


def test_tampered_payload_fails_auth(clock):
    m = signed(clock)
    m["payload"]["name"] = "sad"
    with pytest.raises(ProtocolError) as e:
        Authenticator(SECRET, clock).verify(m)
    assert e.value.code == "auth_failed"


def test_wrong_secret_fails(clock):
    m = Authenticator(b"x" * 20, clock).sign(build("animation.stop"))
    with pytest.raises(ProtocolError):
        Authenticator(SECRET, clock).verify(m)


def test_replay_and_stale_timestamp(clock):
    m = signed(clock)
    a = Authenticator(SECRET, clock)
    a.verify(m)
    with pytest.raises(ProtocolError) as e:
        a.verify(m)
    assert e.value.code == "replay"
    old = signed(clock)
    clock.advance(31)
    with pytest.raises(ProtocolError):
        Authenticator(SECRET, clock).verify(old)


def test_short_secret_refused(clock):
    with pytest.raises(ValueError):
        Authenticator(b"short", clock)
