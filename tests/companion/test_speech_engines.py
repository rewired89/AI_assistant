import shutil
import struct
import subprocess
from types import SimpleNamespace as NS

import pytest

from companion.speech import engines as e
from companion.speech.engines import EngineError, EspeakSynthesizer, WhisperRecognizer, parse_wav, resample, rms
from companion.speech.fakes import FakeRecognizer, silence, tone
from tests.companion.test_voice_pipeline import Reply, ScriptedResponder, rig  # noqa: F401

HAS_ESPEAK = shutil.which("espeak-ng") is not None


def wav(pcm: bytes, rate=22050, placeholder_len=True):
    n = 0x7FFFF000 if placeholder_len else len(pcm)
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", n) + pcm)


# ---------------------------------------------------------------- wav / resample
def test_parse_wav_ignores_streaming_length_and_rejects_bad_formats():
    pcm = tone(0.1, 300)
    assert parse_wav(wav(pcm)) == (22050, pcm)
    assert parse_wav(wav(pcm + b"x")) == (22050, pcm)                      # odd trailing byte dropped
    for bad in (b"", b"RIFF" + bytes(60), wav(pcm)[:30]):
        with pytest.raises(EngineError):
            parse_wav(bad)
    stereo = wav(pcm).replace(struct.pack("<HH", 1, 1), struct.pack("<HH", 1, 2), 1)
    with pytest.raises(EngineError):
        parse_wav(stereo)


def test_resample_length_and_pitch_preserved():
    import math
    src = struct.pack("<11025h", *(int(6000 * math.sin(2 * math.pi * 440 * i / 22050)) for i in range(11025)))   # 0.5 s
    out = resample(src, 22050)
    assert abs(len(out) / 2 - 8000) <= 2
    assert resample(out, 16000) == out
    assert abs(rms(out) - rms(src)) < 0.1 * rms(src)
    smp = struct.unpack(f"<{len(out) // 2}h", out)
    crossings = sum(1 for x, y in zip(smp, smp[1:]) if x < 0 <= y)
    assert 215 <= crossings <= 225                                           # ~220 cycles in 0.5 s: pitch unchanged


