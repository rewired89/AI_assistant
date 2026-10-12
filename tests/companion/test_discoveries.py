import copy
import datetime as dt
import json
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml

from companion.discoveries import DailyInsights, load_dir, problems, script_for, seconds_for
from companion.discoveries.library import CATEGORIES, FORBIDDEN, Entry, _build

LIB = Path("content/discoveries")
TODAY = dt.date(2026, 10, 12)


def good(**kw):
    d = yaml.safe_load((LIB / "habit_formation_001.yaml").read_text())
    d.update(kw)
    return d


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.t

    def at(self, h, m=0, day=0):
        self.t = (datetime(2026, 10, 12, h, m, tzinfo=timezone.utc) + timedelta(days=day)).timestamp()


def mk(tmp_path, clock=None, **kw):
    clock = clock or Clock()
    entries, bad = load_dir(LIB, TODAY)
    assert not bad
    return DailyInsights(tmp_path / "i.json", entries, clock, rng=random.Random(1), **kw), clock


def enable(svc, h=17):
    svc.handle("turn on daily insights")
    svc.handle(f"set the insights time to {h} pm" if h > 12 else f"set the insights time to {h} am")


# ---------------------------------------------------------------- library
def test_shipped_library_is_valid_and_every_verified_entry_has_what_the_spec_requires():
    entries, bad = load_dir(LIB, TODAY)
    assert bad == {}
    verified = [e for e in entries if e.verified]
    assert len(verified) >= 6
    for e in verified:
        assert e.researcher and e.publication_year >= 1900 and e.source_url.startswith("https://") and e.limitations
        assert e.reflection_question.endswith("?") and e.everyday_example and e.verification["checked_on"]
        assert 30 <= seconds_for(script_for(e)) <= 90


def test_library_covers_several_topics_not_just_weaknesses():
    verified = {e.topic for e in load_dir(LIB, TODAY)[0] if e.verified}
    assert len(verified) >= 4


def test_drafts_stay_drafts_and_are_never_deliverable(tmp_path):
    entries, _ = load_dir(LIB, TODAY)
    drafts = [e for e in entries if not e.verified]
    assert drafts
    svc = DailyInsights(tmp_path / "i.json", entries, Clock())
    assert not set(svc.entries) & {e.id for e in drafts}


@pytest.mark.parametrize("change,needle", [
    ({"researcher": ""}, "researcher"), ({"publication_year": 3000}, "year"), ({"source_url": "http://x"}, "https"),
    ({"limitations": ""}, "limitations"), ({"reflection_question": "Think about it."}, "question"),
    ({"summary": "You always avoid this, which is a disorder."}, "must not use"),
    ({"everyday_example": "Your boyfriend would say so."}, "must not use"),
    ({"verification": {"status": "verified", "checked_on": "2026-10-12"}}, "checked_by"),
    ({"verification": {"status": "maybe"}}, "verification.status"),
    ({"topic": "politics"}, "topic"), ({"summary": "word " * 400}, "spoken length"),
])
def test_invalid_entries_are_rejected(change, needle):
    p = problems(good(**change), TODAY)
    assert p and any(needle in x for x in p)


def test_unverified_entry_may_be_incomplete_but_a_verified_one_may_not():
    d = good(researcher="", source_url="", verification={"status": "unverified"})
    assert problems(d, TODAY) == []


