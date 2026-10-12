import json
import time

import pytest

from companion.learning.memory_talk import MemoryTalk
from companion.personality import config as pc
from companion.personality.adaptation import DAILY_CAP, MAX_DRIFT, PersonalityAdapter

DAY = 86400.0


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t


def _cfg():
    return pc.load("species/ninetales/personality.yaml")


def make(tmp_path, clock=None, **kw):
    clock = clock or Clock()
    p = pc.Personality(_cfg())
    return PersonalityAdapter(tmp_path / "d.json", p, clock, None, **kw), p, clock


def test_history_before_first_sight_counts_for_nothing(tmp_path):
    a, p, _ = make(tmp_path)
    a.observe({"play": 500})
    assert p.drift["playful"] == 0.0


def test_activity_nudges_the_matching_trait_only(tmp_path):
    a, p, _ = make(tmp_path)
    a.observe({})
    a.observe({"play": 2})
    assert p.drift["playful"] == pytest.approx(0.008) and p.drift["curious"] == 0.0


def test_daily_cap_and_hard_ceiling(tmp_path):
    a, p, c = make(tmp_path)
    a.observe({})
    a.observe({"play": 1000})
    assert p.drift["playful"] == pytest.approx(DAILY_CAP)
    n = 1000
    for _ in range(60):                                       # a month of nonstop play never passes the ceiling
        c.t += DAY
        n += 1000
        a.observe({"play": n})
    assert p.drift["playful"] <= MAX_DRIFT + 1e-9 and p.value("playful") <= p.baseline["playful"] + MAX_DRIFT + 1e-9


def test_drift_is_never_negative_and_fades_when_unused(tmp_path):
    a, p, c = make(tmp_path)
    a.observe({})
    a.observe({"teach": 3})
    before = p.drift["curious"]
    for _ in range(30):
        c.t += DAY
        a.observe({"teach": 3})
    assert 0.0 <= p.drift["curious"] < before and all(v >= 0 for v in p.drift.values())
    assert p.drift["curious"] == 0.0


def test_persists_and_survives_a_damaged_file(tmp_path):
    a, p, c = make(tmp_path)
    a.observe({})
    a.observe({"play": 3})
    a2, p2, _ = make(tmp_path, c)
    assert p2.drift["playful"] == pytest.approx(p.drift["playful"])
    (tmp_path / "d.json").write_text("{not json")
    a3, p3, _ = make(tmp_path, c)
    assert all(v == 0 for v in p3.drift.values())


def test_stored_values_are_clamped_on_load(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"schema_version": 1, "enabled": True, "drift": {"playful": 9, "curious": -3},
                                                 "seen": {}, "day": None, "fade_day": None, "used": {}}))
    _, p, _ = make(tmp_path)
    assert p.drift["playful"] == MAX_DRIFT and p.drift["curious"] == 0.0


def test_off_and_reset_erase_drift_and_off_stays_off(tmp_path):
    a, p, c = make(tmp_path)
    a.observe({})
    a.observe({"play": 3})
    a.reset()
    assert all(v == 0 for v in p.drift.values())
    a.observe({"play": 6})
    assert p.drift["playful"] > 0
    a.set_enabled(False)
    a.observe({"play": 20})
    assert all(v == 0 for v in p.drift.values())
    a2, p2, _ = make(tmp_path, c)                              # a restart does not switch it back on
    a2.observe({"play": 40})
    assert not a2.enabled and all(v == 0 for v in p2.drift.values())


def test_baseline_follows_live_config_and_does_not_mutate_it(tmp_path):
    a, p, _ = make(tmp_path)
    cfg2 = pc.apply_update(_cfg(), {"traits": {"playful": 0.2}}, pc.USER_SETTINGS)
    p.cfg = cfg2
    assert p.value("playful") == pytest.approx(0.2)
    a.observe({})
    a.observe({"play": 5})
    assert cfg2["traits"]["playful"] == 0.2


def test_spoken_controls(tmp_path):
    a, p, c = make(tmp_path)
    a.observe({})
    for day in range(1, 5):                                   # a few days of play and teaching
        c.t += DAY
        a.observe({"play": 10 * day, "teach": 10 * day})
    t = MemoryTalk(memory=None, clock=c, adapter=a)
    assert "playful" in t.handle("how have you changed?").text
    assert "go back to exactly how" in t.handle("go back to how you were").text
    assert p.drift["playful"] > 0                              # asked, not done yet
    assert "back to how I started" in t.handle("yes").text
    assert all(v == 0 for v in p.drift.values())
    assert "haven't changed" in t.handle("have you changed").text
    assert "stay just as I am" in t.handle("stop changing").text and not a.enabled
    assert "change a little" in t.handle("you can start changing again").text and a.enabled
