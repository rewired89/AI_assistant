import pytest
from companion.application.device_client import (CommandRejected, CommandTimeout, backoff_delays)
from protocol import Authenticator, build, encode
from tests.conftest import SECRET


def test_accepted_is_not_completed(client, device, clock):
    out = client.request("animation.play", {"name": "happy"}, until="accepted")
    assert out.accepted and not out.completed
    clock.advance(2)
    client.pump()
    assert out.completed
    assert any(e["type"] == "animation.finished" for e in client.events)


def test_loop_runs_until_stopped(client, device, clock):
    out = client.request("animation.play", {"name": "idle", "loop": True}, until="accepted")
    clock.advance(60)
    client.pump()
    assert not out.completed
    client.request("animation.stop")
    assert out.completed


def test_priority_blocks_lower_and_interrupts_higher(client):
    client.request("animation.play", {"name": "idle", "loop": True, "priority": 8}, until="accepted")
    with pytest.raises(CommandRejected) as e:
        client.request("animation.play", {"name": "happy", "priority": 2})
    assert e.value.code == "busy"
    client.request("animation.play", {"name": "excited", "priority": 9}, until="accepted")
    assert any(e["payload"].get("interrupted") for e in client.events if e["type"] == "animation.finished")


def test_unauthenticated_commands_get_no_reply_and_no_effect(device, clock):
    bad = Authenticator(b"y" * 20, clock).sign(build("power.sleep", {"reason": "user_request", "requested_by": "user"}))
    assert device.receive(encode(bad)) == []
    assert device.receive(b"garbage") == []
    assert device.power.value == "AWAKE" and device.rejected_count == 2


def test_replayed_message_does_not_reexecute(device, clock):
    m = Authenticator(SECRET, clock).sign(build("animation.play", {"name": "happy"}))
    raw = encode(m)
    assert device.receive(raw)
    assert device.receive(raw) == []  # same nonce: dropped
    assert device.rejected_count == 1


def test_duplicate_message_id_replays_ack_without_reexecuting(device, clock):
    a = Authenticator(SECRET, clock)
    m = build("animation.play", {"name": "happy"}, message_id="dup-message-1")
    first = device.receive(encode(a.sign(m)))
    again = device.receive(encode(a.sign(m)))
    assert first and again
    started = device.animation["ends"]
    clock.advance(1)
    device.receive(encode(a.sign(m)))
    assert device.animation["ends"] == started  # not restarted
    assert all(b"animation" not in r or b"command" in r for r in again)


def test_unsupported_version_and_unknown_command(device, clock):
    a = Authenticator(SECRET, clock)
    m = build("animation.stop"); m["protocol_version"] = 2
    out = device.receive(encode(a.sign(m)))
    assert out and b"command.failed" in out[0]
    out = device.receive(encode(a.sign(build("gpio.write", {"pin": 1}))))
    assert b"unsupported_command" in out[0]


def test_set_config_validation_and_mute(client, device):
    client.request("audio.start_capture", {"capture_id": "c1", "max_seconds": 5})
    client.request("device.set_config", {"config": {"mic_enabled": False}})
    assert device.capture is None
    with pytest.raises(CommandRejected) as e:
        client.request("audio.start_capture", {"capture_id": "c2", "max_seconds": 5})
    assert e.value.code == "not_allowed"


def test_capture_times_out_on_device(client, device, clock):
    client.request("audio.start_capture", {"capture_id": "c1", "max_seconds": 3})
    clock.advance(4); client.pump()
    assert any(e["type"] == "audio.capture.stopped" and e["payload"]["reason"] == "timeout" for e in client.events)


def test_host_side_validation_blocks_bad_command_before_sending(client, device):
    from protocol import ProtocolError
    with pytest.raises(ProtocolError):
        client.send("display.set_brightness", {"level": 500})


def test_backoff_grows_and_caps():
    import random
    d = [next(g) for g in [backoff_delays(rng=random.Random(1))] for _ in range(1)]
    g = backoff_delays(jitter=0)
    seq = [next(g) for _ in range(8)]
    assert seq == [1, 2, 4, 8, 16, 30, 30, 30]


def test_get_status_roundtrip(client):
    out = client.request("device.get_status")
    assert out.result["power_state"] == "AWAKE" and out.result["battery_percent"] == 100
