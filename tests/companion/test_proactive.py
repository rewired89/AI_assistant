import time
from datetime import datetime, timedelta, timezone

import pytest

from companion.application.proactive import DirectorConfig, ProactiveDirector
from companion.behavior import Intent
from companion.conversation import ConversationEngine, FakeLanguageModel, LLMUnavailable
from companion.conversation.dialogue import DEFAULTS, DialogueError, LinePicker, load_templates
from companion.memory import MemoryStore
from companion.personality import config as pc
from companion.speech import Reply, State
from companion.tamagotchi import StateStore, load_rules
from simulator import PowerState as P
from tests.companion.test_conversation import SP, call
from tests.companion.test_voice_pipeline import FakeRecognizer, Rig, ScriptedResponder, rig, spoken  # noqa: F401
import random

D = 86400


class World:
    def __init__(self, rig, clock, llm=None, memory=None, proactive="frequent", name="Sam", state_store=None,
                 curiosity=0.95, playfulness=0.3, psychology=None, safety=None, insights=None, checkin=None, checkin_blocked=lambda: False, reminders=None, remind_talk=None, **dcfg):
        self.clock = clock
        self.r = rig(responder=ScriptedResponder([Reply("Okay.")]))
        self.beh = self.r.pipe.engine
        self.beh.p.cfg["behavior"]["proactive_questions"] = proactive
        self.beh.state.curiosity, self.beh.state.playfulness, self.beh.state.energy = curiosity, playfulness, 1.0
        self.llm = llm or FakeLanguageModel()
        cfg = pc.load(SP / "ninetales" / "personality.yaml")
        self.conv = ConversationEngine(self.llm, lambda: cfg, self.beh.state, clock, memory=memory,
                                       user_name=lambda: name,
                                       dialogue=LinePicker(load_templates(SP / "ninetales"), random.Random(2)),
                                       safety=safety, psychology=psychology, insights=insights, checkin=checkin)
        self.r.pipe.responder = self.conv
        self.d = ProactiveDirector(self.beh, self.conv, self.r.pipe, clock, memory=memory, state_store=state_store,
                                   config=DirectorConfig(**dcfg), rng=random.Random(3), psychology=psychology, insights=insights,
                                   checkin=checkin, checkin_blocked=checkin_blocked, reminders=reminders, remind_talk=remind_talk,
                                   reminder_name=lambda: name)
        self.device = self.r.device

    def drive(self, seconds, stop=None, coarse=20.0, fine=0.05):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.r.pipe.poll()
            self.d.tick()
            if stop and stop():
                return True
            busy = self.r.pipe.state is not State.IDLE or self.d._composing is not None
            self.clock.advance(fine if busy else coarse)
            time.sleep(0.0003)
        return False

    def settle(self):
        self.drive(30, stop=lambda: self.r.pipe.state is State.IDLE and self.d._composing is None
                   and self.device.playback is None and not self.d._pending and not self.d._stimuli)
        for _ in range(40):
            self.r.pipe.poll(); self.d.tick(); time.sleep(0.001)


@pytest.fixture
def world(rig, clock):
    made = []

    def _make(**kw):
        w = World(rig, clock, **kw)
        made.append(w)
        return w
    yield _make
    for w in made:
        w.d.close()


# ------------------------------------------------------------------ idle chatter
def test_curious_question_is_spoken_when_conditions_allow(world):
    w = world(llm=FakeLanguageModel(["What's your favorite color?"] * 20))
    assert w.drive(3 * 3600, stop=lambda: w.d.stats["spoken"] >= 1)
    w.settle()
    assert "favorite color" in spoken(w.r) and w.d.stats["spoken:curious_question"] >= 1
    assert w.device.capture is None and w.r.pipe.stats["turns"] == 0       # never opens the mic by itself
    assert all(r["result"] == "completed" for r in w.r.client.playback_results.values())


