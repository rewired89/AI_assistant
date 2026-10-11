"""Measure the local Whisper model on this machine: load time, speed, memory and what it hears.

    python -m tools.bench_stt --model models/whisper-small.en                    # speaks a test sentence with espeak-ng, then transcribes it
    python -m tools.bench_stt --model models/whisper-small.en --wav me.wav       # your own recording (any 16-bit mono WAV)
    python -m tools.bench_stt --model models/whisper-small.en --record 6         # records 6 s from the microphone (needs arecord)

Real-time factor (RTF) = processing time / audio length. Under 1.0 means faster than real time; push-to-talk replies
feel OK below about 0.5 for short sentences."""
from __future__ import annotations

import argparse
import resource
import subprocess
import sys
import time
from pathlib import Path

from companion.speech.engines import EngineError, EspeakSynthesizer, WhisperRecognizer, parse_wav, resample, rms

SAMPLE = "Hello, my name is Sam. Please tell me something nice about foxes."


def _load_wav(path: str) -> bytes:
    rate, pcm = parse_wav(Path(path).read_bytes())
    return resample(pcm, rate)


def _record(seconds: int) -> bytes:
    out = Path("/tmp/nt_bench.wav")
    subprocess.run(["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), str(out)], check=True)
    return _load_wav(str(out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--wav")
    ap.add_argument("--record", type=int)
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args(argv)
    try:
        if a.wav:
            pcm, source = _load_wav(a.wav), a.wav
        elif a.record:
            print(f"recording {a.record} s, speak now...")
            pcm, source = _record(a.record), "microphone"
        else:
            pcm, source = EspeakSynthesizer().synthesize(SAMPLE), f"espeak-ng saying: {SAMPLE!r}"
        t0 = time.time()
        stt = WhisperRecognizer(a.model)
        load = time.time() - t0
    except (EngineError, OSError, subprocess.CalledProcessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    secs = len(pcm) / 32000
    print(f"input: {source}\naudio: {secs:.1f} s, loudness (RMS) {rms(pcm):.0f}\nmodel load: {load:.1f} s")
    times, text = [], ""
    for i in range(a.repeat):
        t = time.time()
        text = stt.transcribe(pcm)
        times.append(time.time() - t)
        print(f"run {i + 1}: {times[-1]:.1f} s  (RTF {times[-1] / secs:.2f})")
    print(f"heard: {text!r}")
    print(f"peak memory: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
