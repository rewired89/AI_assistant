"""Real, local speech engines behind the SpeechRecognizer / SpeechSynthesizer interfaces. Nothing here uses the network
at run time: audio and text never leave the machine.

STT: faster-whisper (optional dependency, `pip install faster-whisper`). The model must already be on disk (see
`python -m tools.fetch_models`); `local_files_only` is on so a missing model fails loudly instead of downloading.
TTS: espeak-ng as a subprocess (text goes in on stdin, never through a shell). It is intelligible but clearly robotic,
which suits a small robot; a nicer voice (e.g. Piper) can replace it behind the same interface. espeak-ng is GPLv3:
running it as a separate program is fine for a personal build, check licensing before selling hardware with it."""
from __future__ import annotations

import array
import re
import shutil
import struct
import subprocess
import zlib

from ..interfaces import SpeechRecognizer, SpeechSynthesizer

SAMPLE_RATE = 16000
MAX_TEXT_CHARS = 600
MIN_RMS = 120                          # below this, "speech" is room noise; Whisper would invent words for it
# Phrases Whisper is known to produce from near-silence; dropped when the clip is short or the model says no-speech.
HALLUCINATIONS = frozenset({"thank you", "thanks for watching", "you", "bye", "thank you for watching", "okay"})


_RUN = re.compile(r"(.)\1{7,}")                                   # "Ahhhhhhhh"
_REPEAT_WORD = re.compile(r"\b(\w+)(?:[\s,.!?-]+\1\b){4,}", re.I)      # "no no no no no"


def looks_degenerate(text: str, seconds: float) -> bool:
    """Whisper sometimes loops on noise or silence ("Ahhhhhh...", the same word forever) instead of staying quiet.
    Such output is never a real sentence, so it is thrown away rather than shown to the companion."""
    if not text:
        return False
    if _RUN.search(text) or _REPEAT_WORD.search(text):
        return True
    if len(text) >= 40 and len(text.encode()) / max(1, len(zlib.compress(text.encode()))) > 2.4:
        return True
    return len(text.split()) / max(seconds, 0.5) > 6.5               # more words per second than anyone speaks


class EngineError(Exception):
    pass


def rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    s = struct.unpack(f"<{n}h", pcm[:n * 2])
    return (sum(x * x for x in s) / n) ** 0.5


class WhisperRecognizer(SpeechRecognizer):
    """`model` is a directory (or size name already in the local cache). `loader` exists so tests can inject a stub."""

    def __init__(self, model: str, language: str | None = "en", device: str = "cpu", compute_type: str = "int8",
                 beam_size: int = 1, loader=None):
        if loader is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise EngineError("faster-whisper is not installed: pip install faster-whisper") from e

            def loader(m, **kw):
                return WhisperModel(m, device=device, compute_type=compute_type, local_files_only=True)
        try:
            self._model = loader(model)
        except Exception as e:
            raise EngineError(f"could not load the speech model {model!r} (is it downloaded?): {type(e).__name__}") from None
        self.language, self.beam_size = language, beam_size

    def transcribe(self, pcm16_16k_mono: bytes) -> str:
        n = len(pcm16_16k_mono) // 2
        if n < SAMPLE_RATE // 5 or rms(pcm16_16k_mono) < MIN_RMS:
            return ""
        samples = struct.unpack(f"<{n}h", pcm16_16k_mono[:n * 2])
        try:
            import numpy as np
            audio = np.asarray(samples, dtype=np.float32) / 32768.0
        except ImportError:
            audio = [x / 32768.0 for x in samples]
        seconds = n / SAMPLE_RATE
        segments, _info = self._model.transcribe(
            audio, language=self.language, beam_size=self.beam_size, condition_on_previous_text=False, vad_filter=False,
            no_speech_threshold=0.6, log_prob_threshold=-1.0, compression_ratio_threshold=2.4,
            max_new_tokens=int(8 * seconds) + 24)                  # a runaway loop cannot run for ever (was 24 s once)
        short = n < SAMPLE_RATE * 2
        parts = []
        for s in segments:
            if getattr(s, "no_speech_prob", 0.0) > 0.6 and getattr(s, "avg_logprob", 0.0) < -1.0:
                continue
            parts.append(s.text.strip())
        text = " ".join(p for p in parts if p).strip()
        if short and text.lower().strip(" .!?,") in HALLUCINATIONS:
            return ""
        if looks_degenerate(text, seconds):
            return ""
        return text


