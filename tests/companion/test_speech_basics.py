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
