import pytest
from companion.speech import EndpointDetector, clean_transcript, load_lines, rms, split_sentences
from companion.speech.fakes import silence, tone
from pathlib import Path

SP = Path(__file__).parents[2] / "species"


def test_rms_and_silence():
    assert rms(b"") == 0 and rms(silence(0.1)) == 0
    assert 5000 < rms(tone(0.1, amplitude=8000)) < 6000          # 8000 / sqrt(2)
    assert rms(b"\x01") == 0                                      # odd trailing byte ignored


def test_endpoint_needs_speech_then_trailing_silence():
    d = EndpointDetector(threshold=500, min_speech_s=0.3, trailing_silence_s=1.0)
    d.feed(silence(2.0))
    assert not d.has_speech and not d.endpoint                    # leading silence never ends a turn
    d.feed(tone(0.2))
    d.feed(silence(1.5))
    assert d.has_speech and not d.enough_speech and not d.endpoint   # a click is not an utterance
    d = EndpointDetector()
    d.feed(tone(0.6)); d.feed(silence(0.5))
    assert d.enough_speech and not d.endpoint
    d.feed(silence(0.6))
    assert d.endpoint
    d.feed(tone(0.1))
    assert not d.endpoint                                         # speech resumed: silence counter resets


def test_detector_handles_odd_chunk_sizes():
    d = EndpointDetector()
    pcm = tone(0.6) + silence(1.2)
    for i in range(0, len(pcm), 777):
        d.feed(pcm[i:i + 777])
    e = EndpointDetector(); e.feed(pcm)
    assert d.endpoint and e.endpoint and abs(d.speech_s - e.speech_s) < 0.03


def test_quiet_noise_below_threshold_is_not_speech():
    d = EndpointDetector(threshold=500)
    d.feed(tone(2.0, amplitude=300))
    assert not d.has_speech


@pytest.mark.parametrize("raw,expected", [
    ("  hello   there ", "hello there"), ("hi\x00\x07 there‮", "hi there"), ("...", ""), ("", ""),
    ("a" * 900, "a" * 500), ("line1\nline2", "line1 line2")])
def test_clean_transcript(raw, expected):
    assert clean_transcript(raw, 500) == expected


def test_split_sentences():
    assert split_sentences("Hi there. How are you? Fine!", 8, 300) == ["Hi there.", "How are you?", "Fine!"]
    assert split_sentences("One. Two. Three.", 2, 300) == ["One.", "Two."]
    long = " ".join(["word"] * 200)
    parts = split_sentences(long, 8, 100)
    assert all(len(p) <= 100 for p in parts) and " ".join(parts) == long[: len(" ".join(parts))]
    assert split_sentences("   ", 8, 100) == []


def test_species_lines_override_and_validate(tmp_path):
    assert "ears" in load_lines(SP / "ninetales")["didnt_hear"]
    assert load_lines(SP / "cubone")["didnt_hear"] == "Did not catch that."
    assert load_lines(SP / "cubone")["battery_low"]                # unspecified lines fall back
    bad = tmp_path / "speech.yaml"
    bad.write_text("lines:\n  made_up: hello\n")
    with pytest.raises(ValueError):
        load_lines(tmp_path)


# ---------------------------------------------------------------- adaptive endpointing (hot mic / noisy room)
def _noise(level, seconds, seed=1):
    import random
    import struct
    r = random.Random(seed)
    n = int(seconds * 16000)
    return struct.pack(f"<{n}h", *(int(r.gauss(0, level)) for _ in range(n)))


def test_endpoint_works_in_a_noisy_room_where_a_fixed_threshold_never_ends():
    from companion.speech.vad import EndpointDetector
    from companion.speech.fakes import tone
    det = EndpointDetector()                                  # base threshold 500
    det.feed(_noise(1500, 0.6))                               # TV / hot mic: constant noise far above 500
    for _ in range(3):
        det.feed(tone(0.3, 300, 20000))                       # she talks, much louder than the noise
    det.feed(_noise(1500, 1.4))                               # she stops; the noise stays
    assert det.enough_speech and det.endpoint
    fixed = EndpointDetector(threshold=500.0)
    fixed.noise_floor = 0.0
    assert det.current_threshold > 1500                       # learned the room


def test_endpoint_still_hears_quiet_speech_in_a_quiet_room():
    from companion.speech.vad import EndpointDetector
    from companion.speech.fakes import tone
    det = EndpointDetector()
    det.feed(_noise(50, 0.5))
    det.feed(tone(0.8, 300, 4000))
    det.feed(_noise(50, 1.3))
    assert det.endpoint and det.current_threshold == 500.0


def test_continuous_talking_is_not_mistaken_for_noise():
    from companion.speech.vad import EndpointDetector
    from companion.speech.fakes import tone, silence
    det = EndpointDetector()
    for _ in range(6):
        det.feed(tone(0.35, 300, 9000))
        det.feed(silence(0.08))                               # tiny gaps between words
    assert det.has_speech and det.speech_s > 1.5 and not det.endpoint
