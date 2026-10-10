from datetime import datetime, timedelta, timezone
import json
import pytest
from simulator import InvalidTransition, PowerState as P, SimulatedDevice, Storage, Thresholds
from simulator.device import ALLOWED_TRANSITIONS
from companion.application.device_client import CommandRejected
from tests.conftest import SECRET


def wake_iso(clock, seconds):
    return datetime.fromtimestamp(clock.t + seconds, tz=timezone(timedelta(hours=-4))).isoformat(timespec="seconds")


def test_transition_table_is_closed():
    for s, targets in ALLOWED_TRANSITIONS.items():
        assert s not in targets
    assert ALLOWED_TRANSITIONS[P.SLEEPING] == {P.WAKING}
    assert ALLOWED_TRANSITIONS[P.CRITICAL_BATTERY] == {P.SLEEPING}


def test_invalid_transition_raises(device):
    with pytest.raises(InvalidTransition):
        device._go(P.WAKING)


def test_sleep_then_scheduled_wake_without_host(client, device, clock):
    client.request("power.sleep", {"reason": "battery_saving", "requested_by": "user",
                                   "wake_at": wake_iso(clock, 3600)})
    assert device.power is P.SLEEPING
    clock.advance(3599)
    assert device.tick() == []
    assert device.power is P.SLEEPING
    clock.advance(2)
    assert device.tick()
    assert device.power is P.AWAKE and device.state["wake_at"] is None


def test_schedule_survives_restart(make_device, client, device, clock):
    client.request("power.sleep", {"reason": "user_request", "requested_by": "user",
                                   "wake_at": wake_iso(clock, 600)})
    d2 = make_device()  # fresh process, same flash
    assert d2.power is P.SLEEPING and d2.state["wake_at"] is not None
    d2.boot("timer")  # spurious early timer wake: back to sleep
    assert d2.power is P.SLEEPING
    clock.advance(601)
    d2.boot("timer")
    assert d2.power is P.AWAKE


def test_double_press_wakes_without_wifi_single_does_not(client, device, clock):
    client.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    assert device.press_button("single") == []
    assert device.power is P.SLEEPING
    assert device.press_button("double")
    assert device.power is P.AWAKE


def test_commands_ignored_while_sleeping(client, device):
    client.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    from companion.application.device_client import CommandTimeout
    with pytest.raises(CommandTimeout):
        client.request("device.get_status", timeout_s=1)


@pytest.mark.parametrize("wake_at,why", [
    ("tomorrow", "ISO"), ("2030-01-01T08:00:00", "offset"),
    ("2001-01-01T08:00:00+00:00", "future"), ("2999-01-01T08:00:00+00:00", "days"),
])
def test_invalid_wake_at_rejected_and_state_unchanged(client, device, wake_at, why):
    with pytest.raises(CommandRejected) as e:
        client.request("power.sleep", {"reason": "battery_saving", "requested_by": "user", "wake_at": wake_at})
    assert e.value.code == "invalid_payload" and why in e.value.message
    assert device.power is P.AWAKE


def test_corrupted_state_recovers_to_defaults(tmp_path, clock):
    path = tmp_path / "state.json"
    path.write_text('{"state": {"power_state": "SLEEPING"}, "sha256": "bad"}')
    d = SimulatedDevice(SECRET, Storage(path), clock)
    assert d.recovered and d.power is P.AWAKE
    path.write_text("\x00garbage")
    assert SimulatedDevice(SECRET, Storage(path), clock).recovered


def test_battery_thresholds_and_hysteresis(device):
    out = device.set_battery(19)
    assert device.power is P.AWAKE and b'"warning":true' in out[0]
    device.set_battery(10)
    assert device.power is P.LOW_BATTERY and device.brightness <= 30
    device.set_battery(12)  # inside hysteresis band
    assert device.power is P.LOW_BATTERY
    device.set_battery(13)
    assert device.power is P.AWAKE


def test_low_battery_blocks_bright_screen_and_capture(client, device):
    device.set_battery(8)
    with pytest.raises(CommandRejected) as e:
        client.request("display.set_brightness", {"level": 90})
    assert e.value.code == "unsafe"
    with pytest.raises(CommandRejected):
        client.request("audio.start_capture", {"capture_id": "c1", "stream_no": 1, "max_seconds": 5})


def test_critical_battery_persists_and_shuts_down(make_device, device):
    device.set_battery(4)
    assert device.power is P.SLEEPING and device.state["sleep_reason"] == "critical_battery"
    assert device.state["wake_at"] is None
    d2 = make_device()
    assert d2.power is P.SLEEPING
    d2.battery = 50
    d2.boot("button")
    assert d2.power is P.AWAKE


def test_critical_boot_with_dead_battery_reshuts(make_device, device):
    device.set_battery(4)
    d2 = make_device(battery_percent=3)
    d2.boot("button")
    assert d2.power in (P.CRITICAL_BATTERY, P.SLEEPING)


def test_threshold_validation():
    with pytest.raises(ValueError):
        Thresholds(warn=5, low=10, critical=2)


def test_host_cannot_wake_critical_or_unmute(client, device):
    with pytest.raises(CommandRejected) as e:
        client.request("power.wake", {"reason": "host_request"})
    assert e.value.code == "invalid_state"
    with pytest.raises(Exception):
        client.send("device.set_config", {"config": {"mic_enabled": True}})
