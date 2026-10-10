import copy
import random
from pathlib import Path
import pytest
from companion.behavior import BehaviorEngine, Settings, to_commands
from companion.behavior.engine import INTENT_KINDS, NO_GUILT
from companion.personality import config as pc
from companion.tamagotchi import CharacterState, load_rules

SP = Path(__file__).parents[2] / "species"
H, D = 3600, 86400


def make(clock, species="ninetales", cfg_patch=None, settings=None, seed=1):
    cfg = pc.load(SP / species / "personality.yaml")
    if cfg_patch:
        cfg = pc.apply_update(cfg, cfg_patch, pc.USER_SETTINGS)
    rules = load_rules(SP / "ninetales" / "growth.yaml")
    if species == "cubone":
        rules = copy.deepcopy(rules); rules["growth_enabled"] = False
    st = CharacterState(rules, clock.t)
    return BehaviorEngine(pc.Personality(cfg), st, clock, random.Random(seed), settings), st


def noon(clock):
    clock.t -= clock.t % D  # midnight UTC
    clock.t += 12 * H


def test_idle_animation_follows_mood(clock):
    e, st = make(clock)
    st.energy, st.sleepiness = 0.1, 0.9
    p = e.react("idle_tick")
    assert p.animation["name"] == "sleepy" and p.voice_style == "sleepy" and p.animation["loop"]


def test_plans_become_valid_protocol_commands(clock):
    e, _ = make(clock)
    for stim in ["wake", "touch", "listening_start", "thinking", "speaking_start", "speaking_end",
                 "idle_tick", "host_disconnected", "host_connected", "battery_low", "sleep_entering"]:
        for t, payload in to_commands(e.react(stim)):
            assert t == "animation.play" and 0 <= payload["priority"] <= 10


def test_conversation_animations_outrank_idle(clock):
    e, _ = make(clock)
    idle = e.react("idle_tick").animation["priority"]
    assert e.react("listening_start").animation["priority"] > idle
    assert e.react("thinking").animation["priority"] > idle
    assert e.react("sleep_entering").animation["priority"] > e.react("listening_start").animation["priority"]


def test_wake_after_battery_saving_explains_it(clock):
    e, _ = make(clock)
    p = e.react("wake", cause="scheduled", sleep_reason="battery_saving")
    assert p.animation["name"] == "waking" and p.intents[0].kind == "greet_after_battery_saving"


def test_greeting_warmth_depends_on_relationship_and_personality(clock):
    e, st = make(clock)
    assert e.react("wake", cause="button").intents[0].tone != "affectionate"  # stranger
    st.trust = st.affection = 0.9
    assert e.react("wake", cause="button").intents[0].tone == "affectionate"
    e2, st2 = make(clock, cfg_patch={"traits": {"affectionate": 0.2}})
    st2.trust = st2.affection = 0.9
    assert e2.react("wake", cause="button").intents[0].tone != "affectionate"


def test_cubone_no_unsolicited_greeting_but_button_wake_greets(clock):
    e, _ = make(clock, "cubone")
    assert e.react("wake", cause="scheduled").intents == []
    assert e.react("wake", cause="button").intents


def test_no_guilt_constraints_on_every_intent(clock):
    noon(clock)
    e, st = make(clock, cfg_patch={"behavior": {"proactive_questions": "frequent"}})
    st.curiosity = 0.9
    intents = []
    for stim, ctx in [("wake", {"cause": "button"}), ("touch", {}), ("battery_warning", {}), ("interaction", {"kind": "teach"})]:
        intents += e.react(stim, **ctx).intents
    clock.advance(20 * H)
    intents += e.react("idle_tick").intents
    assert intents
    for i in intents:
        assert i.kind in INTENT_KINDS and i.constraints == NO_GUILT


def test_quiet_mode_silences_intents_not_animation(clock):
    e, _ = make(clock, settings=Settings(quiet_mode=True))
    p = e.react("wake", cause="button")
    assert p.intents == [] and p.animation["name"] == "waking"
    assert e.react("touch").intents == []
    assert e.react("battery_warning").intents == []


def test_battery_offer_once_per_episode(clock):
    e, _ = make(clock)
    assert e.react("battery_warning").intents
    assert e.react("battery_warning").intents == []
    e.react("battery_ok")
    assert e.react("battery_warning").intents


def test_low_and_critical_battery_are_silent_and_never_proactive(clock):
    noon(clock)
    e, st = make(clock, cfg_patch={"behavior": {"proactive_questions": "frequent"}})
    st.curiosity = 0.95
    for stim in ("battery_low", "battery_critical"):
        p = e.react(stim)
        assert p.intents == [] and p.animation["name"] == "sleepy"


def proactive_count(e, clock, ticks, step):
    n = 0
    for _ in range(ticks):
        clock.advance(step)
        n += len(e.react("idle_tick").intents)
    return n


