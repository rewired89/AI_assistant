"""Energy-based end-of-speech detection for push-to-talk captures (PCM16 LE, 16 kHz mono).

Deliberately simple. The threshold is a guess until it is calibrated against the real microphone
(noise floor, gain and AGC are unknown), so it is configuration, not a constant of nature.
"""
from __future__ import annotations

import math
import struct

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 320  # 20 ms


def rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return math.sqrt(sum(x * x for x in samples) / n)


class EndpointDetector:
    def __init__(self, threshold: float = 500.0, min_speech_s: float = 0.3, trailing_silence_s: float = 1.0):
        self.threshold, self.min_speech_s, self.trailing_silence_s = threshold, min_speech_s, trailing_silence_s
        self.speech_s = 0.0
        self.trailing_s = 0.0
        self.has_speech = False
        self._carry = b""

    def feed(self, pcm: bytes) -> None:
        data = self._carry + pcm
        step = WINDOW_SAMPLES * 2
        usable = len(data) // step * step
        for i in range(0, usable, step):
            if rms(data[i:i + step]) >= self.threshold:
                self.speech_s += WINDOW_SAMPLES / SAMPLE_RATE
                self.trailing_s = 0.0
                self.has_speech = True
            elif self.has_speech:
                self.trailing_s += WINDOW_SAMPLES / SAMPLE_RATE
        self._carry = data[usable:]

    @property
    def enough_speech(self) -> bool:
        return self.speech_s >= self.min_speech_s

    @property
    def endpoint(self) -> bool:
        """Speech happened, was long enough, and has been followed by enough silence."""
        return self.has_speech and self.enough_speech and self.trailing_s >= self.trailing_silence_s
