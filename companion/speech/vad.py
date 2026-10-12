"""Energy-based end-of-speech detection for push-to-talk captures (PCM16 LE, 16 kHz mono).

Deliberately simple. `threshold` is the floor for "this is speech"; on top of it the detector learns the room: the
quietest 10% of the windows heard so far in this capture approximate the background noise, and speech must be 3x
louder than that (capped), so a hot microphone or a TV no longer keeps "silence" from ever being detected (the
fixed 500 threshold made one real laptop mic listen until the 15 s limit). Still a guess until calibrated on the
real hardware microphone."""
from __future__ import annotations

import math
import struct

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 320  # 20 ms
MAX_ADAPTIVE_THRESHOLD = 6000.0


def rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    mean = sum(samples) / n                    # some mics sit on a constant offset; that is not sound
    return math.sqrt(sum((x - mean) ** 2 for x in samples) / n)


class EndpointDetector:
    def __init__(self, threshold: float = 500.0, min_speech_s: float = 0.3, trailing_silence_s: float = 1.0):
        self.threshold, self.min_speech_s, self.trailing_silence_s = threshold, min_speech_s, trailing_silence_s
        self.speech_s = 0.0
        self.trailing_s = 0.0
        self.has_speech = False
        self._carry = b""
        self._levels: list[float] = []
        self.noise_floor = 0.0

    def feed(self, pcm: bytes) -> None:
        data = self._carry + pcm
        step = WINDOW_SAMPLES * 2
        usable = len(data) // step * step
        for i in range(0, usable, step):
            level = rms(data[i:i + step])
            self._levels.append(level)
            if len(self._levels) >= 15:                        # 0.3 s of audio heard
                quiet = sorted(self._levels)[len(self._levels) // 10]
                # Only trust it as "noise" when something clearly louder has also been heard; if everything so far is
                # about equally loud we cannot tell noise from speech and stay on the fixed threshold.
                self.noise_floor = quiet if quiet * 2.5 <= max(self._levels) else 0.0
            if level >= self.current_threshold:
                self.speech_s += WINDOW_SAMPLES / SAMPLE_RATE
                self.trailing_s = 0.0
                self.has_speech = True
            elif self.has_speech:
                self.trailing_s += WINDOW_SAMPLES / SAMPLE_RATE
        self._carry = data[usable:]

    @property
    def current_threshold(self) -> float:
        return max(self.threshold, min(3.0 * self.noise_floor, MAX_ADAPTIVE_THRESHOLD))

    @property
    def enough_speech(self) -> bool:
        return self.speech_s >= self.min_speech_s

    @property
    def endpoint(self) -> bool:
        """Speech happened, was long enough, and has been followed by enough silence."""
        return self.has_speech and self.enough_speech and self.trailing_s >= self.trailing_silence_s