def test_model_down_falls_back_to_scripted_lines(world):
    w = world(llm=FakeLanguageModel([LLMUnavailable("down")] * 20))
    assert w.drive(3 * 3600, stop=lambda: w.d.stats["spoken"] >= 1)
    w.settle()
    assert w.conv.stats["proactive_fallback"] >= 1
    assert spoken(w.r) in " ".join(load_templates(SP / "ninetales")["curious_question"])


@pytest.mark.parametrize("bad", [
    "I missed you so much, where were you?", "Welcome back! It's been a long time.", "Please don't leave me again, okay?",
    "You probably have an anxiety disorder, right?", "What did you do today? I know you were at work.",
    "Hello there.",                                    # not a question: curious_question must ask
    "What are you up to? You were busy all day, I bet.",
    "Is it " + "very " * 40 + "nice?"])              # too long
def test_unsafe_or_off_script_proactive_text_is_replaced(world, bad):
    w = world(llm=FakeLanguageModel([bad] * 20))
    w.drive(3 * 3600, stop=lambda: w.d.stats["spoken"] >= 1)
    w.settle()
    assert bad.split()[0:3] != spoken(w.r).split()[0:3]
    assert w.conv.stats["proactive_fallback"] >= 1 and w.conv.stats["proactive_llm"] == 0


def test_proactive_prompt_is_tightly_grounded_and_toolless(world):
    w = world(llm=FakeLanguageModel(["What do you like?"] * 20))
    w.drive(3 * 3600, stop=lambda: w.llm.calls)
    c = w.llm.calls[0]
    assert c["tools"] is None
    for must in ("Never mention her absence", "You have no facts to rely on", "at most 25 words", "ONE curious open question"):
        assert must in c["system"]
    assert c["messages"][-1]["content"] == "(it is your turn to speak first)"


def test_never_means_never_but_wake_greeting_rules_still_apply(world):
    w = world(proactive="never")
    w.drive(6 * 3600)
    assert w.d.stats["spoken"] == 0 and w.llm.calls == []


def test_quiet_mode_is_silent_but_alive(world):
    w = world()
    w.beh.settings.quiet_mode = True
    w.drive(2 * 3600)
    assert w.d.stats["spoken"] == 0 and w.llm.calls == [] and w.device.animation is not None


def test_nothing_is_said_while_she_is_talking(world):
    w = world(llm=FakeLanguageModel(["What do you like?"] * 20))
    w.r.press()
    w.r.run(lambda: w.r.pipe.state is State.LISTENING)
    w.drive(10)                                         # idle time while the mic is open
    assert w.d.stats["spoken"] == 0 and w.d.stats["expired"] == 0 and w.r.pipe.state is State.LISTENING
    assert w.llm.calls == [] and not w.d._pending


# ------------------------------------------------------------------ device events
def test_waking_from_scheduled_battery_nap_explains_itself(world):
    w = world(llm=FakeLanguageModel(["I'm awake again after my nap."]), proactive="never")
    wake = (datetime.fromtimestamp(w.clock.t, timezone.utc) + timedelta(seconds=40)).isoformat(timespec="seconds")
    w.r.client.request("power.sleep", {"reason": "battery_saving", "requested_by": "user", "wake_at": wake})
    w.drive(5)
    assert w.d.asleep and w.beh.state.sleeping
    assert w.drive(120, stop=lambda: w.d.stats["spoken:greet_after_battery_saving"] >= 1, coarse=2.0)
    w.settle()
    assert not w.beh.state.sleeping and "awake again after my nap" in spoken(w.r)
    assert "woke_from_battery_saving" in w.llm.calls[0]["system"] or "awake again after a nap" in w.llm.calls[0]["system"]


def test_host_restart_during_a_nap_still_explains_the_wake(world):
    """Spec 33: the device remembers why it slept; a host that restarted in between must not lose that."""
    w = world(llm=FakeLanguageModel(["I'm awake again after my nap."]), proactive="never")
    wake = (datetime.fromtimestamp(w.clock.t, timezone.utc) + timedelta(seconds=40)).isoformat(timespec="seconds")
    w.r.client.request("power.sleep", {"reason": "battery_saving", "requested_by": "user", "wake_at": wake})
    w.drive(5)
    assert w.d.asleep
    w.d._sleep_reason = None                              # the host forgot (as after a restart); only the device knows
    assert w.drive(120, stop=lambda: w.d.stats["spoken:greet_after_battery_saving"] >= 1, coarse=2.0)


