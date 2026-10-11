from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from companion.psychology.conflict_navigation import build_conflict_exercise
from companion.psychology.exercises import ExerciseRunner, load_library, parse_return_time, parse_scale
from companion.scheduling.sleep import AmbiguousTime

NY = ZoneInfo("America/New_York")
T0 = datetime(2026, 10, 10, 15, 0, tzinfo=NY).timestamp()      # 3 pm Saturday, New York


@pytest.fixture(scope="module")
def lib():
    return load_library()


def runner(ex, now=T0):
    return ExerciseRunner(ex, lambda: now, NY)


def test_naming_emotions_end_to_end(lib):
    r = runner(lib["naming_emotions"])
    out = r.start()
    assert "put a word on it" in out.text and "what is going on" in out.text and not out.done
    r.answer("my friend canceled on me again")
    out = r.answer("I think hurt")
    assert "body" in out.text
    r.answer("my chest feels tight")
    r.answer("maybe she doesn't care")
    out = r.answer("six")
    assert "change anything" in out.text
    out = r.answer("yes a bit")
    assert out.done and not out.stopped
    assert r.answers["feeling"] == "hurt" and r.answers["intensity"] == "6"
    assert r.indicators()["named_feeling"] and r.indicators()["rated_intensity"]
    labels = [l for l, _ in r.recap()]
    assert "The feeling I named" in labels and "What was happening" in labels


def test_choice_accepts_her_own_word_when_allowed(lib):
    r = runner(lib["naming_emotions"])
    r.start(); r.answer("work stuff")
    r.answer("deflated")
    assert r.answers["feeling"] == "deflated"


@pytest.mark.parametrize("stop", ["stop", "Stop.", "let's stop", "can we stop", "never mind", "that's enough", "not now",
                                  "I don't want to do this", "cancel", "I want to stop", "enough please"])
def test_she_can_stop_at_any_time_in_her_own_words(lib, stop):
    r = runner(lib["facts_vs_interpretations"])
    r.start(); r.answer("something happened")
    out = r.answer(stop)
    assert out.done and out.stopped and r.stopped
    assert r.answer("anything else").done                       # nothing continues after a stop


def test_stop_words_inside_an_answer_do_not_stop(lib):
    r = runner(lib["facts_vs_interpretations"])
    r.start()
    out = r.answer("He said he doesn't want to talk and told me to stop calling him")
    assert not out.done and r.answers["event"].startswith("He said")


@pytest.mark.parametrize("skip", ["skip", "pass", "I'd rather not", "I don't know", "next"])
def test_any_step_can_be_skipped(lib, skip):
    r = runner(lib["facts_vs_interpretations"])
    r.start()
    out = r.answer(skip)
    assert r.answers["event"] is None and "story" in out.text and not out.done


def test_skipped_answers_never_reach_the_recap(lib):
    r = runner(lib["facts_vs_interpretations"])
    r.start()
    r.answer("skip"); r.answer("the story is X")
    assert [l for l, _ in r.recap()] == ["The story I told myself"]


def test_unclear_answers_get_one_retry_then_we_move_on(lib):
    r = runner(lib["naming_emotions"])
    r.start(); r.answer("hello")
    r2 = runner(lib["break_and_return_plan"])
    r2.start()
    first = r2.answer("hmm")
    assert "which one" in first.text.lower() and r2.i == 1
    second = r2.answer("whatever")
    assert "move on" in second.text and r2.answers["who"] is None


@pytest.mark.parametrize("text,n", [("7", 7), ("it's an 8 maybe", 8), ("zero", 0), ("ten", 10), ("about five", 5), ("0", 0)])
def test_scale_parsing(text, n):
    assert parse_scale(text) == n


@pytest.mark.parametrize("text", ["eleven", "99", "lots", ""])
def test_scale_rejects_nonsense(text):
    assert parse_scale(text) is None


def test_very_high_intensity_ends_the_questions_and_offers_calm(lib):
    r = runner(lib["naming_emotions"])
    r.start(); r.answer("a fight"); r.answer("overwhelmed"); r.answer("skip"); r.answer("skip")
    out = r.answer("ten")
    assert out.done and out.stopped and out.suggest == "slow_breathing" and "stop the questions" in out.text


def test_consent_step_no_ends_gracefully(lib):
    for eid in ("slow_breathing", "grounding_54321"):
        r = runner(lib[eid])
        r.start()
        out = r.answer("no thanks")
        assert out.done and out.stopped and "No problem" in out.text


def test_breathing_guidance_carries_a_pause_hint(lib):
    r = runner(lib["slow_breathing"])
    r.start()
    out = r.answer("yes")
    assert out.pauses == [40.0] and "in for four" in out.text.lower() and "out for six" in out.text.lower()


def test_build_text_uses_her_words_only(lib):
    r = runner(lib["prepare_difficult_conversation"])
    r.start()
    for a in ["I want us to split chores fairly", "the dishes sat there since Tuesday", "annoyed", "shared effort", "do the dishes after dinner"]:
        out = r.answer(a)
    assert "the dishes sat there since Tuesday" in out.text
    assert r.answers["draft"] == ("When the dishes sat there since Tuesday, I felt annoyed. What I need is shared effort. "
                                  "Would you be willing to do the dishes after dinner?")
    assert "Change anything" in out.text