# ---------------------------------------------------------------- espeak adapter (stubbed process)
def runner_for(stdout=None, rc=0, exc=None, seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append((cmd, kw))
        if exc:
            raise exc
        return NS(returncode=rc, stdout=wav(tone(0.2)) if stdout is None else stdout)
    return run


def test_text_goes_over_stdin_never_into_the_command_line():
    seen = []
    t = EspeakSynthesizer(binary="espeak-ng", runner=runner_for(seen=seen))
    t.synthesize("hello; rm -rf / $(x)", "happy", 1.0)
    cmd, kw = seen[0]
    assert all("rm" not in c for c in cmd) and kw["input"] == b"hello; rm -rf / $(x)" and "shell" not in kw
    assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "65"


def test_style_and_speed_are_bounded_and_unknown_style_falls_back():
    seen = []
    t = EspeakSynthesizer(binary="espeak-ng", runner=runner_for(seen=seen))
    t.synthesize("hi", "nonsense", 99)
    t.synthesize("hi", "sleepy", 0.01)
    wpms = [int(c[c.index("-s") + 1]) for c, _ in seen]
    assert 80 <= min(wpms) and max(wpms) <= 300


@pytest.mark.parametrize("text", ["", "   ", "\x00\x01"])
def test_empty_text_is_refused(text):
    with pytest.raises(EngineError):
        EspeakSynthesizer(binary="x", runner=runner_for()).synthesize(text)


def test_long_text_is_clipped():
    seen = []
    EspeakSynthesizer(binary="x", runner=runner_for(seen=seen)).synthesize("a" * 5000)
    assert len(seen[0][1]["input"]) == e.MAX_TEXT_CHARS


@pytest.mark.parametrize("kw", [dict(rc=1), dict(stdout=b""), dict(stdout=b"garbage"), dict(exc=subprocess.TimeoutExpired("x", 1)),
                                dict(exc=FileNotFoundError())])
def test_process_failures_become_engine_errors_without_leaking_details(kw):
    with pytest.raises(EngineError) as err:
        EspeakSynthesizer(binary="x", runner=runner_for(**kw)).synthesize("hi")
    assert "/" not in str(err.value) or "WAV" in str(err.value)


def test_missing_binary_is_reported():
    with pytest.raises(EngineError):
        EspeakSynthesizer(binary="definitely-not-installed-xyz")


# ---------------------------------------------------------------- espeak for real
@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak-ng not installed")
def test_real_espeak_produces_valid_16k_speech_with_style_differences():
    t = EspeakSynthesizer()
    normal = t.synthesize("Hello, I am Nine Tails.")
    sleepy = t.synthesize("Hello, I am Nine Tails.", "sleepy")
    fast = t.synthesize("Hello, I am Nine Tails.", "normal", 1.5)
    assert len(normal) % 2 == 0 and 0.8 < len(normal) / 32000 < 5 and rms(normal) > 500
    assert len(sleepy) > len(normal) > len(fast)
    assert t.synthesize("¿Qué tal? 日本語 ok")                              # odd input must not crash


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak-ng not installed")
def test_real_tts_through_the_voice_pipeline_and_simulated_speaker(rig, device):
    r = rig(stt=FakeRecognizer(["hi"]), tts=EspeakSynthesizer(),
            responder=ScriptedResponder([Reply("Hello there. I am here.", "happy")]))
    r.utter()
    r.idle()
    fin = list(r.client.playback_results.values())
    assert len(fin) == 2 and all(f["result"] == "completed" and f["underruns"] == 0 for f in fin)
    assert sum(f["bytes_played"] for f in fin) > 32000


# ---------------------------------------------------------------- whisper adapter (stub model)
class StubModel:
    def __init__(self, segments):
        self.segments, self.calls = segments, []

    def transcribe(self, audio, **kw):
        self.calls.append((len(audio), kw))
        return iter(self.segments), NS()


def seg(text, nsp=0.0, lp=-0.2):
    return NS(text=text, no_speech_prob=nsp, avg_logprob=lp)


def stt_with(segments):
    m = StubModel(segments)
    return WhisperRecognizer("any", loader=lambda path: m), m


def speech(seconds=1.0):
    return tone(seconds, 300, 6000)


def test_whisper_joins_segments_and_never_downloads_or_vads():
    r, m = stt_with([seg(" What is"), seg(" your name? ")])
    assert r.transcribe(speech()) == "What is your name?"
    assert m.calls[0][1]["vad_filter"] is False and m.calls[0][1]["condition_on_previous_text"] is False
    assert m.calls[0][0] == 16000


def test_whisper_skips_silence_and_noise_without_calling_the_model():
    r, m = stt_with([seg("Thank you.")])
    assert r.transcribe(b"") == "" and r.transcribe(silence(2)) == "" and r.transcribe(speech(0.05)) == ""
    assert r.transcribe(tone(1, 300, 40)) == "" and m.calls == []


def test_whisper_drops_known_hallucinations_on_short_clips_only():
    r, _ = stt_with([seg("Thank you.")])
    assert r.transcribe(speech(1.0)) == ""
    r, _ = stt_with([seg("Thank you.")])
    assert r.transcribe(speech(3.0)) == "Thank you."                       # a real, longer "thank you" is kept


def test_whisper_drops_confident_no_speech_segments():
    r, _ = stt_with([seg("blah", nsp=0.9, lp=-1.5), seg("keep me")])
    assert r.transcribe(speech()) == "keep me"


def test_whisper_load_failures_are_clear_and_do_not_leak_paths():
    def boom(path):
        raise OSError("/secret/path/model.bin missing")
    with pytest.raises(EngineError) as err:
        WhisperRecognizer("models/x", loader=boom)
    assert "/secret" not in str(err.value) and "downloaded" in str(err.value)


def test_whisper_local_files_only_is_set_when_using_the_real_library(monkeypatch):
    fw = pytest.importorskip("faster_whisper")
    seen = {}

    class Spy:
        def __init__(self, m, **kw):
            seen.update(kw)
    monkeypatch.setattr(fw, "WhisperModel", Spy)
    WhisperRecognizer("models/x")
    assert seen["local_files_only"] is True