def test_touch_gets_a_warm_reply_only_while_awake(world):
    w = world(llm=FakeLanguageModel(["Hehe, that tickles!"] * 5), proactive="never")
    w.r.client.transport._take(w.device.press_button("single"))     # (button is not touch: ignored)
    w.d._stimuli.append(("touch", {}))
    w.drive(5, stop=lambda: w.d.stats["spoken:comfort_touch_reply"] >= 1, fine=0.05)
    w.settle()
    assert "tickles" in spoken(w.r)
    w.beh.state.sleeping = True
    n = w.d.stats["spoken"]
    w.d._stimuli.append(("touch", {}))
    w.drive(5)
    assert w.d.stats["spoken"] == n


def test_battery_offer_once_per_episode_and_silence_when_low(world):
    w = world(llm=FakeLanguageModel(["Shall I take a nap to save power?"] * 6), proactive="never")
    w.r.client.transport._take(w.device.set_battery(19)); w.settle()
    assert w.d.stats["spoken:offer_save_battery"] == 1
    w.r.client.transport._take(w.device.set_battery(18)); w.settle()
    assert w.d.stats["spoken:offer_save_battery"] == 1                 # same episode
    w.r.client.transport._take(w.device.set_battery(8)); w.settle()
    assert w.d.stats["spoken"] == 1                                    # low battery: no chatter
    w.r.client.transport._take(w.device.set_battery(60)); w.settle()
    w.r.client.transport._take(w.device.set_battery(19)); w.settle()
    assert w.d.stats["spoken:offer_save_battery"] == 2


def test_stale_intents_expire_instead_of_being_said_late(world):
    w = world(proactive="never", pending_ttl_s=10)
    w.r.press()
    w.r.run(lambda: w.r.pipe.state is State.LISTENING)
    w.d._pending.append((w.clock.t + 10, Intent("greet", grounding=["relationship_new"]), "normal"))
    w.drive(60)
    assert w.d.stats["expired"] == 1 and w.d.stats["spoken"] == 0


def test_intents_are_dropped_when_device_falls_asleep(world):
    w = world(proactive="never")
    w.d._pending.append((w.clock.t + 30, Intent("greet", grounding=["relationship_new"]), "normal"))
    w.r.client.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    w.drive(60)
    assert w.d.stats["spoken"] == 0 and not w.d._pending


# ------------------------------------------------------------------ growth
def test_voice_turn_feeds_growth_and_milestone_is_celebrated(world):
    w = world(llm=FakeLanguageModel(["Hello!"] * 50 + ["I feel like I grew!"] * 5), proactive="never")
    w.r.stt = w.r.pipe.stt = FakeRecognizer(["hello fox"] * 40)
    stages = []
    for _ in range(40):
        w.r.utter(); w.r.idle()
        w.d.tick()
        w.clock.advance(120)
        w.settle()
        stages.append(w.beh.state.stage)
        if w.beh.state.stage != "EGG":
            break
    assert w.beh.state.experience > 0 and w.beh.state.kind_counts.get("conversation", 0) >= 1
    # a milestone needs variety (>=2 kinds): feed a play turn via the hook, as the engine would classify it
    from companion.speech import Reply as R
    w.r.pipe.on_reply(R("ok", interaction="play"))
    w.settle()
    assert w.beh.state.kind_counts.get("play") == 1
    assert w.beh.state.stage in ("BABY", "YOUNG")


def test_milestone_line_mentions_the_new_stage(world):
    w = world(proactive="never", llm=FakeLanguageModel([LLMUnavailable("down")] * 3))
    w.beh.state.experience = 4.9
    w.beh.state.kind_counts = {"conversation": 3}
    w.d._stimuli.append(("interaction", {"kind": "play"}))
    w.drive(20, stop=lambda: w.d.stats["spoken:milestone"] >= 1, fine=0.05)
    w.settle()
    assert w.beh.state.stage == "BABY" and "baby fox" in spoken(w.r)


