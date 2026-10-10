"""Deterministic speech providers for tests and offline development. Not real recognition or synthesis."""
from __future__ import annotations

import math
import struct
import threading

from ..interfaces import SpeechRecognizer, SpeechSynthesizer

SAMPLE_RATE = 16000
STYLE_HZ = {"normal": 220, "happy": 330, "excited": 392, "sleepy": 165, "sad": 196, "surprised": 440,
            "affectionate": 262, "calm": 196, "whisper": 150}


def tone(seconds: float, hz: float = 220, amplitude: int = 8000) -> bytes:
    n = int(seconds * SAMPLE_RATE)
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * hz * i / SAMPLE_RATE)) for i in range(n)))


def silence(seconds: float) -> bytes:
    return bytes(int(seconds * SAMPLE_RATE) * 2)


class FakeRecognizer(SpeechRecognizer):
    """Returns scripted transcripts in order (ignores the audio content, but records its size)."""

    def __init__(self, script: list[str], block: threading.Event | None = None, fail: Exception | None = None):
        self.script, self.block, self.fail = list(script), block, fail
        self.calls: list[int] = []

    def transcribe(self, pcm16_16k_mono: bytes) -> str:
        self.calls.append(len(pcm16_16k_mono))
        if self.block is not None:
            self.block.wait(30)
        if self.fail:
            raise self.fail
        return self.script.pop(0) if self.script else ""


class FakeSynthesizer(SpeechSynthesizer):
    """A tone whose length follows the text (50 ms per character) and whose pitch follows the style."""

    def __init__(self, fail: Exception | None = None, block: threading.Event | None = None, bad_output: bytes | None = None):
        self.fail, self.block, self.bad_output = fail, block, bad_output
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, style: str = "normal", speed: float = 1.0) -> bytes:
        self.calls.append((text, style))
        if self.block is not None:
            self.block.wait(30)
        if self.fail:
            raise self.fail
        if self.bad_output is not None:
            return self.bad_output
        return tone(max(0.2, 0.05 * len(text)) / max(speed, 0.5), STYLE_HZ.get(style, 220))
