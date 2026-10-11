import pytest

from companion.conversation import ConversationEngine, FakeLanguageModel
from companion.speech import Reply, State
from companion.speech.pipeline import plan_speech
from tests.companion.test_conversation import SP
from tests.companion.test_voice_pipeline import FakeRecognizer, ScriptedResponder, rig, spoken  # noqa: F401


def plan(text, paced=True, trailing=(), n=8):
    return plan_speech(text, paced, trailing, n, 300)


def test_markers_become_silences_between_sentences():
    assert plan("In for four. [pause 4] Out for six. [pause 6.5]") == ["In for four.", 4.0, "Out for six.", 6.5]
    assert plan("Hello.", trailing=[3]) == ["Hello.", 3.0]


def test_untrusted_text_can_never_produce_silence():
    assert plan("Hello [pause 20] there. [pause 5]", paced=False) == ["Hello there."]
    assert plan("x [pause 5]", paced=False, trailing=[30]) == ["x"]


def test_silences_are_capped_and_cut_into_short_clips():
    items = plan("Go. [pause 99] Again. [pause 25]")
    sil = [x for x in items if isinstance(x, float)]
    assert max(sil) <= 10.0 and abs(sum(sil) - 85) < 0.5 and all(isinstance(x, str) or x > 0 for x in items)
    many = plan(" ".join(f"Go {i}. [pause 30]" for i in range(10)))
    assert sum(x for x in many if isinstance(x, float)) <= 120.0
    assert len(plan(" ".join(["A."] * 100))) <= 24


def test_all_silence_and_empty_text_speaks_nothing():
    items = plan("[pause 5]")
    assert not any(isinstance(x, str) for x in items)


# ---------------------------------------------------------------- through the real pipeline
def test_paced_reply_plays_sentences_and_real_silence(rig, clock, device):
    r = rig(responder=ScriptedResponder([Reply("In. [pause 3] Out. [pause 2]", "calm", paced=True, pauses=(2,))]))
    r.utter()
    r.run(lambda: r.pipe.state is State.IDLE and r.pipe.stats["turns"] == 1 and device.playback is None, limit=6000)
    assert [t for t, _ in r.tts.calls] == ["In.", "Out."]                  # silences never reach the synthesizer
    fin = list(r.client.playback_results.values())
    assert len(fin) == 5 and all(f["result"] == "completed" for f in fin)
    silent = sum(f["bytes_played"] for f in fin) - sum(len(r.tts.synthesize(t)) for t in ("In.", "Out."))
    assert abs(silent / 32000 - 7.0) < 0.2                                   # 3 + 2 + trailing 2 seconds


def test_unpaced_reply_with_markers_is_just_speech(rig):
    r = rig(responder=ScriptedResponder([Reply("In. [pause 30] Out.", "calm")]))
    r.utter()
    r.idle()
    assert [t for t, _ in r.tts.calls] == ["In.", "Out."] and len(r.client.playback_results) == 2


def test_button_interrupts_a_silence(rig, device):
    r = rig(responder=ScriptedResponder([Reply("In. [pause 20] Out.", "calm", paced=True)]))
    r.utter()
    r.run(lambda: r.pipe.state is State.SPEAKING)
    r.run(lambda: len(r.client.playback_results) >= 1 and device.playback is not None and len(r.tts.calls) == 1)
    r.press()
    r.idle()
    assert r.pipe.stats["interrupted"] == 1 and [t for t, _ in r.tts.calls] == ["In."]


# ---------------------------------------------------------------- who is allowed to ask for silence
def test_model_replies_are_never_paced(tmp_path, clock):
    import random
    from companion.conversation.dialogue import LinePicker, load_templates
    from companion.personality import config as pc
    from companion.tamagotchi import CharacterState, load_rules
    cfg = pc.load(SP / "ninetales" / "personality.yaml")
    state = CharacterState(load_rules(SP / "ninetales" / "growth.yaml"), clock.t)
    llm = FakeLanguageModel(["Sure. [pause 30] Done."])
    eng = ConversationEngine(llm, lambda: cfg, state, clock, dialogue=LinePicker(load_templates(SP / "ninetales"), random.Random(1)))
    reply = eng.respond("tell me about foxes")
    assert reply.paced is False and reply.pauses == ()


def test_breathing_exercise_reply_is_paced_with_the_scripted_waits(tmp_path, clock):
    import random
    from companion.conversation.dialogue import LinePicker, load_templates
    from companion.personality import config as pc
    from companion.tamagotchi import CharacterState, load_rules
    from tests.psychology.test_integration import build
    eng, psy, mem, vault, llm, settings = build(tmp_path, clock)
    settings.acknowledge()
    eng.respond("can we do a breathing exercise")
    r = eng.respond("yes")
    assert r.paced and r.pauses == (40.0,) and "[pause 4]" in r.text
    items = plan_speech(r.text, r.paced, r.pauses, 8, 300)
    assert sum(x for x in items if isinstance(x, float)) == pytest.approx(2 + 4 + 6 + 4 + 6 + 40)
    assert llm.calls == []