def test_interaction_classification():
    c = ConversationEngine._classify
    assert c("let's play a game", []) == "play" and c("did you know foxes dig dens", []) == "teach"
    assert c("tell me about stars", []) == "explore_topic" and c("hello", []) == "conversation"
    from companion.conversation.tools import Remember
    assert c("my cat is named Momo", [Remember("semantic", "x", None)]) == "teach"
    assert c("my cat is named Momo", [Remember("profile", "x", None)]) == "conversation"


def test_state_is_saved_after_interactions_and_periodically(world, tmp_path):
    rules = load_rules(SP / "ninetales" / "growth.yaml")
    store = StateStore(tmp_path / "c.json", rules)
    w = world(proactive="never", state_store=store, save_interval_s=100)
    w.d._stimuli.append(("interaction", {"kind": "conversation"}))
    w.d.tick()
    assert (tmp_path / "c.json").exists()
    first = (tmp_path / "c.json").stat().st_mtime_ns
    w.drive(300)
    assert (tmp_path / "c.json").stat().st_mtime_ns >= first
    assert store.load(w.clock.t).experience > 0


# ------------------------------------------------------------------ memories spoken aloud
def test_remembered_things_are_shared_once_per_week_and_never_after_deletion(world, tmp_path, clock):
    mem = MemoryStore(tmp_path / "m.db", "her", clock)
    mem.add({"category": "profile", "key": "favorite_pokemon", "value": "Vulpix"})
    mem.add({"category": "profile", "key": "user_name", "value": "Sam"})
    mem.add({"category": "profile", "key": "hidden", "value": "x" * 120})
    w = world(llm=FakeLanguageModel(["You love Vulpix, right?"] * 30), memory=mem, curiosity=0.3, playfulness=0.3)
    assert w.drive(6 * 3600, stop=lambda: w.d.stats["spoken:share_interest"] >= 1, coarse=60)
    w.settle()
    assert "Vulpix" in spoken(w.r)
    n = w.d.stats["spoken:share_interest"]
    for _ in range(6):                                                # six days: a fed, rested creature (energy topped up daily)
        w.beh.state.energy = 1.0
        w.drive(D, coarse=600)
    assert w.d.stats["spoken:share_interest"] == n                    # reuse window; user_name and long values never used
    for _ in range(3):
        w.beh.state.energy = 1.0
        if w.drive(D, stop=lambda: w.d.stats["spoken:share_interest"] > n, coarse=600):
            break
    assert w.d.stats["spoken:share_interest"] > n


def test_share_interest_must_actually_be_about_the_memory(world, tmp_path, clock):
    mem = MemoryStore(tmp_path / "m.db", "her", clock)
    mid = mem.add({"category": "profile", "key": "favorite_pokemon", "value": "Vulpix"})
    w = world(llm=FakeLanguageModel(["Do you like the weather?"] * 5), memory=mem, proactive="never")
    w.d._pending.append((clock.t + 30, Intent("share_interest", grounding=[f"memory:{mid}"]), "normal"))
    w.drive(20, stop=lambda: w.d.stats["spoken"] >= 1, fine=0.05)
    w.settle()
    assert "Vulpix" in spoken(w.r) and w.conv.stats["proactive_fallback"] == 1


def test_deleted_or_disabled_memory_is_not_spoken(world, tmp_path, clock):
    mem = MemoryStore(tmp_path / "m.db", "her", clock)
    mid = mem.add({"category": "profile", "key": "favorite_pokemon", "value": "Vulpix"})
    w = world(memory=mem, proactive="never")
    mem.delete(mid)
    w.d._pending.append((clock.t + 30, Intent("share_interest", grounding=[f"memory:{mid}"]), "normal"))
    w.drive(20)
    assert w.d.stats["intent_unspeakable"] == 1 and w.d.stats["spoken"] == 0
    mid2 = mem.add({"category": "profile", "key": "color", "value": "blue"})
    mem.set_enabled("profile", False)
    assert w.conv.compose_intent(Intent("share_interest", grounding=[f"memory:{mid2}"])) is None


