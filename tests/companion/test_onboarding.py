from pathlib import Path
import pytest
from companion.application.device_client import DeviceClient, LoopbackTransport
from companion.application.onboarding import (CORE, OPTIONAL, Onboarding, load_copy, parse_name)
from companion.memory import MemoryStore, ReflectionVault
from companion.personality import config as pc
from protocol import Authenticator
from tests.conftest import SECRET

SP = Path(__file__).parents[2] / "species"
H, D = 3600, 86400
VOICES = [{"id": "v_soft", "label": "Soft Fox"}, {"id": "v_bright", "label": "Bright Kit"}]


def make(tmp_path, species="ninetales", **kw):
    return Onboarding(tmp_path / "ob.json", load_copy(SP / species), pc.load(SP / species / "personality.yaml"), **kw)


def finish_core(ob, t=1000.0):
    ob.next_prompt(t)
    r = ob.answer("call me José", t + 1)
    assert r.follow_up and r.follow_up.step == "companion_name"
    r = ob.answer("keep", t + 2)
    return r, t + 2


@pytest.mark.parametrize("text,expected", [("I'm José", "José"), ("My name is Ana María.", "Ana María"), ("Sam", "Sam"),
                                           ("call me O'Brien", "O'Brien"), ("hello", None), ("tired", None),
                                           ("12345", None), ("<script>alert(1)</script>", None),
                                           ("x" * 40, None), ("", None), ("a b c d", None)])
def test_parse_name(text, expected):
    assert parse_name(text) == expected


def test_copy_files_valid_and_species_override():
    n = load_copy(SP / "ninetales")["steps"]["user_name"]["ask"]
    c = load_copy(SP / "cubone")["steps"]["user_name"]["ask"]
    assert n != c and "hatched" in n


def test_core_flow_is_two_questions_then_done(tmp_path, clock):
    ob = make(tmp_path)
    t = ob.next_prompt(1000)
    assert t.kind == "ask" and t.step == "user_name" and "hatched" in t.text
    assert ob.next_prompt(1001) is None  # already asked, wait
    r = ob.answer("I'm José", 1002)
    assert "José" in r.text and r.follow_up.step == "companion_name" and "Nine Tails" in r.follow_up.text
    r = ob.answer("Kitsune", 1003)
    assert ob.core_done and "José" in r.text
    assert ob.effective_config()["character"]["name"] == "Kitsune"
    assert ob.user_name() == "José" and ob.awaiting() is None


def test_keep_default_companion_name(tmp_path):
    ob = make(tmp_path)
    ob.next_prompt(0); ob.answer("Sam", 1)
    r = ob.answer("keep it", 2)
    assert ob.effective_config()["character"]["name"] == "Nine Tails" and "stay" in r.text


def test_core_never_blocks_on_bad_answers(tmp_path):
    ob = make(tmp_path)
    ob.next_prompt(0)
    assert ob.answer("???", 1).kind == "reask"
    r = ob.answer("!!!", 2)  # second miss: proceed without a name
    assert "user_name" in ob.s["skipped"] and r.follow_up.step == "companion_name"
    ob.answer("skip", 3)
    assert ob.core_done and ob.user_name() is None
    assert "friend" in ob._fmt("{user_name}")


def test_core_not_repeated_after_restart_but_resumes_midway(tmp_path):
    ob = make(tmp_path)
    ob.next_prompt(0)
    ob.answer("Sam", 1)
    ob2 = make(tmp_path)                       # restart while waiting on companion_name
    assert ob2.awaiting() == "companion_name" and not ob2.core_done
    assert ob2.next_prompt(2) is None          # not re-asked immediately
    assert ob2.next_prompt(2 + 700).kind == "reask"
    ob2.answer("keep", 800)
    ob3 = make(tmp_path)
    assert ob3.core_done
    later = ob3.next_prompt(10 * D)
    assert later is not None and later.step not in CORE


