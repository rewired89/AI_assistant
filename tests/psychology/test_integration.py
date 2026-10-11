import json
import random

import pytest

from companion.conversation import ConversationEngine, FakeLanguageModel
from companion.conversation.dialogue import LinePicker, load_templates
from companion.memory import MemoryStore, ReflectionVault
from companion.personality import config as pc
from companion.psychology.engine import DISCLOSURE, PsychologyEngine
from companion.psychology.safety import SafetyGate
from companion.psychology.settings import PsychologySettings
from companion.speech import State
from companion.tamagotchi import CharacterState, load_rules
from tests.companion.test_conversation import SP
from tests.companion.test_proactive import World, rig, world  # noqa: F401
from tests.companion.test_voice_pipeline import FakeRecognizer, spoken  # noqa: F401

D = 86400


def build(tmp_path, clock, llm=None, feature="opt_in", species="ninetales", memory=True):
    cfg = pc.load(SP / species / "personality.yaml")
    state = CharacterState(load_rules(SP / "ninetales" / "growth.yaml"), clock.t)
    llm = llm or FakeLanguageModel()
    mem = MemoryStore(tmp_path / "m.db", "her", clock) if memory else None
    vault = ReflectionVault(tmp_path / "v.db", "her", clock)
    settings = PsychologySettings(tmp_path / "psy.json", clock)
    gate = SafetyGate(clock, lambda: settings.region)
    psy = PsychologyEngine(settings, vault, clock, gate=gate, llm=llm, feature=lambda: feature)
    eng = ConversationEngine(llm, lambda: cfg, state, clock, memory=mem, safety=gate, psychology=psy,
                             dialogue=LinePicker(load_templates(SP / species), random.Random(1)))
    return eng, psy, mem, vault, llm, settings


def test_a_crisis_in_ordinary_chat_gets_fixed_text_and_nothing_else(tmp_path, clock):
    eng, psy, mem, vault, llm, _ = build(tmp_path, clock)
    r = eng.respond("I want to kill myself, I love Vulpix")
    assert "988" in r.text and r.interaction == "none" and r.style == "calm"
    assert llm.calls == [] and mem.list() == [] and list(eng.history) == [] and vault.list() == []
    follow = eng.respond("no")
    assert "911" in follow.text and llm.calls == []
    eng.respond("okay")                                          # hold used up
    r = eng.respond("what's your favorite color")
    assert llm.calls and r.interaction in ("conversation", "explore_topic")


def test_abuse_signals_never_get_compromise_advice(tmp_path, clock):
    eng, *_ = build(tmp_path, clock, feature="on")
    r = eng.respond("my boyfriend hits me but I want to make him understand, help me with this argument")
    assert "1-800-799-7233" in r.text and "won't suggest ways to work it out" in r.text
    after = eng.respond("yes I'm safe")
    assert "exercise" not in after.text.lower() and "argument" not in after.text.lower()


def test_exercises_never_start_in_the_hour_after_a_safety_signal(tmp_path, clock):
    eng, psy, *_ = build(tmp_path, clock)
    eng.respond("I want to die")
    eng.respond("no"); eng.respond("ok")
    clock.advance(30 * 60)
    r = eng.respond("help me with this argument")
    assert "rather not start an exercise" in r.text
    r = eng.respond("can we do a breathing exercise")           # breathing is allowed when she asks herself
    assert DISCLOSURE in r.text or "slow breathing" in r.text.lower()


def test_reflection_turns_stay_out_of_the_model_history_and_growth(tmp_path, clock):
    eng, psy, mem, vault, llm, settings = build(tmp_path, clock)
    settings.acknowledge()
    r = eng.respond("can we do a breathing exercise")
    assert "slow breathing" in r.text.lower() and r.interaction == "none"
    assert llm.calls == [] and list(eng.history) == []
    r = eng.respond("yes")
    assert r.interaction == "none" and llm.calls == []


def test_ordinary_turns_still_reach_the_model(tmp_path, clock):
    eng, *_ , llm, _ = build(tmp_path, clock)
    eng.respond("tell me about foxes")
    assert len(llm.calls) == 1


def test_when_the_feature_is_off_for_this_character_the_model_handles_everything(tmp_path, clock):
    eng, psy, mem, vault, llm, _ = build(tmp_path, clock, feature="off", species="cubone")
    eng.respond("can we do a breathing exercise")
    assert len(llm.calls) == 1 and psy.sess is None


def test_corrupt_settings_fall_back_to_asking_again(tmp_path, clock):
    (tmp_path / "psy.json").write_text("garbage")
    s = PsychologySettings(tmp_path / "psy.json", clock)
    assert s.recovered and not s.acknowledged and s.active and s.d["followups"] == []


