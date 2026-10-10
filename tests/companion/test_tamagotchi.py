import copy
from pathlib import Path
import pytest
from companion.tamagotchi import CharacterState, RulesError, StateStore, load_rules, validate_rules
from companion.tamagotchi.state import UNIT_FIELDS

SP = Path(__file__).parents[2] / "species" / "ninetales" / "growth.yaml"
T0 = 1_800_000_000.0
H, D = 3600, 86400


@pytest.fixture
def rules():
    return load_rules(SP)


@pytest.fixture
def st(rules):
    return CharacterState(rules, T0)


def bounded(s):
    return all(0 <= getattr(s, f) <= 1 for f in UNIT_FIELDS)


def test_rules_file_valid_and_bad_rules_rejected(rules):
    bad = copy.deepcopy(rules); bad["needs"]["happiness_floor"] = 0.9
    with pytest.raises(RulesError):
        validate_rules(bad)
    bad = copy.deepcopy(rules); bad["stages"][2]["required"] = {"nonexistent": 1}
    with pytest.raises(RulesError):
        validate_rules(bad)
    bad = copy.deepcopy(rules); bad["events"]["play"]["happiness"] = 5
    with pytest.raises(RulesError):
        validate_rules(bad)


def test_starts_as_egg_and_values_bounded(st):
    assert st.stage == "EGG" and bounded(st)


def test_awake_decays_energy_sleep_restores(st):
    st.advance(T0 + 10 * H)
    low = st.energy
    assert low < 0.9 and st.sleepiness > 0.1
    st.set_sleeping(True, T0 + 10 * H)
    st.advance(T0 + 20 * H)
    assert st.energy > low and bounded(st)


def test_sleepy_mood_and_slower_animation(st):
    fast = st.animation_speed
    st.advance(T0 + 40 * H)
    assert st.mood == "sleepy" and st.animation_speed < fast


def test_long_absence_is_not_punished(rules):
    s = CharacterState(rules, T0)
    for k in ["conversation", "play", "teach"]:
        s.apply_event(k, T0 + 60)
    snap = (s.affection, s.trust, s.experience, s.stage)
    s.advance(T0 + 400 * D)
    assert (s.affection, s.trust, s.experience, s.stage) == snap
    assert s.happiness >= rules["needs"]["happiness_floor"]
    assert s.mood != "sad" and bounded(s)


def test_clock_going_backwards_is_ignored(st):
    st.advance(T0 + 5 * H)
    e = st.energy
    st.advance(T0)
    assert st.energy == e and st.last_update == T0 + 5 * H


def test_diminishing_returns_and_daily_cap(st, rules):
    gains = [st.apply_event("conversation", T0 + i)["xp_gained"] for i in range(200)]
    assert gains[0] > gains[2] > gains[4] and gains[-1] == 0
    assert sum(gains) <= rules["events"]["conversation"]["daily_xp_cap"] + 1e-9
    nxt = st.apply_event("conversation", T0 + D + 1)["xp_gained"]
    assert nxt > 0.9  # new day, full value again


def test_spamming_one_action_cannot_grow_past_baby(st):
    for day in range(60):
        for i in range(100):
            st.apply_event("conversation", T0 + day * D + i)
    assert st.stage in ("EGG", "BABY")  # needs variety (min_kinds), never reaches NINE_TAILS


def test_varied_meaningful_interaction_reaches_nine_tails_and_never_regresses(st):
    changes = []
    for day in range(40):
        t = T0 + day * D
        for k in ["greeting", "conversation", "play", "teach", "explore_topic", "feed", "optional_activity"]:
            for i in range(4):
                r = st.apply_event(k, t + i * 60 + 1)
                if r["stage_changed"]:
                    changes.append(r["stage_changed"])
    assert changes[:4] == ["BABY", "YOUNG", "NINE_TAILS", "PERSONALIZED_NINE_TAILS"]
    st.advance(T0 + 500 * D)
    assert st.stage == "PERSONALIZED_NINE_TAILS"


def test_growth_disabled_keeps_stage_but_updates_needs(rules):
    r = copy.deepcopy(rules); r["growth_enabled"] = False
    s = CharacterState(r, T0)
    for k in ["greeting", "conversation", "play", "teach", "explore_topic"]:
        s.apply_event(k, T0 + 1)
    assert s.stage == "EGG" and s.trust > 0.1


def test_unknown_event_rejected(st):
    with pytest.raises(KeyError):
        st.apply_event("punish", T0)


def test_mood_priority(st):
    st.playfulness, st.energy, st.sleepiness = 0.9, 0.9, 0.1
    assert st.mood == "playful"
    st.energy = 0.1
    assert st.mood == "sleepy"
    st.sleeping = True
    assert st.mood == "sleeping"


def test_persistence_roundtrip_across_restart(tmp_path, rules):
    store = StateStore(tmp_path / "c.json", rules)
    s = CharacterState(rules, T0)
    for k in ["greeting", "conversation", "play", "teach"]:
        s.apply_event(k, T0 + 5)
    s.apply_event("teach", T0 + 10)
    store.save(s)
    s2 = StateStore(tmp_path / "c.json", rules).load(T0 + H)
    assert s2.to_dict() == s.to_dict() and not store.recovered
    assert s2.stage == s.stage and s2.kind_counts == s.kind_counts


@pytest.mark.parametrize("junk", ["\x00\x01", "{}", '{"state":{},"sha256":"x"}', "null"])
def test_corrupt_file_recovers_to_fresh_state(tmp_path, rules, junk):
    p = tmp_path / "c.json"; p.write_text(junk)
    store = StateStore(p, rules)
    s = store.load(T0)
    assert store.recovered and s.stage == "EGG" and bounded(s)


def test_out_of_range_values_rejected_on_load(tmp_path, rules):
    import json, hashlib
    s = CharacterState(rules, T0); d = s.to_dict(); d["trust"] = 7
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"state": d, "sha256": hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()}))
    store = StateStore(p, rules); store.load(T0)
    assert store.recovered


def test_relationship_grows_from_care_not_time(st):
    assert st.relationship_state == "new"
    for i in range(40):
        for k in ("conversation", "teach"):
            st.apply_event(k, T0 + i * D)
    assert st.relationship_state in ("familiar", "close")