def test_optional_questions_are_paced_one_at_a_time(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    assert ob.next_prompt(t + 60) is None                # too soon after setup
    q1 = ob.next_prompt(t + 31 * 60)
    assert q1.step == "communication_style"
    assert ob.next_prompt(t + 32 * 60) is None           # waiting for her answer
    ob.answer("playful please", t + 33 * 60)
    assert ob.next_prompt(t + 2 * H) is None             # gap not elapsed
    assert ob.next_prompt(t + 5 * H).step == "notification_frequency"


def test_quiet_and_sleeping_suppress_optional_questions(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    assert ob.next_prompt(t + D, quiet=True) is None
    assert ob.next_prompt(t + D, sleeping=True) is None
    assert ob.next_prompt(t + D)


def test_skip_later_and_unrecognized(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    t += D
    ob.next_prompt(t)
    assert ob.answer("skip", t + 1).kind == "ack" and "communication_style" in ob.s["skipped"]
    t += D
    q = ob.next_prompt(t)
    assert q.step == "notification_frequency"
    ob.answer("not now", t + 1)
    assert ob.next_prompt(t + 5 * H).step == "sleep_behavior"       # deferred one is skipped over for now
    ob.answer("banana", t + 5 * H + 1)
    r = ob.answer("purple", t + 5 * H + 2)                          # second miss: let conversation continue
    assert r.passthrough
    assert "communication_style" not in [ob.awaiting()]


def test_optional_question_lapses_without_nagging(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    t += D
    assert ob.next_prompt(t).step == "communication_style"
    assert ob.next_prompt(t + 10 * 60) is None                       # still waiting, no repeat
    ob.s["next_offer_at"] = 0
    nxt = ob.next_prompt(t + 40 * 60)                                # unanswered 30+ min: deferred, move on
    assert "communication_style" in ob.s["deferred"] and nxt.step == "notification_frequency"


def test_communication_and_notification_effects_validated_and_persisted(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    ob.reopen("communication_style", t); ob.answer("2", t + 1)      # option number
    ob.reopen("notification_frequency", t + 2); ob.answer("never", t + 3)
    cfg = make(tmp_path).effective_config()                         # after restart
    assert cfg["communication"]["verbosity"] == "high"
    assert cfg["behavior"]["proactive_questions"] == "never" and cfg["behavior"]["unsolicited_greetings"] is False
    pc.validate(cfg)


def test_ambiguous_choice_is_not_guessed(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    ob.reopen("communication_style", t)
    assert ob.answer("short and playful", t + 1).kind == "reask"


def test_memory_preferences_applied_to_store(tmp_path, clock):
    mem = MemoryStore(tmp_path / "m.db", "her", clock)
    ob = make(tmp_path, memory=mem)
    ob.next_prompt(0); ob.answer("Sam", 1); ob.answer("keep", 2)
    assert mem.search("sam")                                         # name remembered by default
    ob.reopen("memory_preference", 3); ob.answer("only what I tell you", 4)
    s = mem.settings()
    assert not s["episodic"]["enabled"] and s["profile"]["enabled"]
    ob.reopen("memory_preference", 5); ob.answer("nothing at all", 6)
    assert not any(v["enabled"] for v in mem.settings().values())
    mem2 = MemoryStore(tmp_path / "m.db", "her", clock)              # restart: preference re-applied
    mem2.set_enabled("profile", True)
    make(tmp_path, memory=mem2)
    assert not mem2.settings()["profile"]["enabled"]


def test_psychology_optin_is_explicit_and_never_enables_vault(tmp_path, clock):
    vault = ReflectionVault(tmp_path / "v.db", "her", clock)
    ob = make(tmp_path, memory=MemoryStore(tmp_path / "m.db", "her", clock))
    _, t = finish_core(ob)
    assert ob.effective_config()["behavior"]["psychological_growth_features"] == "opt_in"
    ob.reopen("psychology_optin", t)
    ob.answer("yes please", t + 1)
    assert ob.effective_config()["behavior"]["psychological_growth_features"] == "on"
    assert not vault.settings()["reflection"]["enabled"]             # vault untouched
    ob.reopen("psychology_optin", t + 2); ob.answer("no", t + 3)
    assert ob.effective_config()["behavior"]["psychological_growth_features"] == "off"


def test_psychology_not_now_defers_and_no_thanks_is_no(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    ob.reopen("psychology_optin", t); ob.answer("not now", t + 1)
    assert "psychology_optin" in ob.s["deferred"] and "psychology_optin" not in ob.answers()
    ob.reopen("psychology_optin", t + 2); ob.answer("no thanks", t + 3)
    assert ob.answers()["psychology_optin"] == "no"


def test_psychology_prompt_discloses_what_it_is(tmp_path):
    ob = make(tmp_path)
    finish_core(ob)
    text = ob.reopen("psychology_optin", 5000).text.lower()
    for must in ("private", "pause", "not therapy"):
        assert must in text


def test_voice_step_only_when_voices_exist(tmp_path):
    ob = make(tmp_path)
    _, t = finish_core(ob)
    seen = []
    for i in range(1, 12):
        q = ob.next_prompt(t + i * D)
        if q:
            seen.append(q.step); ob.answer("skip", t + i * D + 1)
    assert "voice" not in seen
    sub = tmp_path / "x"; sub.mkdir()
    ob2 = make(sub, voices=VOICES)
    _, t = finish_core(ob2)
    q = ob2.reopen("voice", t)
    assert "Soft Fox" in q.text and "Bright Kit" in q.text and ob2.awaiting() == "voice"
    ob2.answer("the bright one", t + 1)
    assert ob2.s["settings"]["voice_id"] == "v_bright"


def test_sleep_behavior_syncs_to_device_or_stays_pending(tmp_path, device, clock):
    client = DeviceClient(LoopbackTransport(device), Authenticator(SECRET, clock), clock)
    ob = make(tmp_path, device=client)
    _, t = finish_core(ob)
    ob.reopen("sleep_behavior", t); ob.answer("nap soon", t + 1)
    assert device.state["config"]["idle_sleep_seconds"] == 120 and ob.pending_device_config() == {}
    # device asleep: setting is kept and pushed later
    client.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    ob.reopen("sleep_behavior", t + 2); ob.answer("stay up", t + 3)
    assert ob.pending_device_config() == {"idle_sleep_seconds": 1800}
    device.press_button("double")
    assert ob.sync_device() and device.state["config"]["idle_sleep_seconds"] == 1800


def test_privacy_info_step_only_with_text_and_not_asked_twice(tmp_path):
    ob = make(tmp_path, privacy_text="I only listen while you hold my button.")
    _, t = finish_core(ob)
    got = []
    for i in range(1, 15):
        q = ob.next_prompt(t + i * D)
        if q:
            got.append(q)
            if ob.awaiting():
                ob.answer("skip", t + i * D + 1)
    priv = [q for q in got if q.step == "privacy_info"]
    assert len(priv) == 1 and "hold my button" in priv[0].text


def test_cubone_uses_own_copy_and_config(tmp_path):
    ob = make(tmp_path, "cubone")
    assert ob.next_prompt(0).text.startswith("Ready")
    ob.answer("Sam", 1)
    r = ob.answer("keep", 2)
    assert "Setup done" in r.text and ob.effective_config()["character"]["name"] == "Cubone"


def test_corrupt_store_restarts_onboarding_safely(tmp_path):
    (tmp_path / "ob.json").write_text("garbage")
    ob = make(tmp_path)
    assert ob.recovered and not ob.core_done and ob.next_prompt(0).step == "user_name"


def test_answer_without_question_is_passthrough(tmp_path):
    ob = make(tmp_path)
    assert ob.answer("hello", 0).passthrough


def test_all_steps_have_copy_and_options_cover_effects():
    c = load_copy(SP / "ninetales")["steps"]
    for s in CORE + OPTIONAL:
        assert s in c
    from companion.application.onboarding import CONFIG_EFFECTS, IDLE_SLEEP_S, MEMORY_CATEGORIES
    for step, effects in CONFIG_EFFECTS.items():
        assert set(c[step]["options"]) == set(effects)
    assert set(c["sleep_behavior"]["options"]) == set(IDLE_SLEEP_S)
    assert set(c["memory_preference"]["options"]) == set(MEMORY_CATEGORIES)


def test_yaml_keyword_lists_are_all_strings():
    # YAML 1.1 turns bare yes/no/on/off into booleans; guard against that regression.
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                assert isinstance(k, str), k
                walk(v)
        elif isinstance(x, list):
            for v in x:
                assert isinstance(v, str), v
    walk(load_copy(SP / "ninetales"))
