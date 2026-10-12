import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from companion.conversation import FakeLanguageModel
from companion.learning import LearnConfig, MemoryTalk, RoutineLearner
from companion.learning.routines import KEY_CHAT, KEY_GREETING, part_of_day
from companion.memory import MemoryStore, ReflectionVault
from companion.psychology.safety import SafetyGate
from tests.companion.test_conversation import make

UTC = timezone.utc
D = 86400
BASE = datetime(2027, 1, 4, 0, 0, tzinfo=UTC).timestamp()             # a Monday, midnight UTC


def at(day: int, hour: int) -> float:
    return BASE + day * D + hour * 3600


@pytest.fixture
def mem(tmp_path, clock):
    return MemoryStore(tmp_path / "m.db", "her", clock)


@pytest.fixture
def learner(tmp_path, clock, mem):
    return RoutineLearner(tmp_path / "r.json", clock, UTC, mem)


def chats(learner, days_hours, text=None):
    for d, h in days_hours:
        learner.record_chat(text, at(d, h))


# ---------------------------------------------------------------- evidence rules
def test_one_busy_day_is_not_a_routine(learner):
    chats(learner, [(0, 19)] * 30)
    assert learner.candidates(at(1, 0)) == []


def test_enough_different_days_in_the_same_part_of_day_make_a_candidate(learner):
    chats(learner, [(0, 19), (1, 20), (2, 18), (4, 21), (5, 19), (6, 9)])         # 5 evenings, 1 morning, over 6 days
    (c,) = learner.candidates(at(7, 0))
    assert c["key"] == KEY_CHAT and "evening" in c["value"] and c["days"] == 5


def test_a_spread_out_week_is_not_a_routine(learner):
    chats(learner, [(0, 8), (1, 13), (2, 19), (3, 8), (4, 13), (5, 19), (6, 23)])
    assert learner.candidates(at(7, 0)) == []


def test_old_evidence_falls_out_of_the_window(learner):
    chats(learner, [(0, 19), (1, 19), (2, 19), (3, 19), (4, 19), (5, 19)])
    assert learner.candidates(at(6, 0))
    assert learner.candidates(at(40, 0)) == []                                       # 21-day window long past


def test_part_of_day_buckets_follow_the_local_clock(tmp_path, clock, mem):
    assert [part_of_day(h) for h in (4, 5, 10, 11, 16, 17, 21, 22, 0)] == ["night", "morning", "morning", "afternoon", "afternoon", "evening", "evening", "night", "night"]
    ny = RoutineLearner(tmp_path / "ny.json", clock, ZoneInfo("America/New_York"), mem)
    ny.record_chat(None, datetime(2027, 1, 5, 23, 30, tzinfo=UTC).timestamp())        # 18:30 in New York
    assert ny.d["obs"][0][2] == "evening"


def test_only_the_time_and_a_greeting_word_are_kept_never_what_she_said(learner, tmp_path):
    learner.record_chat("hello, my secret password is swordfish and I am scared", at(0, 9))
    raw = (tmp_path / "r.json").read_text()
    assert "swordfish" not in raw and "secret" not in raw and "scared" not in raw
    assert learner.d["obs"] == [[at(0, 9), "chat", "morning"], [at(0, 9), "greeting", "hello"]]


def test_greeting_routine_needs_repetition_over_days_and_a_clear_favourite(learner):
    for d in (0, 1, 2, 3):
        learner.record_chat("hey, how are you", at(d, 9))
    (c,) = [x for x in learner.candidates(at(4, 0)) if x["key"] == KEY_GREETING]
    assert "'hey'" in c["value"]
    mixed = RoutineLearner(learner.store.path.with_name("m2.json"), learner.clock, UTC, None)
    for d, w in enumerate(["hey", "hello", "hi", "hey", "hello", "hi"]):
        mixed.record_chat(f"{w} there", at(d, 9))
    assert [x for x in mixed.candidates(at(7, 0)) if x["key"] == KEY_GREETING] == []