def test_speak_memories_switch_and_privacy_of_stats(world, tmp_path, clock):
    mem = MemoryStore(tmp_path / "m.db", "her", clock)
    mem.add({"category": "profile", "key": "favorite_pokemon", "value": "Vulpix"})
    w = world(memory=mem, speak_memories=False, curiosity=0.3, playfulness=0.3)
    w.drive(4 * 3600, coarse=60)
    assert w.d.stats["spoken:share_interest"] == 0
    assert all(isinstance(k, str) and "Vulpix" not in k for k in w.d.stats)


# ------------------------------------------------------------------ dialogue templates
def test_templates_load_validate_and_vary(tmp_path):
    t = load_templates(SP / "ninetales")
    assert t["curious_question"][0].startswith("Ooh") and t["greet"]["close"][0].startswith("{name}")
    assert load_templates(None) == DEFAULTS
    p = LinePicker(t, random.Random(1))
    seen = [p.pick("curious_question") for _ in range(6)]
    assert all(a != b for a, b in zip(seen, seen[1:]))
    for bad in ("intents:\n  greet:\n    new: ['{secret}']\n    familiar: ['x']\n    close: ['x']\n",
                "intents:\n  made_up_kind: ['x']\n", "intents:\n  curious_question: []\n",
                "intents:\n  greet: ['flat list']\n", "intents:\n  curious_question: ['" + "a" * 300 + "']\n"):
        (tmp_path / "dialogue.yaml").write_text(bad)
        with pytest.raises(DialogueError):
            load_templates(tmp_path)


def test_every_intent_kind_has_a_scripted_line():
    from companion.behavior.engine import INTENT_KINDS
    assert set(DEFAULTS) == INTENT_KINDS


def test_compose_never_raises_and_remembers_what_it_said(world):
    w = world(llm=FakeLanguageModel([RuntimeError("boom"), {"text": 42}]), proactive="never")
    r1 = w.conv.compose_intent(Intent("play_invite", tone="excited", grounding=["playfulness_high"]))
    r2 = w.conv.compose_intent(Intent("play_invite", tone="not-a-style", grounding=["playfulness_high"]))
    assert r1.text and r1.style == "excited" and r2.text and r2.style == "normal"
    assert w.conv.history[-1]["role"] == "assistant"


# ------------------------------------------------------------------ daily psychology discoveries
def _insights(tmp_path, clock, mode="voice"):
    from datetime import datetime as _dt
    from companion.discoveries import DailyInsights, load_dir
    entries, _ = load_dir("content/discoveries")
    ins = DailyInsights(tmp_path / "i.json", entries, clock, rng=random.Random(1))
    ins.enabled, ins.mode = True, mode
    h = _dt.fromtimestamp(clock.t, timezone.utc)
    ins.time_hm = (h.hour, h.minute)
    return ins


def test_scheduled_discovery_is_spoken_once_a_day_and_only_when_idle(world, tmp_path, clock):
    ins = _insights(tmp_path, clock)
    w = world(proactive="never", insights=ins)
    assert w.drive(600, stop=lambda: w.d.stats["insight_spoken"] >= 1)
    w.settle()
    assert "psychology discovery" in spoken(w.r)
    assert w.device.capture is None                              # speaking only, the mic stays closed
    w.drive(6 * 3600)
    assert w.d.stats["insight_spoken"] == 1                      # no second one, no reminder
    assert w.llm.calls == []                                     # the claims come from the library, not a model


def test_discovery_waits_while_she_is_talking_and_is_spoken_after(world, tmp_path, clock):
    ins = _insights(tmp_path, clock)
    w = world(proactive="never", insights=ins)
    w.r.press()
    w.r.run(lambda: w.r.pipe.state is State.LISTENING)
    w.drive(2)
    assert w.r.pipe.state is State.LISTENING
    assert w.d.stats["insight_spoken"] == 0 and ins.last_scheduled_day is None   # not started, so not counted as delivered
    assert w.drive(120, stop=lambda: w.d.stats["insight_spoken"] >= 1)           # once she is done, still inside the window