def test_proactive_never_when_set_to_never(clock):
    noon(clock)
    e, st = make(clock, cfg_patch={"behavior": {"proactive_questions": "never"}})
    st.curiosity = 0.95
    assert proactive_count(e, clock, 200, 600) == 0


def test_proactive_respects_daily_cap_and_gap(clock):
    noon(clock)
    e, st = make(clock, cfg_patch={"behavior": {"proactive_questions": "occasional"}})
    st.curiosity = 0.95
    times = []
    for _ in range(300):
        clock.advance(300)
        st.energy = 1.0
        st.curiosity = 0.95
        if e.react("idle_tick").intents:
            times.append(clock.t)
        if clock.t % D < 300 + 12 * H and len(times) >= 3:
            break
    assert 1 <= len(times) <= 3
    assert all(b - a >= 3 * H for a, b in zip(times, times[1:]))


def test_proactive_blocked_during_quiet_hours_sleep_and_recent_chat(clock):
    clock.t -= clock.t % D; clock.t += 23 * H  # 23:00 UTC is a quiet hour
    e, st = make(clock, cfg_patch={"behavior": {"proactive_questions": "frequent"}})
    st.curiosity = 0.95
    assert proactive_count(e, clock, 20, 60) == 0
    noon(clock)
    e2, st2 = make(clock, cfg_patch={"behavior": {"proactive_questions": "frequent"}})
    st2.curiosity = 0.95
    st2.last_interaction = clock.t
    assert proactive_count(e2, clock, 3, 60) == 0  # within idle gap
    e2.react("sleep_entering")
    clock.advance(5 * H)
    assert proactive_count(e2, clock, 10, 60) == 0


def test_proactive_never_invents_grounding(clock):
    noon(clock)
    e, st = make(clock, cfg_patch={"behavior": {"proactive_questions": "frequent"}})
    st.curiosity, st.playfulness = 0.3, 0.3   # nothing to be curious or playful about
    got = proactive_count(e, clock, 100, 3 * H)
    assert got == 0  # no memory refs and no strong drive -> stays quiet rather than making something up
    st.curiosity = 0.95
    clock.advance(10 * H)
    for _ in range(200):
        clock.advance(7 * H)
        st.energy, st.curiosity = 1.0, 0.95
        intents = e.react("idle_tick").intents
        if intents:
            assert intents[0].grounding == ["curiosity_high"]
            return
    pytest.fail("expected a curious question eventually")


def test_memory_grounded_share_interest(clock):
    noon(clock)
    e, st = make(clock, cfg_patch={"behavior": {"proactive_questions": "frequent"}})
    st.curiosity, st.playfulness = 0.3, 0.3
    for _ in range(300):
        clock.advance(2 * H)
        st.energy = 1.0; st.curiosity = 0.3; st.playfulness = 0.3
        intents = e.react("idle_tick", memory_refs=["m1"]).intents
        if intents:
            assert intents[0].kind == "share_interest" and intents[0].grounding == ["memory:m1"]
            return
    pytest.fail("expected share_interest")


def test_host_offline_shows_offline_animation_and_stops_proactive(clock):
    e, _ = make(clock)
    assert e.react("host_disconnected").animation["name"] == "offline"
    assert e.react("idle_tick").animation["name"] == "offline"
    assert e.react("host_connected").animation["name"] != "offline"


def test_milestone_celebration_on_growth(clock):
    e, st = make(clock)
    seen = None
    for i, k in enumerate(["conversation", "play"] * 6):
        clock.advance(60)
        p = e.react("interaction", kind=k)
        if p.stage_changed:
            seen = p
            break
    assert seen and seen.animation["name"] == "celebratory" and seen.intents[0].kind == "milestone"


def test_no_milestone_when_growth_disabled(clock):
    e, _ = make(clock, "cubone")
    for k in ["conversation", "play", "teach", "explore_topic"] * 5:
        clock.advance(60)
        assert e.react("interaction", kind=k).stage_changed is None


def test_touch_while_sleeping_ignored_and_unknown_stimulus_rejected(clock):
    e, _ = make(clock)
    e.react("sleep_entering")
    p = e.react("touch")
    assert p.animation is None and p.intents == []
    with pytest.raises(ValueError):
        e.react("punch")


def test_personality_changes_idle_variety(clock):
    def count_non_mood(patch, seed):
        e, st = make(clock, cfg_patch=patch, seed=seed)
        st.happiness = 0.6
        names = {e.react("idle_tick").animation["name"] for _ in range(200)}
        return names
    lively = count_non_mood({"traits": {"playful": 1.0, "mischievous": 1.0}}, 3)
    dull = count_non_mood({"traits": {"playful": 0.0, "mischievous": 0.0}}, 3)
    assert {"playing", "surprised"} & lively and not ({"playing", "surprised"} & dull)