def test_retention_and_size_limits(tmp_path, clock):
    l = RoutineLearner(tmp_path / "x.json", clock, UTC, None, LearnConfig(retention_days=10, max_observations=50))
    l.record_chat(None, at(0, 9))
    l.record_chat(None, at(30, 9))
    assert len(l.d["obs"]) == 1                                                       # the day-0 entry expired
    for i in range(80):
        l.record_chat(None, at(30, 9) + i)
    assert len(l.d["obs"]) <= 50


# ---------------------------------------------------------------- proposals are pending, asking, declining
def test_proposals_are_pending_inferences_not_facts(learner, mem):
    chats(learner, [(0, 19), (1, 20), (2, 18), (3, 21), (4, 19)])
    ids = learner.propose(at(5, 0))
    assert len(ids) == 1
    rec = mem.list("goal_habit")[0]
    assert rec["status"] == "pending" and rec["source"] == "ai_inference" and rec["key"] == KEY_CHAT
    assert mem.search("chat evening") == []                                           # pending memories are never used


def test_a_confirmed_routine_is_never_overwritten_by_a_new_inference(learner, mem):
    chats(learner, [(0, 19), (1, 20), (2, 18), (3, 21), (4, 19)])
    mid = learner.propose(at(5, 0))[0]
    mem.confirm(mid)
    chats(learner, [(10, 8), (11, 8), (12, 8), (13, 8), (14, 8), (15, 8), (16, 8), (17, 8), (18, 8), (19, 8), (20, 8)])
    learner.propose(at(21, 0))
    rec = [r for r in mem.list("goal_habit") if r["key"] == KEY_CHAT]
    assert len(rec) == 1 and rec[0]["status"] == "active" and "evening" in rec[0]["value"]


def test_a_question_is_asked_once_per_cooldown(learner):
    chats(learner, [(0, 19), (1, 20), (2, 18), (3, 21), (4, 19)])
    q = learner.next_question(at(5, 0))
    assert q and "evening" in q["question"] and q["key"] == KEY_CHAT
    assert learner.next_question(at(6, 0)) is None                                    # inside the 3-day cooldown
    assert learner.next_question(at(8.5, 0)) is not None                              # asked again later if still pending


def test_declining_silences_that_routine_for_a_month(learner, mem):
    chats(learner, [(0, 19), (1, 20), (2, 18), (3, 21), (4, 19)])
    q = learner.next_question(at(5, 0))
    mem.delete(q["id"])
    learner.decline(q["key"], at(5, 0))
    assert learner.next_question(at(10, 0)) is None and learner.propose(at(10, 0)) == []
    chats(learner, [(34, 19), (35, 19), (36, 19), (37, 19), (38, 19)])
    assert learner.propose(at(40, 0))                                                 # a month later it may come back


def test_turning_it_off_erases_the_evidence_and_stops_recording(learner, tmp_path):
    chats(learner, [(0, 19), (1, 19)])
    learner.set_enabled(False)
    assert learner.d["obs"] == []
    chats(learner, [(2, 19)])
    assert learner.d["obs"] == [] and json.loads((tmp_path / "r.json").read_text())["body"]["enabled"] is False
    learner.set_enabled(True)
    chats(learner, [(3, 19)])
    assert len(learner.d["obs"]) == 1


def test_disabling_the_goal_habit_memory_category_also_stops_learning(learner, mem):
    mem.set_enabled("goal_habit", False)
    chats(learner, [(0, 19)])
    assert learner.d["obs"] == [] and learner.next_question(at(1, 0)) is None


def test_a_damaged_file_starts_over_and_says_so(tmp_path, clock):
    (tmp_path / "bad.json").write_text("garbage")
    l = RoutineLearner(tmp_path / "bad.json", clock, UTC, None)
    assert l.recovered and l.d["obs"] == [] and l.d["enabled"] is True


