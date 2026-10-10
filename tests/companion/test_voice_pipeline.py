import random
import threading
import time
from pathlib import Path

import pytest

from companion.application.device_client import DeviceClient, LoopbackTransport
from companion.application.onboarding import Onboarding, load_copy
from companion.behavior import BehaviorEngine
from companion.personality import config as pc
from companion.speech import PipelineConfig, Reply, State, VoicePipeline, load_lines
from companion.speech.fakes import FakeRecognizer, FakeSynthesizer, silence, tone
from companion.tamagotchi import CharacterState, load_rules
from protocol import Authenticator, audio as a
from tests.conftest import SECRET

SP = Path(__file__).parents[2] / "species"


class ScriptedResponder:
    def __init__(self, replies=None, fail=None, block=None):
        self.replies, self.fail, self.block, self.heard = list(replies or []), fail, block, []

    def respond(self, utterance):
        self.heard.append(utterance)
        if self.block is not None:
            self.block.wait(30)
        if self.fail:
            raise self.fail
        return self.replies.pop(0) if self.replies else Reply("Okay.")


def spoken(rig):
    return " ".join(t for t, _ in rig.tts.calls)


class Rig:
    def __init__(self, device, clock, stt, tts, responder, onboarding=None, with_engine=True, lines=None, cfg=None):
        self.device, self.clock = device, clock
        self.client = DeviceClient(LoopbackTransport(device, secure=True), Authenticator(SECRET, clock), clock)
        engine = None
        if with_engine:
            personality = pc.Personality(pc.load(SP / "ninetales" / "personality.yaml"))
            engine = BehaviorEngine(personality, CharacterState(load_rules(SP / "ninetales" / "growth.yaml"), clock.t),
                                    clock, random.Random(1))
        self.stt, self.tts, self.responder = stt, tts, responder
        self.pipe = VoicePipeline(self.client, stt, tts, responder, clock, engine=engine, onboarding=onboarding,
                                  lines=lines or load_lines(SP / "ninetales"), config=cfg or PipelineConfig())

    def press(self, kind="single"):
        self.client.transport._take(self.device.press_button(kind))

    def mic(self, pcm):
        self.device.feed_mic(pcm)

    def run(self, cond, limit=4000, step=0.02):
        for _ in range(limit):
            self.pipe.poll()
            if cond():
                return
            self.clock.advance(step)
            time.sleep(0.0005)
        raise AssertionError(f"condition not reached; state={self.pipe.state} log={list(self.pipe.log)[-5:]}")

    def utter(self, speech_s=0.8, trailing_s=1.3):
        """Press, talk, go quiet: leaves the pipeline at the point where it has decided to stop listening."""
        self.press()
        self.run(lambda: self.pipe.state is State.LISTENING)
        self.mic(tone(speech_s))
        self.mic(silence(trailing_s))

    def until_state(self, state):
        self.run(lambda: self.pipe.state is state)

    def idle(self):
        self.run(lambda: self.pipe.state is State.IDLE and self.device.playback is None)


@pytest.fixture
def rig(device, clock):
    made = []

    def _make(stt=None, tts=None, responder=None, **kw):
        r = Rig(device, clock, stt or FakeRecognizer(["what is your name"]), tts or FakeSynthesizer(),
                responder or ScriptedResponder([Reply("I am Nine Tails.", "happy")]), **kw)
        made.append(r)
        return r
    yield _make
    for r in made:
        r.pipe.close()


# ------------------------------------------------------------------ happy path
def test_full_voice_turn_with_animations(rig, device):
    r = rig()
    r.utter()
    r.idle()
    assert r.responder.heard == ["what is your name"]
    assert r.tts.calls == [("I am Nine Tails.", "happy")]
    fin = list(r.client.playback_results.values())
    assert len(fin) == 1 and fin[0]["result"] == "completed" and fin[0]["underruns"] == 0
    assert r.stt.calls and r.stt.calls[0] >= a.BYTES_PER_SECOND * 0.8
    assert r.client.captures == {} and device.capture is None
    assert r.pipe.stats["turns"] == 1 and r.pipe.state is State.IDLE
    names = [e["payload"]["name"] for e in r.client.events if e["type"] == "animation.finished"]
    assert "listening" in names and "thinking" in names and "talking" in names     # each stage was shown on the device
    assert device.animation is None or device.animation["name"] not in ("listening", "thinking", "talking")


def test_multi_sentence_reply_is_spoken_in_order_with_prefetch(rig):
    r = rig(responder=ScriptedResponder([Reply("First one. Second one. Third one.", "calm")]))
    r.utter()
    r.run(lambda: r.pipe.state is State.SPEAKING)
    r.run(lambda: len(r.tts.calls) >= 2)           # next sentence synthesized while the first is still playing
    assert r.pipe.state is State.SPEAKING
    r.idle()
    assert [t for t, _ in r.tts.calls] == ["First one.", "Second one.", "Third one."]
    assert len(r.client.playback_results) == 3


def test_invalid_style_falls_back_to_normal(rig):
    r = rig(responder=ScriptedResponder([Reply("Hello there.", "evil; DROP")]))
    r.utter(); r.idle()
    assert r.tts.calls == [("Hello there.", "normal")]


