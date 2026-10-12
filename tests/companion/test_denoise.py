import numpy as np
import pytest

from companion.speech.denoise import Denoiser

SR = 16000
LAG = 512


def _fan(n, seed=1):
    r = np.random.default_rng(seed)
    noise = np.convolve(r.normal(0, 1, n), np.ones(8) / 8, mode="same") * 1500      # low-pass hiss
    hum = 1200 * np.sin(2 * np.pi * 120 * np.arange(n) / SR)
    return noise + hum


def _speech(n):
    t = np.arange(n) / SR
    env = (np.sin(2 * np.pi * 3 * t) > -0.2).astype(float)                            # syllable-like bursts
    return env * sum(3000 / k * np.sin(2 * np.pi * 180 * k * t) for k in range(1, 12))


def _pcm(x):
    return np.clip(x, -32768, 32767).astype("<i2").tobytes()


def _run(x, chunk=3200):
    d, out, b = Denoiser(), b"", _pcm(x)
    for i in range(0, len(b), chunk):
        out += d.process(b[i:i + chunk])
    return np.frombuffer(out, dtype="<i2").astype(float)


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


def test_steady_noise_is_removed():
    y = _run(_fan(SR * 4))
    assert len(y) == SR * 4
    assert _rms(y[SR * 2:]) < _rms(_fan(SR * 4)[SR * 2:]) * 0.25          # at least 12 dB quieter


def test_speech_survives_and_stands_out_from_the_fan():
    s, f = _speech(SR * 4), _fan(SR * 4)
    y = _run(s + f)
    y = np.concatenate([y[LAG:], np.zeros(LAG)])                              # undo the 32 ms delay
    clean_ref = _run(f)
    t = np.arange(SR * 4) / SR
    on = (np.sin(2 * np.pi * 3 * t) > -0.2)[SR * 2:]
    gap = (np.sin(2 * np.pi * 3 * t) < -0.35)[SR * 2:]                       # clear of the burst edges
    loud_in = _rms((s + f)[SR * 2:][np.abs(s[SR * 2:]) > 500])
    loud_out = _rms(y[SR * 2:][np.abs(s[SR * 2:]) > 500])
    assert loud_out > loud_in * 0.5                                         # speech keeps most of its level
    assert _rms(y[SR * 2:][gap]) < _rms((s + f)[SR * 2:][gap]) * 0.3        # gaps between words are much quieter
    assert _rms(clean_ref[SR * 2:]) < 600


def test_chunk_size_does_not_change_the_result():
    x = _speech(SR * 2) + _fan(SR * 2)
    a, b = _run(x, 3200), _run(x, 1000)
    assert np.allclose(a, b, atol=2)


def test_disabled_and_odd_sizes_pass_through():
    d = Denoiser(enabled=False)
    assert d.process(b"\x01\x02\x03\x04") == b"\x01\x02\x03\x04"
    d2 = Denoiser()
    assert d2.process(b"") == b""
    assert len(d2.process(b"\x00" * 3201)) == 3201