def test_text_mode_goes_to_the_inbox_without_speaking(world, tmp_path, clock):
    ins = _insights(tmp_path, clock, mode="text")
    w = world(proactive="never", insights=ins)
    assert w.drive(600, stop=lambda: w.d.stats["insight_text"] >= 1)
    assert ins.inbox and w.d.stats["spoken"] == 0 and w.device.playback is None


def test_voice_commands_reach_the_insights_before_the_model(world, tmp_path, clock):
    from companion.discoveries import DailyInsights, load_dir
    ins = DailyInsights(tmp_path / "i.json", load_dir("content/discoveries")[0], clock, rng=random.Random(1))
    w = world(proactive="never", insights=ins)
    r = w.conv.respond("please turn on daily insights")
    assert "real research" in r.text and ins.enabled and w.llm.calls == []
    r = w.conv.respond("tell me a psychology discovery")
    assert "psychology discovery" in r.text and w.llm.calls == []


# ------------------------------------------------------------------ daily check-in
def _checkin(tmp_path, clock):
    from datetime import datetime as _dt
    from companion.checkin import CheckIn
    ci = CheckIn(tmp_path / "ci.json", clock, timezone.utc)
    ci.enabled = True
    h = _dt.fromtimestamp(clock.t, timezone.utc)
    ci.time_hm = (h.hour, h.minute)
    return ci


def test_the_check_in_question_is_spoken_once_a_day_and_waits_for_her_answer(world, tmp_path, clock):
    ci = _checkin(tmp_path, clock)
    w = world(proactive="never", checkin=ci)
    assert w.drive(600, stop=lambda: w.d.stats["checkin_asked"] >= 1)
    w.settle()
    assert "day" in spoken(w.r).lower() and ci.waiting
    w.drive(6 * 3600)
    assert w.d.stats["checkin_asked"] == 1 and w.llm.calls == []                    # no second question, no model


def test_the_check_in_holds_back_after_a_safety_signal_on_low_battery_and_after_a_discovery(world, tmp_path, clock):
    ci = _checkin(tmp_path, clock)
    blocked = {"on": True}
    w = world(proactive="never", checkin=ci, checkin_blocked=lambda: blocked["on"])
    w.drive(120)
    assert w.d.stats["checkin_asked"] == 0
    blocked["on"] = False
    w.d._battery = "low"
    w.drive(120)
    assert w.d.stats["checkin_asked"] == 0
    w.d._battery = "ok"
    from companion.discoveries import DailyInsights, load_dir
    ins = DailyInsights(tmp_path / "i2.json", load_dir("content/discoveries")[0], clock)
    ins.offer = {"id": "x", "t": clock.t}                                            # a discovery was just shared
    w.d.insights = ins
    w.drive(120)
    assert w.d.stats["checkin_asked"] == 0
    assert w.drive(40 * 60, stop=lambda: w.d.stats["checkin_asked"] >= 1)            # half an hour later it may ask