def test_reply_text_is_sanitized_and_truncated(rig):
    r = rig(responder=ScriptedResponder([Reply("Hi\x00 there‮. " + "word " * 1000)]),
            cfg=PipelineConfig(max_reply_chars=40, max_sentences=2))
    r.utter(); r.idle()
    spoken = " ".join(t for t, _ in r.tts.calls)
    assert "\x00" not in spoken and len(spoken) <= 41


# ------------------------------------------------------------------ nothing heard
def test_silence_never_reaches_stt_or_llm(rig):
    r = rig()
    r.press(); r.run(lambda: r.pipe.state is State.LISTENING)
    r.mic(silence(3.0))
    r.press()                                            # user gives up
    r.idle()
    assert r.stt.calls == [] and r.responder.heard == []
    assert spoken(r) == load_lines(SP / "ninetales")["didnt_hear"]
    assert r.pipe.stats["no_speech"] == 1


def test_empty_transcript_says_didnt_hear(rig):
    r = rig(stt=FakeRecognizer([" ... "]))
    r.utter(); r.idle()
    assert r.responder.heard == [] and r.pipe.stats["empty_transcript"] == 1
    assert "missed" in spoken(r)


def test_second_press_ends_listening_early(rig):
    r = rig()
    r.press(); r.run(lambda: r.pipe.state is State.LISTENING)
    r.mic(tone(0.7))
    r.press()
    r.idle()
    assert r.responder.heard == ["what is your name"]
    assert any(n == "listen_end:button" for _, n in r.pipe.log)


def test_overtime_capture_is_closed(rig, clock):
    r = rig(cfg=PipelineConfig(max_listen_s=2))
    r.press(); r.run(lambda: r.pipe.state is State.LISTENING)
    r.mic(tone(0.5))
    r.idle()                                             # no silence ever arrives: device timeout / overtime ends it
    assert r.responder.heard == ["what is your name"]


def test_corrupt_audio_is_not_transcribed(rig):
    r = rig()
    r.press(); r.run(lambda: r.pipe.state is State.LISTENING)
    cap = next(iter(r.client.captures.values()))
    r.client.transport._binbox.append(a.pack(a.KIND_CAPTURE, cap.stream_no, 0, tone(0.5)[:1920]))
    r.client.transport._binbox.append(a.pack(a.KIND_CAPTURE, cap.stream_no, 5, tone(0.5)[:1920]))   # gap
    r.press()
    r.idle()
    assert r.stt.calls == [] and r.pipe.stats["corrupt_audio"] == 1


# ------------------------------------------------------------------ cancellation and barge-in
def test_press_while_transcribing_cancels(rig):
    gate = threading.Event()
    r = rig(stt=FakeRecognizer(["secret words"], block=gate))
    r.utter()
    r.until_state(State.TRANSCRIBING)
    r.press()
    r.run(lambda: r.pipe.state is State.IDLE)
    gate.set()
    for _ in range(50):
        r.pipe.poll(); time.sleep(0.002)
    assert r.responder.heard == [] and r.pipe.state is State.IDLE and r.pipe.stats["cancelled"] == 1


def test_press_while_thinking_cancels_and_discards_reply(rig):
    gate = threading.Event()
    r = rig(responder=ScriptedResponder([Reply("late answer")], block=gate))
    r.utter()
    r.until_state(State.THINKING)
    r.press()
    r.run(lambda: r.pipe.state is State.IDLE)
    gate.set()
    for _ in range(50):
        r.pipe.poll(); time.sleep(0.002)
    assert r.tts.calls == [] and r.pipe.state is State.IDLE


def test_barge_in_stops_the_speaker(rig, device):
    r = rig(responder=ScriptedResponder([Reply("This is a rather long answer that keeps going for a while, so I can be interrupted.")]))
    r.utter()
    r.run(lambda: device.playback is not None and device.playback["received"] > 0)
    r.press()
    r.run(lambda: r.pipe.state is State.IDLE)
    r.run(lambda: device.playback is None)
    results = list(r.client.playback_results.values())
    assert results and results[-1]["result"] == "interrupted" and r.pipe.stats["interrupted"] == 1


# ------------------------------------------------------------------ failures degrade politely
def test_stt_error_and_timeout_speak_scripted_line(rig, clock):
    r = rig(stt=FakeRecognizer(["x"], fail=RuntimeError("model crashed")))
    r.utter(); r.idle()
    assert spoken(r) == load_lines(SP / "ninetales")["trouble_hearing"] and r.responder.heard == []
    gate = threading.Event()
    r2 = rig(stt=FakeRecognizer(["x"], block=gate), cfg=PipelineConfig(stt_timeout_s=5))
    r2.utter(); r2.until_state(State.TRANSCRIBING)
    clock.advance(6)
    r2.idle()
    gate.set()
    assert r2.pipe.stats["stt_timeouts"] == 1 and "trouble" in spoken(r2)


def test_responder_failure_speaks_scripted_line_not_exception_text(rig):
    r = rig(responder=ScriptedResponder(fail=RuntimeError("api key sk-secret rejected")))
    r.utter(); r.idle()
    said = spoken(r)
    assert said == load_lines(SP / "ninetales")["trouble_thinking"] and "sk-secret" not in said


