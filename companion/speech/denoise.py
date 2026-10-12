"""Steady background noise removal (a fan, an air conditioner, a hum) for the microphone stream.

Spectral gating: per frequency band it keeps a running estimate of the noise floor (it falls fast when things get quiet
and rises very slowly, so speech does not raise it) and turns down the bands that are not clearly louder than that
floor. Speech stays, the fan goes. It works on 16 kHz PCM16 mono, streams in any chunk size, and adds about 32 ms delay.

Honest limits: it only handles STEADY noise. A door slam, music or another person talking is not removed. It can make
very quiet speech slightly thinner. Needs numpy (already installed with the speech extras); without it audio passes
through unchanged. Used on the desk robot's laptop mic only: the real board has its own mic hardware (ES7210), whose
behaviour is UNVERIFIED."""
from __future__ import annotations

try:
    import numpy as np
except ImportError:                                            # pragma: no cover
    np = None

FRAME, HOP = 512, 256
WARMUP_FRAMES = 20                    # ~0.3 s at the start is assumed to be background only
OVER = 2.0                            # how hard to subtract (higher = more removal, more risk to speech)
FLOOR = 0.12                          # never turn a band down by more than ~18 dB (avoids "underwater" artifacts)
RISE = 1.003                          # noise estimate may climb 0.3% per frame while sound is above it
HIGHPASS_BINS = 3                     # bins below ~90 Hz are rumble


class Denoiser:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled and np is not None
        self._win = np.sqrt(np.hanning(FRAME + 1)[:-1]) if np is not None else None
        self._in = b""
        self._tail = None
        self._noise = None
        self._frames = 0
        self._gain = None
        self._smooth = None
        self._out = bytearray(FRAME * 2)                        # priming: output trails input by ~32 ms so a call is never short
        self._backlog = bytearray()

    def process(self, pcm: bytes) -> bytes:
        """Returns the same number of bytes it was given (a few ms later in time)."""
        if not self.enabled or not pcm:
            return pcm
        n_in = len(pcm) - len(pcm) % 2
        self._in += pcm[:n_in]
        x = np.frombuffer(self._in, dtype="<i2").astype(np.float32)
        used = 0
        while used + FRAME <= len(x):
            self._backlog += self._frame(x[used:used + FRAME])
            used += HOP
        self._in = self._in[used * 2:]
        self._out += self._backlog
        self._backlog = bytearray()
        res = bytes(self._out[:n_in]).ljust(n_in, b"\0")
        del self._out[:n_in]
        return res + pcm[n_in:]

    def _frame(self, x):
        spec = np.fft.rfft(x * self._win)
        p = (spec.real ** 2 + spec.imag ** 2) + 1e-3
        if self._noise is None:
            self._noise = p.copy()
            self._gain = np.ones_like(p)
        self._frames += 1
        self._smooth = p.copy() if self._smooth is None else 0.7 * self._smooth + 0.3 * p     # steadier than one frame
        ps = self._smooth
        if self._frames <= WARMUP_FRAMES:
            self._noise += (ps - self._noise) / self._frames   # plain average while warming up
        else:
            self._noise = np.where(ps < self._noise, 0.8 * self._noise + 0.2 * ps, self._noise * RISE)
        g = np.maximum(FLOOR, 1.0 - OVER * self._noise / ps)
        g = np.convolve(g, np.ones(3) / 3, mode="same")                  # neighbouring bands move together
        g = np.where(g > self._gain, g, 0.5 * g + 0.5 * self._gain)   # open at once, close gently
        g[:HIGHPASS_BINS] = FLOOR
        self._gain = g
        y = np.fft.irfft(spec * g) * self._win
        if self._tail is None:
            self._tail = np.zeros(HOP, dtype=np.float32)
        out = self._tail + y[:HOP]
        self._tail = y[HOP:].astype(np.float32)
        return np.clip(out, -32768, 32767).astype("<i2").tobytes()


class DenoisedMic:
    """Wraps any mic object (read/status/close) so everything downstream hears the cleaned audio."""

    def __init__(self, mic, denoiser: Denoiser | None = None):
        self.mic, self.dn = mic, denoiser or Denoiser()

    def read(self) -> bytes:
        return self.dn.process(self.mic.read())

    def status(self) -> str:
        return self.mic.status()

    def close(self) -> None:
        self.mic.close()
