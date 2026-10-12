import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from companion.checkin import DISCLOSURE, CheckIn
from companion.conversation import FakeLanguageModel
from tests.psychology.test_engine import Rig

D = 86400


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.t

    def at(self, h, m=0, day=0):
        self.t = (datetime(2026, 10, 12, h, m, tzinfo=timezone.utc) + timedelta(days=day)).timestamp()


def make(tmp_path, clock, with_psy=True, **kw):
    rig = Rig(tmp_path, clock, subdir="psy") if with_psy else None
    clock.at(8)                                              # Rig moves the clock; put it back
    ci = CheckIn(tmp_path / "checkin.json", clock, timezone.utc, psychology=rig.e if rig else None, **kw)
    return ci, rig


def say(ci, clock, text):
    clock.t += 5
    r = ci.handle(text)
    return r.text if r else None


def enable(ci, clock, h=19):
    say(ci, clock, "turn on daily check-ins")
    say(ci, clock, f"check in at {h - 12 if h > 12 else h} {'pm' if h >= 12 else 'am'}")


def ask(ci, clock, day=0):
    clock.at(19, 5, day)
    q = ci.question(clock())
    assert q
    ci.asked(clock())
    return q


def test_off_by_default_and_turning_it_on_says_what_it_is(tmp_path):
    ci, _ = make(tmp_path, Clock())
    clock = ci.clock
    clock.at(19, 5)
    assert not ci.due(clock()) and ci.question(clock()) is None
    text = say(ci, clock, "turn on daily check-ins")
    assert text == DISCLOSURE and "don't keep" in text and "turn it off" in text and ci.enabled


def test_once_a_day_in_the_window_and_missed_days_are_skipped(tmp_path):
    ci, _ = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    c.at(18, 59)
    assert not ci.due(c())
    ask(ci, c)
    c.at(20)
    assert not ci.due(c())                                   # already asked today: no second question, no reminder
    c.at(23, 30, day=1)
    assert not ci.due(c())                                   # the robot was off all evening: skipped
    c.at(19, 10, day=2)
    assert ci.due(c())


def test_declining_quiets_it_for_two_days_without_guilt(tmp_path):
    ci, _ = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    r = say(ci, c, "not now")
    assert "No problem" in r and "miss" not in r.lower()
    for day in (1, 2):
        c.at(19, 10, day)
        assert not ci.due(c())
    c.at(19, 10, 3)
    assert ci.due(c())


def test_ignoring_it_three_times_makes_it_quiet_for_two_weeks_silently(tmp_path):
    ci, _ = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    for day in range(3):
        ask(ci, c, day)
        c.at(19, 30, day)
        c.t += 11 * 60                                       # never answered
        ci.due(c())                                          # (the loop checks this every tick)
    c.at(19, 10, 3)
    assert not ci.due(c())
    c.at(19, 10, 16)
    assert ci.due(c())


def test_an_answer_resets_the_ignored_count(tmp_path):
    ci, _ = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    for day in (0, 1):
        ask(ci, c, day)
        c.t += 11 * 60
        ci.due(c())
    assert ci.ignored == 2
    ask(ci, c, 2)
    say(ci, c, "fine")
    assert ci.ignored == 0


def test_quiet_mode_pause_and_off(tmp_path):
    q = {"on": False}
    ci, _ = make(tmp_path, Clock(), quiet=lambda: q["on"])
    c = ci.clock
    enable(ci, c)
    c.at(19, 5)
    q["on"] = True
    assert not ci.due(c())
    q["on"] = False
    say(ci, c, "pause the check-ins")
    assert not ci.due(c())
    say(ci, c, "resume the check-ins")
    assert ci.due(c())
    say(ci, c, "stop the daily check-ins")
    assert not ci.due(c()) and not ci.enabled


def test_a_short_answer_gets_thanks_and_nothing_is_kept(tmp_path):
    ci, rig = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    assert say(ci, c, "pretty good") == "Thanks for telling me."
    assert rig.vault.list() == [] and not ci.waiting