def test_tts_failure_and_bad_audio_do_not_wedge(rig, device):
    for tts in (FakeSynthesizer(fail=RuntimeError("voice missing")), FakeSynthesizer(bad_output=b""),
                FakeSynthesizer(bad_output=b"\x01\x02\x03")):
        r = rig(tts=tts)
        r.utter(); r.idle()
        assert r.pipe.state is State.IDLE and r.pipe.stats["tts_errors"] == 1 and device.playback is None


def test_device_asleep_mid_turn_recovers_to_idle(rig, device, clock):
    r = rig()
    r.press(); r.run(lambda: r.pipe.state is State.LISTENING)
    r.client.request("power.sleep", {"reason": "user_request", "requested_by": "user"})
    r.pipe.poll()
    assert r.pipe.state is State.IDLE and r.pipe.stats["device_sleep"] == 1 and r.client.captures == {}
    assert r.tts.calls == [] and r.stt.calls == []          # no speech attempted into a sleeping device


# ------------------------------------------------------------------ the device decides; we only explain
def test_muted_mic_is_respected_and_explained(rig, device):
    r = rig()
    r.client.request("device.set_config", {"config": {"mic_enabled": False}})
    r.press()
    r.idle()
    assert device.capture is None and r.pipe.stats["capture_refused"] == 1
    assert spoken(r) == load_lines(SP / "ninetales")["mic_muted"]
    assert device.state["config"]["mic_enabled"] is False            # never tried to turn it back on


def test_low_battery_is_explained_from_script(rig, device):
    r = rig()
    r.client.transport._take(device.set_battery(8))
    r.press(); r.idle()
    assert device.capture is None and "battery" in spoken(r).lower()


def test_microphone_only_opens_on_button_press(rig, device):
    r = rig()
    for _ in range(200):
        r.pipe.poll(); r.clock.advance(0.1)
    assert device.capture is None and r.pipe.stats["turns"] == 0
    r.press("double"); r.press("long")                                 # other gestures never start listening
    for _ in range(20):
        r.pipe.poll()
    assert device.capture is None


# ------------------------------------------------------------------ onboarding by voice
def test_onboarding_answers_are_routed_before_the_responder(rig, tmp_path, clock):
    ob = Onboarding(tmp_path / "ob.json", load_copy(SP / "ninetales"), pc.load(SP / "ninetales" / "personality.yaml"))
    first = ob.next_prompt(clock.t)
    r = rig(stt=FakeRecognizer(["call me José", "keep", "tell me a joke"]), onboarding=ob,
            responder=ScriptedResponder([Reply("A fox walks into a bar.")]))
    r.utter(); r.idle()
    assert r.responder.heard == [] and ob.user_name() == "José"
    assert "José" in spoken(r) and "call me" in spoken(r).lower()                      # ack + follow-up question
    r.utter(); r.idle()
    assert ob.core_done and r.responder.heard == []
    r.utter(); r.idle()
    assert r.responder.heard == ["tell me a joke"]                                     # normal conversation resumes


def test_offer_onboarding_speaks_without_opening_mic(rig, tmp_path, clock, device):
    ob = Onboarding(tmp_path / "ob.json", load_copy(SP / "ninetales"), pc.load(SP / "ninetales" / "personality.yaml"))
    r = rig(onboarding=ob)
    assert r.pipe.offer_onboarding(clock.t) is True
    r.idle()
    assert "hatched" in spoken(r) and device.capture is None and ob.awaiting() == "user_name"
    assert r.pipe.offer_onboarding(clock.t) is False        # already asked, waiting for the answer


def test_speak_refused_while_busy(rig):
    r = rig()
    assert r.pipe.speak("Hello there.") is True
    assert r.pipe.speak("Another one.") is False
    r.idle()


# ------------------------------------------------------------------ privacy
def test_no_audio_or_transcript_is_retained(rig):
    r = rig(stt=FakeRecognizer(["my private sentence about secrets"]),
            responder=ScriptedResponder([Reply("Noted.")]))
    r.utter(); r.idle()
    blob = repr(list(r.pipe.log)) + repr(dict(r.pipe.stats))
    assert "secrets" not in blob and r.client.captures == {}
    assert not any(isinstance(v, (bytes, bytearray)) and len(v) > 64 for v in vars(r.pipe).values())


def test_pipeline_works_without_behavior_engine(rig):
    r = rig(with_engine=False)
    r.utter(); r.idle()
    assert r.responder.heard == ["what is your name"]


def test_unresponsive_device_counts_error_and_stays_usable(rig, device):
    from simulator import PowerState as P
    r = rig()
    device.power = P.SLEEPING                   # went quiet without telling us (e.g. link dropped)
    r.pipe._presses = 1
    r.pipe.poll()
    assert r.pipe.stats["device_errors"] == 1 and r.pipe.state is State.IDLE and r.stt.calls == []
    device.power = P.AWAKE
    r.utter(); r.idle()
    assert r.responder.heard == ["what is your name"]