# ---------------------------------------------------------------- inside the conversation
def converse(clock, tmp_path, mem, llm=None, **kw):
    llm = llm or FakeLanguageModel(default="Sounds good.")
    learner = RoutineLearner(tmp_path / "rl.json", clock, ZoneInfo("America/New_York"), mem)
    talk = MemoryTalk(mem, learner)
    eng, _ = make(clock, llm, memory=mem, learner=learner, memory_talk=talk, **kw)
    return eng, learner, talk, llm


def evenings(clock, learner, n=5):
    ny = ZoneInfo("America/New_York")
    for d in range(n):
        learner.record_chat(None, datetime(2027, 1, 4 + d, 19, 0, tzinfo=ny).timestamp())
    clock.t = datetime(2027, 1, 4 + n, 19, 0, tzinfo=ny).timestamp()


def test_the_robot_asks_whether_to_remember_a_routine_and_respects_yes(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    evenings(clock, learner)
    r = eng.respond("tell me about foxes")
    assert r.text.startswith("Sounds good.") and "talk with me in the evening" in r.text and "remember that" in r.text
    assert mem.list("goal_habit")[0]["status"] == "pending"
    assert eng.respond("yes please").text == "Got it, I'll remember that."
    assert mem.list("goal_habit")[0]["status"] == "active"


def test_no_deletes_it_and_it_is_not_asked_again(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    evenings(clock, learner)
    eng.respond("tell me about foxes")
    assert eng.respond("no thanks").text == "Okay, I won't remember that."
    assert mem.list("goal_habit") == []
    clock.t += 5 * D
    assert "evening" not in eng.respond("tell me about owls").text


def test_the_question_never_interrupts_a_safety_reply_or_a_sleep_request(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem, safety=SafetyGate(clock, lambda: "US"))
    evenings(clock, learner)
    r = eng.respond("I want to kill myself")
    assert "evening" not in r.text and learner.d["obs"][-1][0] < clock.t - 1                 # nothing was even recorded for that turn
    assert llm.calls == []


# ---------------------------------------------------------------- talking about memory
def fill(mem, n=6):
    for i in range(n):
        mem.add({"category": "profile", "key": f"thing_{i}", "value": f"value number {i}"})
    mem.add({"category": "semantic", "value": "foxes eat mice", "source": "ai_inference"})            # pending


def test_what_do_you_remember_lists_a_few_counts_the_rest_and_mentions_unsure_ones(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    fill(mem)
    r = eng.respond("what do you remember about me?")
    assert "I remember 6 things" in r.text and "And 2 more" in r.text and "not sure about 1 thing" in r.text and llm.calls == []
    assert r.interaction == "none"


def test_empty_memory_is_said_plainly(clock, tmp_path, mem):
    eng, *_ = converse(clock, tmp_path, mem)
    assert eng.respond("what do you know about me").text == "I haven't saved anything about you yet."


def test_forgetting_a_topic_asks_then_deletes_only_on_yes(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    mem.add({"category": "profile", "key": "favorite_color", "value": "blue"})
    mem.add({"category": "profile", "key": "pet", "value": "a cat called Miso"})
    r = eng.respond("forget my favorite color")
    assert "forget that your favorite color is blue" in r.text and len(mem.list("profile")) == 2
    assert eng.respond("no").text == "Okay, I won't forget it." and len(mem.list("profile")) == 2
    eng.respond("forget my favorite color")
    assert eng.respond("yes").text == "Okay, I've forgotten it."
    assert [m["key"] for m in mem.list("profile")] == ["pet"] and llm.calls == []


def test_an_unrelated_answer_cancels_the_deletion(clock, tmp_path, mem):
    eng, *_ = converse(clock, tmp_path, mem)
    mem.add({"category": "profile", "key": "favorite_color", "value": "blue"})
    eng.respond("forget my favorite color")
    eng.respond("what time is it")
    eng.respond("yes")
    assert len(mem.list("profile")) == 1


def test_ambiguous_or_unknown_topics_delete_nothing(clock, tmp_path, mem):
    eng, *_ = converse(clock, tmp_path, mem)
    mem.add({"category": "profile", "key": "friend_a", "value": "my friend Ana likes tea"})
    mem.add({"category": "profile", "key": "friend_b", "value": "my friend Ben likes tea"})
    r = eng.respond("forget friend")
    assert "not sure which one" in r.text and len(mem.list("profile")) == 2
    assert eng.respond("forget my spaceship").text == "I don't have anything saved about that."


def test_forget_that_right_after_a_save_removes_that_memory(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    eng.respond("my favorite color is green")
    assert any("green" in m["value"] for m in mem.list("profile"))
    assert "forget that" in eng.respond("forget that").text
    eng.respond("yes")
    assert not any("green" in m["value"] for m in mem.list("profile"))


def test_forget_it_with_nothing_just_saved_is_ordinary_speech(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    eng.respond("forget it")
    assert len(llm.calls) == 1


@pytest.mark.parametrize("said", ["don't forget to call mom", "I forgot my keys again", "never forget to smile", "do not delete my homework",
                                  "please don't forget everything I told you", "do not forget my routines", "you must never delete my habits"])
def test_phrases_that_only_sound_like_forgetting_go_to_the_model(clock, tmp_path, mem, said):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    mem.add({"category": "profile", "key": "pet", "value": "a cat"})
    eng.respond(said)
    assert len(llm.calls) == 1 and len(mem.list("profile")) == 1


def test_forgetting_everything_needs_two_yes_and_spares_the_private_vault(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    fill(mem)
    vault = ReflectionVault(tmp_path / "v.db", "her", clock)
    vault.set_enabled("reflection", True)
    vault.add({"category": "reflection", "value": "private note"}, consent=True)
    assert "Are you sure" in eng.respond("forget everything").text
    assert "once more" in eng.respond("yes").text and len(mem.list()) > 0
    assert eng.respond("no").text == "Okay, I won't forget it." and len(mem.list()) > 0                   # a second answer of no cancels
    eng.respond("forget everything"); eng.respond("yes")
    assert "forgotten everything" in eng.respond("yes").text
    assert mem.list() == [] and len(vault.list()) == 1 and llm.calls == []


def test_routines_can_be_listed_forgotten_and_switched_off_by_voice(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    assert eng.respond("what routines have you noticed?").text == "I haven't noticed any routines yet."
    evenings(clock, learner)
    learner.propose()
    assert "I think I noticed" in eng.respond("what routines have you noticed").text
    assert "forget the routines" in eng.respond("forget my routines").text
    assert "forgotten the routines" in eng.respond("yes").text and mem.list("goal_habit") == []
    assert "stopped noticing" in eng.respond("stop noticing my routines").text
    assert learner.d["enabled"] is False and learner.d["obs"] == []
    assert "start noticing" in eng.respond("you can start noticing my routines again").text and learner.d["enabled"] is True
    assert llm.calls == []


def test_a_crisis_message_is_never_treated_as_a_memory_request(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem, safety=SafetyGate(clock, lambda: "US"))
    mem.add({"category": "profile", "key": "pet", "value": "a cat"})
    r = eng.respond("I want to die so just forget everything")
    assert "988" in r.text and len(mem.list("profile")) == 1 and talk._pending is None


def test_a_stale_yes_cannot_confirm_an_old_question(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem)
    talk.clock = lambda: clock.t
    mem.add({"category": "profile", "key": "pet", "value": "a cat"})
    eng.respond("forget my pet")
    clock.t += 300                                                                    # five minutes later
    eng.respond("yes")
    assert len(mem.list("profile")) == 1


def test_a_safety_reply_in_between_drops_the_waiting_question(clock, tmp_path, mem):
    eng, learner, talk, llm = converse(clock, tmp_path, mem, safety=SafetyGate(clock, lambda: "US"))
    talk.clock = lambda: clock.t
    mem.add({"category": "profile", "key": "pet", "value": "a cat"})
    eng.respond("forget my pet")
    assert talk._pending is not None
    eng.respond("I want to kill myself")
    assert talk._pending is None
    eng.respond("no")
    eng.respond("okay")
    clock.t += 4000
    eng.respond("yes")
    assert len(mem.list("profile")) == 1