def test_settings_survive_restart_and_contain_no_content(tmp_path, clock):
    s = PsychologySettings(tmp_path / "psy.json", clock)
    s.acknowledge(); s.set_region("us"); s.pause(60); s.add_followup(clock.t + 100)
    again = PsychologySettings(tmp_path / "psy.json", clock)
    assert again.acknowledged and again.region == "US" and not again.active
    assert set(json.loads((tmp_path / "psy.json").read_text())["body"]) == {"enabled", "ack_version", "paused_until", "declined_until",
                                                                          "region", "followups", "last_offer_day"}


def test_reminder_limits(tmp_path, clock):
    s = PsychologySettings(tmp_path / "psy.json", clock)
    assert s.add_followup(clock.t - 5) is None                  # in the past
    ids = [s.add_followup(clock.t + 100 + i) for i in range(7)]
    assert ids.count(None) == 2 and len(s.d["followups"]) == 5
    s.purge_old()


# ------------------------------------------------------------------ through the voice pipeline and the director
def psy_world(world, tmp_path, clock, feature="opt_in", llm=None):
    llm = llm or FakeLanguageModel(["Sure."] * 20)
    vault = ReflectionVault(tmp_path / "v.db", "her", clock)
    settings = PsychologySettings(tmp_path / "psy.json", clock)
    gate = SafetyGate(clock, lambda: settings.region)
    psy = PsychologyEngine(settings, vault, clock, gate=gate, llm=llm, feature=lambda: feature)
    w = world(llm=llm, psychology=psy, safety=gate, proactive="never")
    w.psy, w.settings, w.vault = psy, settings, vault
    return w


def test_a_spoken_crisis_gets_the_scripted_reply_and_no_model_call(world, tmp_path, clock):
    w = psy_world(world, tmp_path, clock)
    w.r.stt = w.r.pipe.stt = FakeRecognizer(["I want to kill myself"])
    w.r.utter(); w.r.idle(); w.settle()
    assert "988" in spoken(w.r) and w.llm.calls == []
    assert w.beh.state.experience == 0                          # a crisis is not "growth"


def test_reflection_turns_earn_no_growth_but_normal_chat_does(world, tmp_path, clock):
    w = psy_world(world, tmp_path, clock)
    w.settings.acknowledge()
    w.r.stt = w.r.pipe.stt = FakeRecognizer(["can we do a breathing exercise", "tell me about foxes"])
    w.r.utter(); w.r.idle(); w.settle()
    assert w.beh.state.experience == 0
    w.psy.sess = None
    w.r.utter(); w.r.idle(); w.settle()
    assert w.beh.state.experience > 0


def test_the_planned_reminder_is_spoken_without_content_and_leads_to_a_gentle_revisit(world, tmp_path, clock):
    w = psy_world(world, tmp_path, clock)
    w.settings.acknowledge()
    w.settings.add_followup(clock.t + 600)
    n = len(w.llm.calls)
    w.drive(700, stop=lambda: w.d.stats["spoken:followup_due"] >= 1, coarse=30)
    w.settle()
    said = spoken(w.r)
    assert "time you planned to come back to something" in said and len(w.llm.calls) == n   # scripted, never model-written
    assert "?" in said and not any(word in said.lower() for word in ["fight", "argument", "break", "sorry"])
    w.r.stt = w.r.pipe.stt = FakeRecognizer(["yes"])
    w.r.utter(); w.r.idle(); w.settle()
    assert "What has happened since the break" in spoken(w.r)


def test_reminders_still_speak_in_quiet_mode_but_wait_for_the_robot_to_wake(world, tmp_path, clock):
    w = psy_world(world, tmp_path, clock)
    w.beh.settings.quiet_mode = True
    w.settings.add_followup(clock.t + 60)
    w.d.asleep = True
    w.drive(300, coarse=30)
    assert w.d.stats["spoken:followup_due"] == 0                # asleep: not spoken, still due
    assert len(w.psy.due_followups()) == 1
    w.d.asleep = False
    assert w.drive(300, stop=lambda: w.d.stats["spoken:followup_due"] >= 1, coarse=30)


def test_expired_reminder_is_requeued_not_lost(world, tmp_path, clock):
    w = psy_world(world, tmp_path, clock)
    w.settings.add_followup(clock.t + 30)
    w.r.press()
    w.r.run(lambda: w.r.pipe.state is State.LISTENING)
    w.drive(20, coarse=1.0)                                     # busy listening: the reminder cannot be spoken
    assert w.d.stats["spoken:followup_due"] == 0
    w.r.mic(b"\x00\x00" * 16000); w.r.press()
    w.r.idle(); w.settle()
    w.drive(120, stop=lambda: w.d.stats["spoken:followup_due"] >= 1, coarse=5)
    assert w.d.stats["spoken:followup_due"] == 1