def test_build_text_survives_skipped_parts(lib):
    r = runner(lib["prepare_difficult_conversation"])
    r.start()
    for a in ["a goal", "skip", "tired", "skip", "skip"]:
        out = r.answer(a)
    assert "..." in r.answers["draft"]


# ------------------------------------------------------------------ return times
@pytest.mark.parametrize("text,expected", [
    ("tonight at 8", (2026, 10, 10, 20, 0)), ("tonight at 8:30", (2026, 10, 10, 20, 30)), ("8 pm", (2026, 10, 10, 20, 0)),
    ("tomorrow at 9 am", (2026, 10, 11, 9, 0)), ("tomorrow morning at 10", (2026, 10, 11, 10, 0)), ("in 30 minutes", (2026, 10, 10, 15, 30)),
    ("in two hours", (2026, 10, 10, 17, 0)), ("noon", (2026, 10, 11, 12, 0)), ("5pm", (2026, 10, 10, 17, 0)),
    ("at 2 pm", (2026, 10, 11, 14, 0))])                          # 2 pm already passed today: next occurrence
def test_return_time_parsing(text, expected):
    got = parse_return_time(text, datetime.fromtimestamp(T0, NY))
    assert (got.year, got.month, got.day, got.hour, got.minute) == expected


def test_ambiguous_or_missing_times_ask_instead_of_guessing():
    now = datetime.fromtimestamp(T0, NY)
    with pytest.raises(AmbiguousTime):
        parse_return_time("at 8", now)
    with pytest.raises(AmbiguousTime):
        parse_return_time("later", now)


def test_return_time_step_validates_and_records(lib):
    r = runner(lib["break_and_return_plan"])
    r.start(); r.answer("me"); r.answer("30 minutes")
    out = r.answer("later")
    assert "What time exactly" in out.text
    out = r.answer("tonight at 8")
    assert out.followup_at == datetime(2026, 10, 10, 20, 0, tzinfo=NY).timestamp() and r.answers["return_time"] == "8:00 PM"
    assert r.indicators()["chose_return_time"]


def test_return_time_too_soon_or_too_far(lib):
    r = runner(lib["break_and_return_plan"])
    r.start(); r.answer("both"); r.answer("an hour")
    assert "few minutes" in r.answer("in 1 minute").text
    r.tries = 0
    assert "sooner" in r.answer("in 100 hours").text


# ------------------------------------------------------------------ the conflict walk-through
def walk(lib, depth, answers):
    r = runner(build_conflict_exercise(lib["conflict_navigation"], depth))
    texts = [r.start().text]
    for a in answers:
        out = r.answer(a)
        texts.append(out.text)
        if out.done:
            break
    return r, " ".join(texts)


def test_intro_says_it_cannot_judge(lib):
    _, text = walk(lib, "full", [])
    assert "I won't say who is right" in text and "skip anything" in text and "ask for a break" in text


def test_person_who_needs_space_gets_the_overwhelm_questions(lib):
    r, text = walk(lib, "full", ["we argued about plans", "hurt", "6", "space", "the yelling", "a calmer tone"])
    assert "what part of this conversation became overwhelming" in text.lower()
    assert "feel safe enough to return to the topic" in text
    assert "unresolved until later" not in text


def test_person_who_wants_it_settled_gets_the_other_questions(lib):
    r, text = walk(lib, "full", ["we argued about plans", "frustrated", "6", "settle", "it nags at me", "tonight"])
    assert "difficult to leave a problem unresolved until later" in text
    assert "specific time to continue" in text
    assert "became overwhelming" not in text


def test_both_gets_all_four_without_assuming_who_is_which(lib):
    r, text = walk(lib, "full", ["we argued", "sad", "5", "both", "x", "x", "x", "x"])
    for q in ["became overwhelming", "safe enough to return", "unresolved until later", "specific time to continue"]:
        assert q in text
    assert "respectful to both people" in text


def test_the_other_persons_side_is_framed_as_a_guess_she_can_check(lib):
    r, text = walk(lib, "full", ["a", "sad", "5", "both", "s", "s", "s", "s", "s"])
    assert "Treat it as a guess" in text


def test_choosing_a_break_hands_over_to_the_break_plan(lib):
    answers = ["a", "sad", "5", "settle", "s", "s", "s", "s", "s", "s", "s", "s", "break"]
    r = runner(build_conflict_exercise(lib["conflict_navigation"], "full"))
    r.start()
    last = None
    for a in answers:
        last = r.answer(a)
    assert last.done and last.suggest == "break_and_return_plan" and r.route_to == "break_and_return_plan"
    assert "a way back" in last.text


def test_quick_walkthrough_is_short_and_ends_with_her_own_next_step(lib):
    r, text = walk(lib, "quick", ["a disagreement", "angry", "4", "to be heard", "continue", "send a calm message"])
    assert r.done and r.answers["next_step"] == "send a calm message" and r.indicators()["chose_next_step"]
    assert "pattern" not in r.answers


def test_nothing_in_the_recap_comes_from_the_robot(lib):
    r, _ = walk(lib, "quick", ["a disagreement", "angry", "4", "to be heard", "continue", "send a calm message"])
    values = [v for _, v in r.recap()]
    assert set(values) <= {"a disagreement", "angry", "to be heard", "send a calm message"}