def test_a_longer_answer_can_be_kept_in_her_own_words_with_her_yes(tmp_path):
    ci, rig = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    offer = say(ci, c, "I finished the painting I started last week and then went for a long walk")
    assert "private journal" in offer and "exactly what you said" in offer
    assert rig.vault.list() == []                            # offered, not saved
    rig.settings.acknowledge()
    r = say(ci, c, "yes")
    assert "notebook" in r                                   # the notebook is off: a second, explicit yes is needed
    assert rig.vault.list() == []
    r = rig.e.handle("yes")
    assert "Saved" in r.text
    rows = rig.vault.list()
    assert len(rows) == 1 and "finished the painting" in rows[0]["value"] and rows[0]["key"].startswith("checkin_2026-10-12")
    assert "thank" not in rows[0]["value"].lower() and "journal" not in rows[0]["value"].lower()   # only her words


def test_saying_no_keeps_nothing_and_the_words_are_forgotten(tmp_path):
    ci, rig = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    say(ci, c, "I had a long and honestly pretty ordinary day at the office today")
    assert "won't keep it" in say(ci, c, "no thanks")
    assert rig.vault.list() == [] and not ci.waiting
    assert "ordinary day" not in (tmp_path / "checkin.json").read_text()


def test_a_heavy_answer_offers_to_listen_and_never_asks_what_is_wrong(tmp_path):
    ci, rig = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    r = say(ci, c, "honestly it was a rough day")
    assert "just listen" in r and "what's wrong" not in r.lower() and "what happened" not in r.lower()
    assert rig.e.sess is None                                # nothing started yet
    r = say(ci, c, "yes please")
    assert rig.e.sess and rig.e.sess["kind"] in ("listen", "ack")          # listening (after the disclosure the first time)


def test_declining_the_listening_offer_leaves_it(tmp_path):
    ci, rig = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    say(ci, c, "it was a stressful day")
    assert "whenever you want" in say(ci, c, "no")
    assert rig.e.sess is None and rig.vault.list() == []


def test_not_bad_is_not_heavy(tmp_path):
    ci, _ = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    assert say(ci, c, "not bad") == "Thanks for telling me."


def test_an_unrelated_question_goes_to_normal_conversation(tmp_path):
    ci, _ = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    assert ci.handle("what's the weather like") is None and not ci.waiting


def test_without_the_reflection_helper_nothing_can_be_kept(tmp_path):
    ci, rig = make(tmp_path, Clock())
    rig.mode["feature"] = "off"
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    say(ci, c, "I did a lot of small things around the house and it felt good to finish them")
    assert "turned on" in say(ci, c, "yes") and rig.vault.list() == []


def test_the_settings_file_never_contains_anything_she_said(tmp_path):
    ci, _ = make(tmp_path, Clock())
    c = ci.clock
    enable(ci, c)
    ask(ci, c)
    say(ci, c, "my secret is that I painted a fox and I am proud of it today")
    raw = (tmp_path / "checkin.json").read_text()
    assert "secret" not in raw and "fox" not in raw
    assert set(json.loads(raw)) == {"schema_version", "enabled", "paused", "time", "declined_until", "snoozed_until", "ignored", "last_asked_day"}


def test_damaged_settings_stay_off(tmp_path):
    (tmp_path / "checkin.json").write_text("{nope")
    ci, _ = make(tmp_path, Clock())
    assert not ci.enabled


def test_a_safety_signal_in_the_answer_gets_the_scripted_reply_and_drops_the_check_in(tmp_path):
    clock = Clock()
    from companion.conversation import ConversationEngine
    from companion.personality import config as pc
    from companion.tamagotchi import CharacterState, load_rules
    from pathlib import Path
    sp = Path("species")
    cfg = pc.load(sp / "ninetales" / "personality.yaml")
    state = CharacterState(load_rules(sp / "ninetales" / "growth.yaml"), clock.t)
    ci, rig = make(tmp_path, clock)
    llm = FakeLanguageModel()
    eng = ConversationEngine(llm, lambda: cfg, state, clock, safety=rig.gate, psychology=rig.e, checkin=ci)
    enable(ci, clock)
    ask(ci, clock)
    r = eng.respond("honestly I want to kill myself")
    assert llm.calls == [] and "988" in r.text or "crisis" in r.text.lower() or "emergency" in r.text.lower()
    assert not ci.waiting
    eng.respond("turn on daily check-ins")
    assert llm.calls == []                                   # the whole check-in runs without a language model
