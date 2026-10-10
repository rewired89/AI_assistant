"""The LLM responder inside the real voice pipeline and simulated device."""
from companion.conversation import FakeLanguageModel, LLMUnavailable
from companion.speech import Reply, State
from companion.speech.lines import load_lines
from simulator import PowerState as P
from tests.companion.test_conversation import SP, call, make
from tests.companion.test_voice_pipeline import FakeRecognizer, FakeSynthesizer, Rig, ScriptedResponder, rig, spoken  # noqa: F401


def sleepy_llm(**args):
    return FakeLanguageModel([{"text": "Sure, napping.", "tool_calls": [call("request_sleep", **args)]}])


def test_sleep_request_runs_only_after_the_speech_has_played(rig, device, clock):
    eng, _ = make(clock, sleepy_llm(hour=8, minute=0, meridiem="pm", reason="battery_saving"))
    r = rig(stt=FakeRecognizer(["save battery now and wake me at 8 pm"]), responder=eng)
    r.utter()
    r.run(lambda: device.playback is not None and device.playback["received"] > 0)
    assert device.power is P.AWAKE                                   # still talking: the command must wait
    r.run(lambda: device.power is P.SLEEPING)
    assert r.pipe.stats["actions_sent"] == 1 and device.state["sleep_reason"] == "battery_saving"
    assert device.state["wake_at"].endswith("-05:00") and "T20:00:00" in device.state["wake_at"]
    assert spoken(r) == "Okay, I'll nap until 8:00 PM."
    assert all(res["result"] == "completed" for res in r.client.playback_results.values())   # was not cut off


def test_pressing_the_button_during_speech_cancels_the_pending_sleep(rig, device, clock):
    eng, _ = make(clock, sleepy_llm(hour=8, minute=0, meridiem="pm"))
    r = rig(stt=FakeRecognizer(["go to sleep and wake me at 8 pm"]), responder=eng)
    r.utter()
    r.run(lambda: device.playback is not None and device.playback["received"] > 0)
    r.press()
    r.run(lambda: r.pipe.state is State.IDLE and device.playback is None)
    for _ in range(100):
        r.pipe.poll(); clock.advance(0.05)
    assert device.power is P.AWAKE and r.pipe.stats["actions_sent"] == 0


def test_only_whitelisted_actions_can_follow_speech(rig, device):
    bad = Reply("Okay.", after_speech=(("power.wake", {"reason": "host_request"}),
                                       ("device.set_config", {"config": {"mic_enabled": False}}),
                                       ("security.rotate_key", {"kid": 2, "key": "ab" * 32})))
    r = rig(responder=ScriptedResponder([bad]))
    r.utter(); r.idle()
    assert r.pipe.stats["blocked_actions"] == 3 and r.pipe.stats["actions_sent"] == 0
    assert device.state["config"]["mic_enabled"] is True and device.auth.ring.kids() == [1]


def test_device_refusing_an_action_is_recorded_not_fatal(rig, device):
    bad = Reply("Okay.", after_speech=(("power.sleep", {"reason": "user_request", "requested_by": "user",
                                                       "wake_at": "2001-01-01T00:00:00+00:00"}),))
    r = rig(responder=ScriptedResponder([bad]))
    r.utter(); r.idle()
    assert r.pipe.stats["action_errors"] == 1 and device.power is P.AWAKE and r.pipe.state is State.IDLE


def test_emotion_hint_is_shown_after_speaking(rig, device):
    r = rig(responder=ScriptedResponder([Reply("Congratulations.", animation="celebratory")]))
    r.utter(); r.idle()
    r.run(lambda: any(e["type"] == "animation.finished" and e["payload"]["name"] == "celebratory" for e in r.client.events))


def test_model_outage_is_spoken_as_scripted_line(rig, clock):
    eng, _ = make(clock, FakeLanguageModel([LLMUnavailable("connection refused")]))
    r = rig(responder=eng)
    r.utter(); r.idle()
    assert spoken(r) == load_lines(SP / "ninetales")["trouble_thinking"] and r.pipe.stats["reply_errors"] == 1


def test_unsafe_model_output_never_reaches_the_speaker(rig, clock):
    eng, _ = make(clock, FakeLanguageModel(["Don't leave me, please.", "You left me alone for hours."]))
    r = rig(responder=eng)
    r.utter(); r.idle()
    assert spoken(r) == "I'm not sure how to put that. Could you ask me another way?"


def test_memory_and_name_flow_through_a_voice_turn(rig, clock, tmp_path):
    from companion.memory import MemoryStore
    mem = MemoryStore(tmp_path / "m.db", "her", clock)
    llm = FakeLanguageModel(["Nice to meet you.", "You love Vulpix."])
    eng, _ = make(clock, llm, memory=mem, name="José")
    r = rig(stt=FakeRecognizer(["I love Vulpix", "what do I love vulpix"]), responder=eng)
    r.utter(); r.idle()
    assert mem.search("vulpix")
    r.utter(); r.idle()
    system = llm.calls[1]["system"]
    assert '"José"' in system and "Vulpix" in system