# ------------------------------------------------------------------ reminders spoken by the robot
def _reminders(tmp_path, clock, **kw):
    from companion.productivity import ProductivityStore
    from companion.productivity.reminders import ReminderStore
    from companion.productivity.remind_talk import RemindTalk
    store = ProductivityStore(tmp_path / "p.db", clock)
    rs = ReminderStore(store, clock, "UTC")
    due = datetime.fromtimestamp((int(clock.t // 60) + 2) * 60, timezone.utc)
    rs.create(title="Eat something", kind="once", date=due.strftime("%Y-%m-%d"), time=due.strftime("%H:%M"), tz="UTC", **kw)
    return store, rs, RemindTalk(rs, clock, "UTC")


def test_a_due_reminder_is_spoken_and_only_then_recorded_as_spoken(world, tmp_path, clock):
    store, rs, talk = _reminders(tmp_path, clock)
    w = world(proactive="never", reminders=rs, remind_talk=talk)
    assert w.drive(300, stop=lambda: w.d.stats["reminder_started"] >= 1)
    occ = rs.occurrences()[0]
    assert occ["robot"] == "speaking" and occ["status"] == "pending"                      # started is not done
    w.settle()
    occ = rs.occurrences()[0]
    assert occ["robot"] == "spoken" and occ["status"] == "delivered" and "around" in occ["spoken_text"]
    assert occ["spoken_text"] in spoken(w.r) and w.llm.calls == []                       # the words, from the library, no model
    w.drive(3600)
    assert w.d.stats["reminder_started"] == 1                                              # never repeated
    assert talk._ctx and talk._ctx["id"] == occ["id"]                                      # "snooze" now means something
    store.close()


def test_a_sleeping_robot_does_not_speak_and_the_record_never_says_it_did(world, tmp_path, clock):
    store, rs, talk = _reminders(tmp_path, clock, grace_minutes=5)
    w = world(proactive="never", reminders=rs, remind_talk=talk)
    w.r.client.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    w.drive(5)
    assert w.d.asleep
    w.drive(20 * 60)
    occ = rs.occurrences()[0]
    assert occ["status"] == "missed" and occ["robot"] == "unavailable" and occ["phone"] == "pending"
    assert w.d.stats["reminder_started"] == 0 and spoken(w.r) == ""
    assert "could not say it" in rs.reminders()[0]["last_delivery"]
    store.close()


def test_a_reminder_that_comes_due_while_she_is_talking_waits_then_speaks(world, tmp_path, clock):
    store, rs, talk = _reminders(tmp_path, clock)
    w = world(proactive="never", reminders=rs, remind_talk=talk)
    w.r.press()
    w.r.run(lambda: w.r.pipe.state is State.LISTENING)
    w.drive(60)
    assert w.d.stats["reminder_started"] == 0 or w.r.pipe.state is not State.LISTENING
    assert w.drive(300, stop=lambda: w.d.stats["reminder_started"] >= 1)
    store.close()


def test_a_reminder_speech_that_was_cut_off_is_recorded_as_failed(world, tmp_path, clock):
    store, rs, talk = _reminders(tmp_path, clock)
    w = world(proactive="never", reminders=rs, remind_talk=talk)
    assert w.drive(300, stop=lambda: w.d.stats["reminder_started"] >= 1)
    w.r.press()                                                                            # she interrupts by pressing the button
    w.settle()
    assert rs.occurrences()[0]["robot"] == "failed" and w.d.stats["reminder_failed"] == 1
    store.close()


def test_late_at_night_and_left_alone_she_is_put_to_bed_once(world):
    w = world(llm=FakeLanguageModel(["Okay."] * 5), proactive="never")
    w.beh.settings.quiet_hours = (0, 24)                        # always "late" for this test
    w.drive(300)
    assert w.d.stats["bedtime_requested"] == 0                  # not alone long enough yet
    w.drive(600)
    assert w.d.stats["bedtime_requested"] == 1 and w.device.power is P.SLEEPING
    w.drive(300)
    assert w.d.stats["bedtime_requested"] == 1


def test_bedtime_can_be_switched_off(world):
    w = world(llm=FakeLanguageModel(["Okay."] * 5), proactive="never", bedtime=False)
    w.beh.settings.quiet_hours = (0, 24)
    w.drive(2000)
    assert w.d.stats["bedtime_requested"] == 0 and w.device.power is not P.SLEEPING


def test_device_is_told_which_sprite_set_matches_her_stage(world):
    w = world(llm=FakeLanguageModel(["Okay."] * 5), proactive="never")
    w.beh.state.stage = "BABY"
    w.drive(60)
    assert w.d._sprite_sent == "baby" and w.device.state["config"].get("sprite_set") == "baby"
    w.beh.state.stage = "YOUNG"
    w.drive(60)
    assert w.device.state["config"].get("sprite_set") == "adult"