# style -> (espeak pitch 0-99, words per minute, amplitude 0-200). Rough hand-tuned guesses, not measured.
ESPEAK_STYLES = {
    "normal": (55, 160, 100), "happy": (65, 175, 110), "excited": (72, 190, 120), "sleepy": (35, 125, 70),
    "sad": (40, 130, 80), "surprised": (75, 170, 115), "affectionate": (58, 145, 85), "calm": (48, 140, 85),
    "whisper": (45, 135, 60),
}


def parse_wav(data: bytes) -> tuple[int, bytes]:
    """Returns (sample_rate, pcm16 mono). espeak writes streaming WAVs whose data length is a placeholder, so the
    length field is ignored and everything after the data header is taken."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise EngineError("synthesizer did not return a WAV")
    pos, rate, ch, bits = 12, 0, 0, 0
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            _fmt, ch, rate, _br, _ba, bits = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
        elif cid == b"data":
            if (ch, bits) != (1, 16) or rate <= 0:
                raise EngineError("unexpected WAV format (need 16-bit mono)")
            pcm = data[pos + 8:]
            return rate, pcm[:len(pcm) // 2 * 2]
        pos += 8 + size + (size & 1)
    raise EngineError("WAV has no audio")


def resample(pcm: bytes, src: int, dst: int = SAMPLE_RATE) -> bytes:
    """Linear interpolation. Good enough for speech from a synthesizer; not a mastering-grade resampler."""
    if src == dst:
        return pcm
    a = array.array("h")
    a.frombytes(pcm)
    n_out = len(a) * dst // src
    out = array.array("h", bytes(2 * n_out))
    step = src / dst
    last = len(a) - 1
    for i in range(n_out):
        p = i * step
        j = int(p)
        f = p - j
        out[i] = int(a[j] + (a[min(j + 1, last)] - a[j]) * f) if j < last else a[last]
    return out.tobytes()


class EspeakSynthesizer(SpeechSynthesizer):
    def __init__(self, voice: str = "en-us", binary: str = "espeak-ng", timeout_s: float = 15.0, runner=None):
        self.binary = shutil.which(binary) if runner is None else binary
        if not self.binary:
            raise EngineError("espeak-ng is not installed (Windows: install from the espeak-ng releases page)")
        self.voice, self.timeout_s, self._run = voice, timeout_s, runner or subprocess.run

    def synthesize(self, text: str, style: str = "normal", speed: float = 1.0) -> bytes:
        text = "".join(ch for ch in text if ch.isprintable() or ch in "\n ").strip()[:MAX_TEXT_CHARS]
        if not text:
            raise EngineError("nothing to say")
        pitch, wpm, amp = ESPEAK_STYLES.get(style, ESPEAK_STYLES["normal"])
        wpm = int(max(80, min(300, wpm * max(0.5, min(speed, 2.0)))))
        cmd = [self.binary, "--stdout", "-v", self.voice, "-p", str(pitch), "-s", str(wpm), "-a", str(amp), "--stdin"]
        try:
            r = self._run(cmd, input=text.encode(), capture_output=True, timeout=self.timeout_s, check=False)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise EngineError(f"speech synthesis failed: {type(e).__name__}") from None
        if r.returncode != 0 or not r.stdout:
            raise EngineError("speech synthesis failed")
        rate, pcm = parse_wav(r.stdout)
        return resample(pcm, rate)
