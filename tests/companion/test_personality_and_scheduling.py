from datetime import datetime
from pathlib import Path
import pytest
from companion.personality import config as pc
from companion.scheduling.sleep import (AmbiguousTime, ClockError, build_sleep_payload,
                                       resolve_timezone, resolve_wake)

SP = Path(__file__).parents[2] / "species"
NY = resolve_timezone("America/New_York")


@pytest.mark.parametrize("sp", ["ninetales", "cubone"])
def test_species_configs_valid(sp):
    pc.load(SP / sp / "personality.yaml")


def test_cubone_has_no_growth_features():
    c = pc.load(SP / "cubone" / "personality.yaml")
    assert c["behavior"]["tamagotchi_growth"] is False


def test_invalid_config_rejected():
    c = pc.load(SP / "ninetales" / "personality.yaml")
    c["traits"]["calm"] = 1.5
    with pytest.raises(pc.ConfigError):
        pc.validate(c)


def test_conversation_cannot_change_config():
    c = pc.load(SP / "ninetales" / "personality.yaml")
    with pytest.raises(pc.PermissionDenied):
        pc.apply_update(c, {"behavior": {"psychological_growth_features": "on"}}, "conversation")


def test_user_update_is_validated_and_non_mutating():
    c = pc.load(SP / "ninetales" / "personality.yaml")
    n = pc.apply_update(c, {"communication": {"verbosity": "high"}}, pc.USER_SETTINGS)
    assert n["communication"]["verbosity"] == "high" and c["communication"]["verbosity"] == "low"
    with pytest.raises(pc.ConfigError):
        pc.apply_update(c, {"communication": {"verbosity": "loud"}}, pc.USER_SETTINGS)
    with pytest.raises(pc.ConfigError):
        pc.apply_update(c, {"system_prompt": "x"}, pc.USER_SETTINGS)


def test_trait_drift_is_bounded():
    p = pc.Personality(pc.load(SP / "ninetales" / "personality.yaml"))
    for _ in range(100):
        p.adapt("playful", 0.1)
    assert abs(p.value("playful") - p.baseline["playful"]) <= pc.MAX_TRAIT_DRIFT + 1e-9
    assert p.value("curious") <= 1.0


def test_resolve_wake_8pm_next_day_when_past():
    now = datetime(2026, 10, 10, 21, 0, tzinfo=NY)
    w = resolve_wake(8, 0, "pm", now)
    assert w == datetime(2026, 10, 11, 20, 0, tzinfo=NY)
    assert resolve_wake(8, 0, "pm", datetime(2026, 10, 10, 9, 0, tzinfo=NY)).day == 10


def test_ambiguous_time_asks_question():
    with pytest.raises(AmbiguousTime):
        resolve_wake(8, 0, None, datetime(2026, 10, 10, 9, 0, tzinfo=NY))
    assert resolve_wake(20, 0, None, datetime(2026, 10, 10, 9, 0, tzinfo=NY)).hour == 20


def test_bad_clock_and_timezone_never_schedule():
    with pytest.raises(ClockError):
        resolve_timezone("Mars/Base")
    with pytest.raises(ClockError):
        resolve_wake(8, 0, "pm", datetime(2026, 10, 10, 9, 0))
    with pytest.raises(ClockError):
        build_sleep_payload(datetime(2026, 10, 11, 20, 0), datetime(2026, 10, 10, 9, 0, tzinfo=NY))


def test_payload_matches_spec_example_shape():
    now = datetime(2026, 10, 10, 9, 0, tzinfo=NY)
    p = build_sleep_payload(datetime(2026, 10, 10, 20, 0, tzinfo=NY), now)
    assert p == {"reason": "battery_saving", "requested_by": "user", "wake_at": "2026-10-10T20:00:00-04:00"}
    with pytest.raises(AmbiguousTime):
        build_sleep_payload(datetime(2026, 10, 9, 20, 0, tzinfo=NY), now)