def test_invalid_file_is_dropped_not_delivered(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: x\n")
    (tmp_path / "ok.yaml").write_text(yaml.safe_dump(good()))
    entries, bad = load_dir(tmp_path, TODAY)
    assert [e.id for e in entries] == ["habit_formation_001"] and "bad.yaml" in bad


def test_script_follows_the_spec_structure():
    e = _build(good())
    s = script_for(e)
    for part in (e.hook, e.researcher, str(e.publication_year), e.everyday_example, e.reflection_question,
                 "You don't have to answer now"):
        assert part in s
    assert s.index(e.hook) < s.index(e.researcher) < s.index(e.everyday_example) < s.index(e.reflection_question)
    assert not FORBIDDEN.search(s)


# ---------------------------------------------------------------- delivery
def test_off_by_default_and_turning_on_explains_itself(tmp_path):
    svc, c = mk(tmp_path)
    c.at(18)
    assert not svc.due(c()) and svc.prepare(c()) is None
    text, *_ = svc.handle("turn on daily insights")
    assert "real research" in text and "skip" in text and "turn them off" in text
    assert svc.enabled


def test_one_per_day_inside_the_window_and_a_missed_day_is_skipped(tmp_path):
    svc, c = mk(tmp_path)
    enable(svc, 17)
    c.at(16, 59)
    assert not svc.due(c())
    c.at(17, 1)
    s = svc.prepare(c())
    assert s and s.entry.verified
    svc.commit(s, c(), spoken=True)
    c.at(18)
    assert not svc.due(c())                                   # already delivered today, no reminder
    c.at(23, 0, day=1)                                        # the robot was off all afternoon: window over
    assert not svc.due(c())
    c.at(19, 0, day=2)
    assert svc.due(c())


def test_ignoring_an_insight_changes_nothing(tmp_path):
    svc, c = mk(tmp_path)
    enable(svc)
    c.at(17, 5)
    s = svc.prepare(c()); svc.commit(s, c(), True)
    before = json.loads((tmp_path / "i.json").read_text())
    c.at(20, 0)                                               # hours later, no answer: nothing is sent or counted
    assert svc.prepare(c()) is None
    assert json.loads((tmp_path / "i.json").read_text()) == before


def test_quiet_pause_and_off(tmp_path):
    q = {"on": False}
    svc, c = mk(tmp_path, quiet=lambda: q["on"])
    enable(svc)
    c.at(17, 5)
    q["on"] = True
    assert not svc.due(c())
    q["on"] = False
    svc.handle("pause the insights")
    assert not svc.due(c())
    svc.handle("resume the insights")
    assert svc.due(c())
    svc.handle("stop the daily insights")
    assert not svc.due(c()) and not svc.enabled


def test_rotation_is_balanced_and_does_not_repeat_until_all_were_shown(tmp_path):
    svc, c = mk(tmp_path)
    enable(svc)
    shown, topics = [], []
    n = len(svc.entries)
    for day in range(n):
        c.at(17, 5, day)
        s = svc.prepare(c())
        svc.commit(s, c(), True)
        shown.append(s.entry.id)
        topics.append(s.entry.topic)
    assert len(set(shown)) == n                                # no repeat inside a round
    assert all(a != b for a, b in zip(topics, topics[1:]))     # never the same topic twice in a row while others remain
    c.at(17, 5, n)
    assert svc.prepare(c())                                    # next round starts


def test_topic_preference_is_hers_and_sensitive_topics_need_her_yes(tmp_path):
    svc, c = mk(tmp_path)
    enable(svc)
    svc.handle("only habits insights")
    for day in range(4):
        c.at(17, 5, day)
        s = svc.prepare(c()); svc.commit(s, c(), True)
        assert s.entry.topic == "habits_and_behavior_change"
    svc.handle("any topics for my insights")
    assert not svc.topics
    extra = _build(good(id="conflict_x_001", topic="relationships_and_conflict", sensitive_topic=True))
    s2 = DailyInsights(tmp_path / "j.json", [extra], Clock(), rng=random.Random(0))
    assert s2.pick() is None                                   # sensitive: not served by default
    s2.handle("include relationship topics")
    assert s2.pick().id == "conflict_x_001"


def test_state_file_holds_no_scores_and_none_of_her_words(tmp_path):
    svc, c = mk(tmp_path)
    enable(svc)
    c.at(17, 5)
    s = svc.prepare(c()); svc.commit(s, c(), True)
    svc.handle("my secret is that I love Vulpix, save that")
    raw = (tmp_path / "i.json").read_text()
    assert "Vulpix" not in raw and "secret" not in raw
    assert set(json.loads(raw)) == {"schema_version", "settings", "seen", "skipped", "saved", "cat_last", "last_scheduled_day", "inbox"}


def test_damaged_state_stays_off(tmp_path):
    (tmp_path / "i.json").write_text("{nope")
    svc, c = mk(tmp_path)
    assert not svc.enabled


# ---------------------------------------------------------------- follow-ups
def shown(svc, c):
    enable(svc)
    c.at(17, 5)
    s = svc.prepare(c()); svc.commit(s, c(), True)
    return s.entry


def test_sources_exercise_limits_skip_save_and_revisit(tmp_path):
    svc, c = mk(tmp_path)
    e = shown(svc, c)
    assert e.researcher in svc.handle("where did that come from")[0] and e.source_title in svc.handle("what's the source")[0]
    assert e.optional_exercise in svc.handle("give me a tiny exercise")[0]
    assert e.limitations in svc.handle("tell me more")[0]
    assert "Saved" in svc.handle("save that")[0] and e.id in svc.saved
    assert e.title in svc.handle("what insights have I saved")[0]
    c.t += 3600                                               # much later: "the source?" means nothing without context
    assert svc.handle("what's the source") is None
    assert e.researcher in svc.handle("revisit " + e.title)[0]
    assert "skipped" in svc.handle("skip")[0] and e.id in svc.skipped


def test_ordinary_speech_is_left_alone(tmp_path):
    svc, c = mk(tmp_path)
    shown(svc, c)
    c.t += 3600
    for t in ("what is the source of the Nile", "skip to the good part", "I want to eat", "tell me more about your day",
              "save the date", "next week is busy"):
        assert svc.handle(t) is None, t


def test_on_demand_and_topic_requests(tmp_path):
    svc, c = mk(tmp_path)
    t, style, _ = svc.handle("tell me a psychology discovery")
    assert "Today's psychology discovery" in t
    t2, *_ = svc.handle("another one")
    assert t2 != t
    t3, *_ = svc.handle("give me a discovery about emotions")
    assert "feeling" in t3.lower() or "emotion" in t3.lower()
    assert svc.handle("tell me a discovery about quantum gravity")[0].startswith("I don't have")


def test_mode_text_goes_to_inbox_and_time_parsing(tmp_path):
    svc, c = mk(tmp_path)
    svc.handle("turn on daily insights")
    assert "text" in svc.handle("send insights as text")[0] and svc.mode == "text"
    assert "6 pm" in svc.handle("change the insights time to 6 pm")[0].replace("6:00 PM", "6 pm").lower() or svc.time_hm == (18, 0)
    assert svc.time_hm == (18, 0)
    c.at(18, 5)
    s = svc.prepare(c()); svc.commit(s, c(), spoken=False)
    assert svc.inbox and svc.inbox[-1]["id"] == s.entry.id
    reloaded = DailyInsights(tmp_path / "i.json", load_dir(LIB, TODAY)[0], c)
    assert reloaded.enabled and reloaded.mode == "text" and reloaded.time_hm == (18, 0) and reloaded.inbox
